"""kiro-mon-freshness: daily data-freshness check for every customer (task 9.2).

Runs at 05:00 UTC (EventBridge Scheduler). Reads S3 listings, CloudWatch
metrics and CloudWatch Logs only: no Athena, no object bodies except the
collector's own result line. Publishes ONE SNS message per run when anything
is late or failing. The message carries customer slug, layer, latest date and
counts only: no account IDs, bucket names, object keys or error text.

Environment:
  CONFIG      JSON, shape below (rendered by Terraform)
  TOPIC_ARN   SNS topic for alerts
  DRY_RUN     "1" allows event["now"] to simulate a date, and prefixes the
              subject with [DRY RUN]. Ignored otherwise.

CONFIG:
{
  "dc_bucket": "<CID Data Collection bucket>",
  "collector_function": "CID-DC-kiro-user-activity-Lambda",
  "cur_bucket": "<CID data-exports bucket>",
  "customers": [{
    "slug": "acme",
    "kiro_account": "<12 digits>",
    "report_bucket": "<customer report bucket, to attribute collector errors>",
    "sample_results_bucket": "<results bucket of the kiro-<slug> sample>" | null,
    "trail": {"bucket": "...", "region": "us-east-1"} | null,
    "promptmeta_bucket": "..." | null,
    "cur": {"prefix": "cur2/<payer>/<export-name>/data/", "created": "YYYY-MM-DD"} | null
  }]
}

Thresholds (design.md 3.7). "Lag" is today minus the latest data date, in days:
  report via CID, report via sample, prompt metadata   alert when lag > 2
  CloudTrail                                          alert when no object in 24 h
  CUR       alert when no file 72 h after the export was created, or newest file > 3 days old
  collector alert when the Lambda Errors metric > 0 in 24 h, when no result line
            was logged in 24 h, or when the result line lists errors for the
            customer's bucket (AccessDenied counted separately)

Why the collector check reads logs (OQ-12): the CID collector catches every
per-file and per-bucket exception into its `errors` list and still returns
statusCode 200 (module-kiro-user-activity.yaml lines 435-448), so a broken
bucket policy never raises the Lambda Errors metric.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Any

import boto3

LATE_DAYS = 2
TRAIL_HOURS = 24
CUR_FIRST_DELIVERY_HOURS = 72
CUR_STALE_DAYS = 3
RESULT_MARKER = "total_files_copied"


# --------------------------------------------------------------------------- S3 listing helpers

def _prefixes(s3, bucket: str, prefix: str) -> list[str]:
    out: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        out.extend(cp["Prefix"] for cp in page.get("CommonPrefixes", []))
    return out


def _values(prefixes: list[str], key: str) -> list[str]:
    rx = re.compile(rf"{re.escape(key)}=([^/]+)/$")
    return sorted(m.group(1) for p in prefixes if (m := rx.search(p)))


def latest_cid_date(s3, bucket: str, account: str) -> dt.date | None:
    """Max date under kiro-user-activity/account_id=<a>/region=*/year=*/month=*/day=*/."""
    base = f"kiro-user-activity/account_id={account}/"
    best: dt.date | None = None
    for region_prefix in _prefixes(s3, bucket, base):
        years = _values(_prefixes(s3, bucket, region_prefix), "year")
        if not years:
            continue
        ym = f"{region_prefix}year={years[-1]}/"
        months = _values(_prefixes(s3, bucket, ym), "month")
        if not months:
            continue
        days = _values(_prefixes(s3, bucket, f"{ym}month={months[-1]}/"), "day")
        if not days:
            continue
        d = dt.date(int(years[-1]), int(months[-1]), int(days[-1]))
        best = d if best is None or d > best else best
    return best


def latest_partition_date(s3, bucket: str, prefix: str, key: str) -> dt.date | None:
    vals = _values(_prefixes(s3, bucket, prefix), key)
    return dt.date.fromisoformat(vals[-1]) if vals else None


def newest_modified(s3, bucket: str, prefix: str) -> dt.datetime | None:
    best = None
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            if best is None or o["LastModified"] > best:
                best = o["LastModified"]
    return best


# --------------------------------------------------------------------------- checks

def check_lag(slug: str, layer: str, latest: dt.date | None, today: dt.date) -> dict | None:
    if latest is None:
        return {"customer": slug, "layer": layer, "latest": None, "lag_days": None, "issue": "no data"}
    lag = (today - latest).days
    if lag > LATE_DAYS:
        return {"customer": slug, "layer": layer, "latest": latest.isoformat(), "lag_days": lag, "issue": "late"}
    return None


def check_trail(s3, slug: str, account: str, trail: dict, now: dt.datetime) -> dict | None:
    newest = None
    for day in (now.date(), now.date() - dt.timedelta(days=1)):
        p = f"AWSLogs/{account}/CloudTrail/{trail['region']}/{day:%Y/%m/%d}/"
        m = newest_modified(s3, trail["bucket"], p)
        if m and (newest is None or m > newest):
            newest = m
    if newest is None or now - newest > dt.timedelta(hours=TRAIL_HOURS):
        return {"customer": slug, "layer": "cloudtrail",
                "latest": newest.date().isoformat() if newest else None,
                "issue": f"no log file in {TRAIL_HOURS} h"}
    return None


def check_cur(s3, slug: str, bucket: str, cur: dict, now: dt.datetime) -> dict | None:
    created = dt.datetime.combine(dt.date.fromisoformat(cur["created"]), dt.time(), dt.timezone.utc)
    newest = None
    first_of_month = now.date().replace(day=1)
    previous_month = (first_of_month - dt.timedelta(days=1)).replace(day=1)
    for period in (first_of_month, previous_month):
        m = newest_modified(s3, bucket, f"{cur['prefix']}BILLING_PERIOD={period:%Y-%m}/")
        if m and (newest is None or m > newest):
            newest = m
    if newest is None:
        if now - created > dt.timedelta(hours=CUR_FIRST_DELIVERY_HOURS):
            return {"customer": slug, "layer": "cur", "latest": None,
                    "issue": f"no file {CUR_FIRST_DELIVERY_HOURS} h after export creation"}
        return None
    if now - newest > dt.timedelta(days=CUR_STALE_DAYS):
        return {"customer": slug, "layer": "cur", "latest": newest.date().isoformat(),
                "issue": f"newest file older than {CUR_STALE_DAYS} days"}
    return None


def collector_results(logs, function: str, now: dt.datetime) -> list[dict]:
    start = int((now - dt.timedelta(hours=24)).timestamp() * 1000)
    out = []
    kwargs: dict[str, Any] = {
        "logGroupName": f"/aws/lambda/{function}",
        "startTime": start,
        "endTime": int(now.timestamp() * 1000),
        "filterPattern": f'"{RESULT_MARKER}"',
    }
    while True:
        try:
            resp = logs.filter_log_events(**kwargs)
        except logs.exceptions.ResourceNotFoundException:
            return []
        for e in resp.get("events", []):
            try:
                out.append(json.loads(e["message"]))
            except ValueError:
                continue
        if not resp.get("nextToken"):
            return out
        kwargs["nextToken"] = resp["nextToken"]


def collector_error_metric(cw, function: str, now: dt.datetime) -> int:
    resp = cw.get_metric_statistics(
        Namespace="AWS/Lambda", MetricName="Errors",
        Dimensions=[{"Name": "FunctionName", "Value": function}],
        StartTime=now - dt.timedelta(hours=24), EndTime=now, Period=86400, Statistics=["Sum"],
    )
    return int(sum(p.get("Sum", 0) for p in resp.get("Datapoints", [])))


def check_collector(results: list[dict], metric_errors: int, customers: list[dict]) -> list[dict]:
    issues = []
    if metric_errors > 0:
        issues.append({"customer": "*", "layer": "collector", "issue": "lambda errors", "count": metric_errors})
    if not results:
        issues.append({"customer": "*", "layer": "collector", "issue": "no run logged in 24 h"})
        return issues
    errors = [str(x) for r in results for x in r.get("errors", [])]
    for c in customers:
        bucket = c.get("report_bucket") or ""
        mine = [e for e in errors if bucket and (e.startswith(f"{bucket}/") or e.startswith(f"Error listing {bucket}:"))]
        if mine:
            issues.append({
                "customer": c["slug"], "layer": "collector", "issue": "copy errors",
                "count": len(mine), "access_denied": sum("AccessDenied" in e for e in mine),
            })
    return issues


def run_checks(cfg: dict, now: dt.datetime, s3, logs, cw) -> list[dict]:
    today = now.date()
    issues: list[dict] = []
    for c in cfg["customers"]:
        slug = c["slug"]
        issues.append(check_lag(slug, "report via CID", latest_cid_date(s3, cfg["dc_bucket"], c["kiro_account"]), today))
        if c.get("sample_results_bucket"):
            issues.append(check_lag(slug, "report via sample",
                                    latest_partition_date(s3, c["sample_results_bucket"], "normalized/facts/", "export_date"), today))
        if c.get("promptmeta_bucket"):
            issues.append(check_lag(slug, "prompt metadata",
                                    latest_partition_date(s3, c["promptmeta_bucket"], f"promptmeta/account_id={c['kiro_account']}/", "dt"), today))
        if c.get("trail"):
            issues.append(check_trail(s3, slug, c["kiro_account"], c["trail"], now))
        if c.get("cur"):
            issues.append(check_cur(s3, slug, cfg["cur_bucket"], c["cur"], now))
    fn = cfg["collector_function"]
    issues.extend(check_collector(collector_results(logs, fn, now), collector_error_metric(cw, fn, now), cfg["customers"]))
    return [i for i in issues if i]


def format_message(issues: list[dict], now: dt.datetime) -> str:
    lines = [f"Kiro monitoring freshness check {now:%Y-%m-%d %H:%M} UTC: {len(issues)} issue(s)", ""]
    for i in issues:
        parts = [f"customer={i['customer']}", f"layer={i['layer']}", f"issue={i['issue']}"]
        for k in ("latest", "lag_days", "count", "access_denied"):
            if k in i:
                parts.append(f"{k}={i[k]}")
        lines.append("- " + ", ".join(parts))
    return "\n".join(lines)


def handler(event, _context):
    cfg = json.loads(os.environ["CONFIG"])
    dry = os.environ.get("DRY_RUN") == "1"
    now = dt.datetime.now(dt.timezone.utc)
    if dry and isinstance(event, dict) and event.get("now"):
        now = dt.datetime.fromisoformat(event["now"].replace("Z", "+00:00"))
    issues = run_checks(cfg, now, boto3.client("s3"), boto3.client("logs"), boto3.client("cloudwatch"))
    published = False
    if issues:
        subject = ("[DRY RUN] " if dry else "") + f"Kiro monitoring: {len(issues)} freshness issue(s)"
        boto3.client("sns").publish(TopicArn=os.environ["TOPIC_ARN"], Subject=subject[:100],
                                    Message=format_message(issues, now))
        published = True
    result = {"checked_customers": len(cfg["customers"]), "issues": len(issues), "published": published, "dry_run": dry}
    print(json.dumps(result))
    return result
