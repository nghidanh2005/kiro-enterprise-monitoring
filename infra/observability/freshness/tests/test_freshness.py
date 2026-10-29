"""Tests for the freshness Lambda (task 9.2). Offline, moto for S3/Logs/CloudWatch/SNS."""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lambda"))

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

import freshness as fr  # noqa: E402

ACCT = "123456789012"
NOW = dt.datetime(2026, 10, 29, 5, 0, tzinfo=dt.timezone.utc)
DC, CUR, RESULTS, TRAIL, META, REPORT = (
    "dc-bucket", "cur-bucket", "sample-results", "trail-bucket", "meta-bucket", "customer-report")
FN = "CID-DC-kiro-user-activity-Lambda"


def cfg(**customer_over):
    c = {"slug": "acme", "kiro_account": ACCT, "report_bucket": REPORT,
         "sample_results_bucket": RESULTS, "trail": None, "promptmeta_bucket": None, "cur": None}
    c.update(customer_over)
    return {"dc_bucket": DC, "collector_function": FN, "cur_bucket": CUR, "customers": [c]}


class Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_DEFAULT_REGION": "ap-southeast-1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.aws = mock_aws()
        self.aws.start()
        self.addCleanup(self.aws.stop)
        self.s3 = boto3.client("s3")
        for b in (DC, CUR, RESULTS, TRAIL, META):
            self.s3.create_bucket(Bucket=b, CreateBucketConfiguration={"LocationConstraint": "ap-southeast-1"})
        self.logs = boto3.client("logs")
        self.cw = boto3.client("cloudwatch")

    def put(self, bucket, key):
        self.s3.put_object(Bucket=bucket, Key=key, Body=b"x")

    def data_on(self, day: dt.date):
        self.put(DC, f"kiro-user-activity/account_id={ACCT}/region=us-east-1/year={day:%Y}/month={day:%m}/day={day:%d}/a.csv")
        self.put(RESULTS, f"normalized/facts/export_date={day:%Y-%m-%d}/part-1.csv")

    def collector_log(self, result: dict):
        g = f"/aws/lambda/{FN}"
        try:
            self.logs.create_log_group(logGroupName=g)
            self.logs.create_log_stream(logGroupName=g, logStreamName="s")
        except self.logs.exceptions.ResourceAlreadyExistsException:
            pass
        self.logs.put_log_events(logGroupName=g, logStreamName="s", logEvents=[
            {"timestamp": int((NOW - dt.timedelta(hours=2)).timestamp() * 1000), "message": json.dumps(result)}])

    def issues(self, c=None):
        return fr.run_checks(c or cfg(), NOW, self.s3, self.logs, self.cw)

    def layers(self, issues):
        return sorted((i["customer"], i["layer"], i["issue"]) for i in issues)


class TestReportLag(Base):
    def test_lag_of_exactly_two_days_is_silent(self):
        self.data_on(NOW.date() - dt.timedelta(days=2))
        self.collector_log({"statusCode": 200, "total_files_copied": 1, "errors": []})
        self.assertEqual(self.issues(), [])

    def test_lag_of_three_days_alerts_both_report_layers(self):
        self.data_on(NOW.date() - dt.timedelta(days=3))
        self.collector_log({"statusCode": 200, "total_files_copied": 0, "errors": []})
        got = self.issues()
        self.assertEqual(self.layers(got), [("acme", "report via CID", "late"), ("acme", "report via sample", "late")])
        self.assertTrue(all(i["lag_days"] == 3 for i in got))

    def test_latest_date_is_max_across_regions_and_months(self):
        for k in ("region=us-east-1/year=2026/month=09/day=30", "region=eu-central-1/year=2026/month=10/day=28",
                  "region=us-east-1/year=2025/month=12/day=31"):
            self.put(DC, f"kiro-user-activity/account_id={ACCT}/{k}/a.csv")
        self.assertEqual(fr.latest_cid_date(self.s3, DC, ACCT), dt.date(2026, 10, 28))

    def test_no_data_at_all_alerts(self):
        self.collector_log({"statusCode": 200, "total_files_copied": 0, "errors": []})
        self.assertIn(("acme", "report via CID", "no data"), self.layers(self.issues()))


class TestCollector(Base):
    def setUp(self):
        super().setUp()
        self.data_on(NOW.date() - dt.timedelta(days=1))

    def test_errors_in_result_line_alert_even_with_status_200(self):
        self.collector_log({"statusCode": 200, "total_files_copied": 0, "errors": [
            f"Error listing {REPORT}: An error occurred (AccessDenied) when calling the ListObjectsV2 operation",
            f"other-bucket/x.csv: boom"]})
        got = [i for i in self.issues() if i["layer"] == "collector"]
        self.assertEqual(got, [{"customer": "acme", "layer": "collector", "issue": "copy errors",
                                "count": 1, "access_denied": 1}])

    def test_no_result_line_in_24h_alerts(self):
        self.assertIn(("*", "collector", "no run logged in 24 h"), self.layers(self.issues()))


class TestTrailAndCur(Base):
    def setUp(self):
        super().setUp()
        self.data_on(NOW.date() - dt.timedelta(days=1))
        self.collector_log({"statusCode": 200, "total_files_copied": 1, "errors": []})

    def test_trail_with_no_object_today_or_yesterday_alerts(self):
        self.put(TRAIL, f"AWSLogs/{ACCT}/CloudTrail/us-east-1/2026/10/20/a.json.gz")
        c = cfg(trail={"bucket": TRAIL, "region": "us-east-1"})
        self.assertIn(("acme", "cloudtrail", "no log file in 24 h"), self.layers(self.issues(c)))

    def test_trail_with_fresh_object_is_silent(self):
        self.put(TRAIL, f"AWSLogs/{ACCT}/CloudTrail/us-east-1/{NOW:%Y/%m/%d}/a.json.gz")
        c = cfg(trail={"bucket": TRAIL, "region": "us-east-1"})
        # moto stamps LastModified with the real clock, so compare against the real now.
        got = fr.check_trail(self.s3, "acme", ACCT, c["customers"][0]["trail"],
                             dt.datetime.now(dt.timezone.utc))
        self.assertIsNone(got)

    def test_cur_silent_inside_first_72h_then_alerts(self):
        prefix = f"cur2/{ACCT}/cid-cur2/data/"
        young = cfg(cur={"prefix": prefix, "created": (NOW.date() - dt.timedelta(days=2)).isoformat()})
        self.assertNotIn("cur", [i["layer"] for i in self.issues(young)])
        old = cfg(cur={"prefix": prefix, "created": (NOW.date() - dt.timedelta(days=4)).isoformat()})
        self.assertIn(("acme", "cur", "no file 72 h after export creation"), self.layers(self.issues(old)))


class TestMessage(Base):
    def test_message_has_no_account_bucket_or_key(self):
        self.data_on(NOW.date() - dt.timedelta(days=5))
        self.collector_log({"statusCode": 200, "total_files_copied": 0,
                            "errors": [f"{REPORT}/kiro/AWSLogs/{ACCT}/x.csv: AccessDenied"]})
        msg = fr.format_message(self.issues(), NOW)
        for forbidden in (ACCT, REPORT, DC, RESULTS, "AWSLogs", "kiro/"):
            self.assertNotIn(forbidden, msg)
        self.assertIn("customer=acme", msg)

    def test_handler_dry_run_publishes_once_with_simulated_date(self):
        sns = boto3.client("sns")
        topic = sns.create_topic(Name="kiro-mon-alerts")["TopicArn"]
        sqs = boto3.client("sqs")
        q = sqs.create_queue(QueueName="t")["QueueUrl"]
        qarn = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=qarn)
        self.data_on(dt.date(2026, 10, 28))
        env = {"CONFIG": json.dumps(cfg()), "TOPIC_ARN": topic, "DRY_RUN": "1"}
        with mock.patch.dict(os.environ, env):
            r = fr.handler({"now": "2030-01-01T05:00:00Z"}, None)
        self.assertTrue(r["published"])
        msgs = sqs.receive_message(QueueUrl=q, MaxNumberOfMessages=10).get("Messages", [])
        self.assertEqual(len(msgs), 1)
        self.assertIn("[DRY RUN]", json.loads(msgs[0]["Body"])["Subject"])

    def test_simulated_date_ignored_without_dry_run(self):
        self.data_on(dt.datetime.now(dt.timezone.utc).date())
        topic = boto3.client("sns").create_topic(Name="kiro-mon-alerts")["TopicArn"]
        self.collector_log({"statusCode": 200, "total_files_copied": 1, "errors": []})
        env = {"CONFIG": json.dumps(cfg()), "TOPIC_ARN": topic, "DRY_RUN": "0"}
        with mock.patch.dict(os.environ, env):
            r = fr.handler({"now": "2030-01-01T05:00:00Z"}, None)
        self.assertFalse(r["dry_run"])
        self.assertNotIn("2030", json.dumps(r))


if __name__ == "__main__":
    unittest.main()
