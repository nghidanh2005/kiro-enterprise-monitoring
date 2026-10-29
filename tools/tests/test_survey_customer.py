"""Tests for tools/survey_customer.py. Offline: moto for STS/S3, mocks for the rest."""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import survey_customer as sc  # noqa: E402

ACCT = "123456789012"  # moto's default account, an AWS documentation placeholder
OTHER = "111122223333"
BUCKET = "example-kiro-report"


def write_config(repo: pathlib.Path, **over) -> dict:
    cfg = {
        "slug": "acme",
        "display_name": "ACME",
        "accounts": {"payer": ACCT, "kiro": ACCT, "identity_center": ACCT},
        "regions": {"kiro_profile": "us-east-1", "identity_center": "us-east-1"},
        "report_bucket": BUCKET,
        "report_prefix": "kiro/",
        "identity_store_id": "",
        "external_id": "kiro-mon-acme-ab12cd34",
        "profiles": {"payer": "", "kiro": "", "identity_center": ""},
    }
    cfg.update(over)
    d = repo / "customers" / "acme"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return cfg


def files_under(path: pathlib.Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


class RecordingSession:
    """Fake boto3 session: STS returns a chosen account, every client() is recorded."""

    def __init__(self, account: str, log: list):
        self.account = account
        self.log = log

    def client(self, service, **_kw):
        self.log.append(service)
        if service == "sts":
            sts = mock.Mock()
            sts.get_caller_identity.return_value = {"Account": self.account}
            return sts
        raise AssertionError(f"survey created a {service} client after an identity mismatch")


class TestIdentityGuard(unittest.TestCase):
    def test_mismatch_stops_before_any_other_call_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            write_config(repo)
            log: list = []
            rc = sc.run(repo, "acme", lambda profile, region: RecordingSession(OTHER, log))
            self.assertEqual(rc, 2)
            self.assertEqual(log, ["sts"])  # first role checked, nothing else
            self.assertEqual(files_under(repo / "customers" / "acme"), ["config.json"])

    def test_second_role_mismatch_also_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            write_config(repo, profiles={"payer": "p", "kiro": "k", "identity_center": "i"})
            log: list = []
            accounts = {"k": ACCT, "p": OTHER, "i": ACCT}
            rc = sc.run(repo, "acme", lambda profile, region: RecordingSession(accounts[profile], log))
            self.assertEqual(rc, 2)
            self.assertEqual(log, ["sts", "sts"])
            self.assertEqual(files_under(repo / "customers" / "acme"), ["config.json"])

    def test_invalid_config_stops_without_creating_a_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            write_config(repo, accounts={"payer": "123", "kiro": ACCT, "identity_center": ACCT})
            rc = sc.run(repo, "acme", lambda profile, region: self.fail("session created"))
            self.assertEqual(rc, 2)

    def test_missing_config_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = sc.run(pathlib.Path(tmp), "acme", lambda profile, region: self.fail("session created"))
            self.assertEqual(rc, 2)


class TestParse(unittest.TestCase):
    def test_parse_report_key(self):
        key = f"kiro/AWSLogs/{ACCT}/KiroLogs/user_report/us-east-1/2026/09/30/01/x.csv"
        self.assertEqual(sc.parse_report_key(key), (ACCT, "us-east-1", dt.date(2026, 9, 30)))
        self.assertIsNone(sc.parse_report_key(f"kiro/AWSLogs/{ACCT}/KiroLogs/by_user_analytic/x.csv"))

    def test_summarize_advanced_selectors(self):
        sel = {"AdvancedEventSelectors": [{"FieldSelectors": [
            {"Field": "eventCategory", "Equals": ["Data"]},
            {"Field": "resources.type", "Equals": [sc.KIRO_RESOURCE_TYPE]},
        ]}]}
        self.assertEqual(sc._summarize_selectors(sel), {
            "management_events": False,
            "data_event_types": [sc.KIRO_RESOURCE_TYPE],
            "captures_kiro_profile": True,
        })


try:
    import boto3
    from moto import mock_aws
except ImportError:  # pragma: no cover
    mock_aws = None


@unittest.skipIf(mock_aws is None, "moto not installed")
class TestHappyPath(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_DEFAULT_REGION": "us-east-1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def _mocked_services(self):
        ct = mock.Mock()
        ct.describe_trails.return_value = {"trailList": [{
            "Name": "org-trail", "TrailARN": "arn:aws:cloudtrail:us-east-1:%s:trail/org-trail" % ACCT,
            "HomeRegion": "us-east-1", "IsMultiRegionTrail": True, "IsOrganizationTrail": False,
            "S3BucketName": "trail-bucket",
        }]}
        ct.get_event_selectors.return_value = {"EventSelectors": [
            {"IncludeManagementEvents": True, "DataResources": []}]}
        org = mock.Mock()
        org.describe_organization.return_value = {"Organization": {"MasterAccountId": ACCT, "FeatureSet": "ALL"}}
        bcm = mock.Mock()
        bcm.list_exports.return_value = {"Exports": [{
            "ExportArn": "arn:x", "ExportName": "cid-cur2",
            "ExportStatus": {"StatusCode": "HEALTHY", "CreatedAt": dt.datetime(2026, 1, 5)}}]}
        bcm.get_export.return_value = {"Export": {
            "DataQuery": {"QueryStatement": "SELECT line_item_resource_id FROM COST_AND_USAGE_REPORT",
                          "TableConfigurations": {"COST_AND_USAGE_REPORT": {"INCLUDE_RESOURCES": "TRUE"}}},
            "DestinationConfigurations": {"S3Destination": {
                "S3Bucket": "cid-data-exports", "S3Prefix": f"cur2/{ACCT}", "S3Region": "us-east-1"}},
            "RefreshCadence": {"Frequency": "SYNCHRONOUS"}}}
        cur = mock.Mock()
        cur.describe_report_definitions.return_value = {"ReportDefinitions": []}
        sso = mock.Mock()
        sso.list_instances.return_value = {"Instances": [
            {"IdentityStoreId": "d-xxxxxxxxxx", "OwnerAccountId": ACCT, "Status": "ACTIVE"}]}
        ids = mock.Mock()
        ids.get_paginator.return_value.paginate.return_value = [
            {"Users": [{"UserName": "alice.secret", "Emails": [{"Value": "alice@example.com"}]}] * 3},
            {"Users": [{"UserName": "bob.secret"}] * 2},
        ]
        return {"cloudtrail": ct, "organizations": org, "bcm-data-exports": bcm,
                "cur": cur, "sso-admin": sso, "identitystore": ids}

    def test_survey_writes_counts_and_policy_but_no_identities(self):
        with mock_aws(), tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            write_config(repo)
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket=BUCKET)
            s3.put_bucket_encryption(Bucket=BUCKET, ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
            policy = {"Version": "2012-10-17", "Statement": [{
                "Sid": "KiroWrite", "Effect": "Allow", "Principal": {"Service": "q.amazonaws.com"},
                "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{BUCKET}/*"}]}
            s3.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(policy))
            base = f"AWSLogs/{ACCT}/KiroLogs/user_report/us-east-1"
            for k in (f"kiro/{base}/2026/08/01/00/a.csv", f"kiro/{base}/2026/10/28/00/b.csv",
                      f"old/{base}/2026/05/02/00/c.csv", f"kiro/{base}/2026/08/01/00/readme.txt"):
                s3.put_object(Bucket=BUCKET, Key=k, Body=b"x")

            fakes = self._mocked_services()
            real = boto3.Session()

            class Sess:
                def client(self, service, **kw):
                    return fakes[service] if service in fakes else real.client(service, **kw)

            rc = sc.run(repo, "acme", lambda profile, region: Sess())
            self.assertEqual(rc, 0)

            out = repo / "customers" / "acme"
            survey = json.loads((out / "survey" / "survey.json").read_text(encoding="utf-8"))
            rb = survey["report_bucket"]
            self.assertEqual(rb["encryption"], ["AES256"])
            self.assertEqual(rb["prefixes"]["kiro/"], {
                "files": 2, "earliest": "2026-08-01", "latest": "2026-10-28",
                "regions": ["us-east-1"], "error": None})
            self.assertEqual(rb["prefixes"]["old/"]["latest"], "2026-05-02")
            self.assertNotIn("policy_raw", rb)
            saved = json.loads((out / "survey" / "bucket-policy-report.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, policy)
            self.assertEqual(survey["identity_center"]["instances"][0]["user_count"], 5)
            self.assertTrue(survey["payer"]["data_exports"][0]["selects_line_item_resource_id"])
            self.assertEqual(survey["trails"][0]["captures_kiro_profile"], False)

            text = "".join(p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file())
            for secret in ("alice", "bob", "example.com"):
                self.assertNotIn(secret, text)
            md = (out / "SURVEY.md").read_text(encoding="utf-8")
            self.assertIn("Reports exist under a prefix other than `kiro/`", md)


if __name__ == "__main__":
    unittest.main()
