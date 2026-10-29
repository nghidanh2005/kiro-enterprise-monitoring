#!/usr/bin/env python3
"""Measure OQ-1, OQ-2, OQ-3, OQ-4, OQ-9 from the temporary experiment buckets (task 3.5 / 3.6).

    python tools/measure_oq.py --expected-account <12 digits> --region us-east-1 \
        --trail-bucket <bucket> [--trail-prefix AWSLogs/] \
        --prompt-bucket <bucket> [--prompt-prefix kiro-prompt-logs/] \
        [--report-bucket <bucket> --report-prefix kiro/] \
        --chat-times customers/<D7>/oq-chat-times.txt [--profile <p>] [--json out.json]

Chat-times file: one line per chat, `<ISO-8601 UTC> <label>`, label is free
text such as `ide`, `cli`, `error`. Space chats at least 5 minutes apart so the
per-chat windows do not overlap.

What it prints, and nothing else:
  OQ-1  eventName counts for resources.type AWS::CodeWhisperer::Profile, and
        events per chat inside [-30 s, +300 s] of each chat time
  OQ-2  share of prompt-log requestId values that equal a CloudTrail requestID
  OQ-3  user ID shape per source: length, first 2 characters, position of '.',
        and which CloudTrail field carried it
  OQ-4  for chats labelled `error`: CloudTrail events with errorCode, and
        prompt-log records, inside the window
  OQ-9  minutes from record timeStamp to the S3 LastModified of its file

Content guard. Prompt logs hold full prompts and answers. Records are reduced
to an allow-list of metadata as soon as they are parsed, and every value goes
out through emit(), which accepts only numbers, booleans and strings shorter
than 40 characters, and refuses any value or label that names a full-text
field. A refusal raises instead of printing.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import json
import pathlib
import sys
from typing import Any, Iterable

KIRO_RESOURCE_TYPE = "AWS::CodeWhisperer::Profile"
FULL_TEXT_FIELDS = frozenset({
    "prompt", "assistantResponse", "followupPrompts", "leftContext", "rightContext",
    "completions", "fileName", "supplementaryWebLinksEvent", "codeReferenceEvents",
    "snippet", "requestParameters", "responseElements",
})
MAX_STR = 40
WINDOW_BEFORE = dt.timedelta(seconds=30)
WINDOW_AFTER = dt.timedelta(seconds=300)


class ContentGuardError(RuntimeError):
    """Raised instead of printing anything that could carry content."""


_FIELD_RE = __import__("re").compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(sorted(FULL_TEXT_FIELDS)) + r")(?![A-Za-z0-9_])",
    __import__("re").IGNORECASE,
)


def _check(text: str, what: str) -> None:
    m = _FIELD_RE.search(text)
    if m:
        raise ContentGuardError(f"{what} names full-text field {m.group(1)!r}")


def emit(label: str, value: Any, out: list[str]) -> None:
    """The ONLY way anything leaves this tool."""
    _check(label, "label")
    if isinstance(value, bool) or value is None:
        pass
    elif isinstance(value, (int, float)):
        pass
    elif isinstance(value, str):
        if len(value) >= MAX_STR:
            raise ContentGuardError(f"value for {label!r} is {len(value)} chars (limit {MAX_STR - 1})")
        _check(value, f"value for {label!r}")
    else:
        raise ContentGuardError(f"value for {label!r} has type {type(value).__name__}")
    out.append(f"{label}: {value}")


# --------------------------------------------------------------------------- parsing

def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def load_json_bytes(data: bytes) -> Any:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def prompt_records(doc: dict, last_modified: dt.datetime | None) -> Iterable[dict]:
    """Reduce each prompt-log record to allow-listed metadata. Content is dropped here."""
    for rec in doc.get("records", []):
        for req_key, resp_key, kind in (
            ("generateAssistantResponseEventRequest", "generateAssistantResponseEventResponse", "chat"),
            ("generateCompletionsEventRequest", "generateCompletionsEventResponse", "inline"),
        ):
            if req_key in rec:
                req, resp = rec.get(req_key) or {}, rec.get(resp_key) or {}
                yield {
                    "kind": kind,
                    "request_id": resp.get("requestId"),
                    "user_id": req.get("userId"),
                    "ts": parse_ts(req.get("timeStamp")),
                    "file_modified": last_modified,
                }
                break
        else:
            yield {"kind": "unknown", "request_id": None, "user_id": None, "ts": None,
                   "file_modified": last_modified}


USER_ID_PATHS = (
    ("userIdentity.onBehalfOf.userId", ("onBehalfOf", "userId")),
    ("userIdentity.userId", ("userId",)),
    ("userIdentity.principalId", ("principalId",)),
)


def trail_events(doc: dict) -> Iterable[dict]:
    """Reduce CloudTrail records for the Kiro profile type to metadata."""
    for r in doc.get("Records", []):
        types = {res.get("type") for res in r.get("resources") or []}
        if KIRO_RESOURCE_TYPE not in types:
            continue
        ui = r.get("userIdentity") or {}
        user_id, field = None, None
        for name, path in USER_ID_PATHS:
            v: Any = ui
            for p in path:
                v = v.get(p) if isinstance(v, dict) else None
            if isinstance(v, str) and v:
                user_id, field = v, name
                break
        yield {
            "event_name": r.get("eventName"),
            "request_id": r.get("requestID"),
            "ts": parse_ts(r.get("eventTime")),
            "error": bool(r.get("errorCode")),
            "user_id": user_id,
            "user_id_field": field,
        }


def id_shape(values: Iterable[str | None]) -> dict:
    vals = [v for v in values if v]
    if not vals:
        return {"count": 0}
    lengths = collections.Counter(len(v) for v in vals)
    heads = collections.Counter(v[:2] for v in vals)
    dots = collections.Counter(v.find(".") for v in vals)
    return {
        "count": len(vals),
        "length": lengths.most_common(1)[0][0],
        "length_variants": len(lengths),
        "head2": heads.most_common(1)[0][0],
        "dot_at": dots.most_common(1)[0][0],
    }


def read_chat_times(path: pathlib.Path) -> list[tuple[dt.datetime, str]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        ts, _, label = line.partition(" ")
        t = parse_ts(ts)
        if t is None:
            raise SystemExit(f"bad chat time: {ts!r}")
        out.append((t, label.strip() or "chat"))
    return out


# --------------------------------------------------------------------------- metrics

def measure(events: list[dict], records: list[dict], chats: list[tuple[dt.datetime, str]],
            report_user_ids: list[str]) -> list[str]:
    out: list[str] = []
    # OQ-1
    emit("OQ-1 kiro profile data events", len(events), out)
    for name, n in sorted(collections.Counter(e["event_name"] or "(none)" for e in events).items()):
        emit(f"OQ-1 eventName {name}", n, out)
    for i, (t, label) in enumerate(chats, start=1):
        lo, hi = t - WINDOW_BEFORE, t + WINDOW_AFTER
        n_ev = sum(1 for e in events if e["ts"] and lo <= e["ts"] <= hi)
        n_rec = sum(1 for r in records if r["ts"] and lo <= r["ts"] <= hi)
        emit(f"OQ-1 chat {i} ({label[:12]}) trail events", n_ev, out)
        emit(f"OQ-1 chat {i} ({label[:12]}) promptlog records", n_rec, out)
        if label.lower().startswith("error"):  # OQ-4
            emit(f"OQ-4 chat {i} trail events with errorCode",
                 sum(1 for e in events if e["ts"] and lo <= e["ts"] <= hi and e["error"]), out)
            emit(f"OQ-4 chat {i} promptlog records", n_rec, out)
    # OQ-2
    trail_ids = {e["request_id"] for e in events if e["request_id"]}
    rec_ids = [r["request_id"] for r in records if r["request_id"]]
    emit("OQ-2 promptlog records with requestId", len(rec_ids), out)
    emit("OQ-2 of those also in CloudTrail", sum(1 for x in rec_ids if x in trail_ids), out)
    emit("OQ-2 match ratio", round(sum(1 for x in rec_ids if x in trail_ids) / len(rec_ids), 3) if rec_ids else None, out)
    # OQ-3
    for source, vals in (
        ("report", report_user_ids),
        ("cloudtrail", [e["user_id"] for e in events]),
        ("promptlog", [r["user_id"] for r in records]),
    ):
        for k, v in id_shape(vals).items():
            emit(f"OQ-3 {source} user id {k}", v, out)
    for field, n in collections.Counter(e["user_id_field"] or "(none)" for e in events).items():
        emit(f"OQ-3 cloudtrail user id from {field.split('.')[-1]}", n, out)
    # OQ-9
    lags = sorted(
        (r["file_modified"] - r["ts"]).total_seconds() / 60
        for r in records if r["ts"] and r["file_modified"]
    )
    emit("OQ-9 records with timing", len(lags), out)
    if lags:
        emit("OQ-9 delay minutes min", round(lags[0], 1), out)
        emit("OQ-9 delay minutes median", round(lags[len(lags) // 2], 1), out)
        emit("OQ-9 delay minutes max", round(lags[-1], 1), out)
    emit("record kinds unknown", sum(1 for r in records if r["kind"] == "unknown"), out)
    return out


# --------------------------------------------------------------------------- S3

def iter_objects(s3, bucket: str, prefix: str) -> Iterable[tuple[str, dt.datetime]]:
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            yield o["Key"], o["LastModified"]


def report_user_ids(s3, bucket: str, prefix: str) -> list[str]:
    """Only the UserId column is kept; every other column (email included) is dropped per row."""
    import csv
    import io
    ids: list[str] = []
    for key, _ in iter_objects(s3, bucket, prefix):
        if "/KiroLogs/user_report/" not in key or not key.endswith(".csv"):
            continue
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        for row in csv.DictReader(io.StringIO(body)):
            col = next((k for k in row if k and k.lower() == "userid"), None)
            if col and row[col]:
                ids.append(row[col])
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure the OQ experiment, printing only counts and shapes.")
    ap.add_argument("--expected-account", required=True)
    ap.add_argument("--region", required=True)
    ap.add_argument("--profile")
    ap.add_argument("--trail-bucket", required=True)
    ap.add_argument("--trail-prefix", default="AWSLogs/")
    ap.add_argument("--prompt-bucket", required=True)
    ap.add_argument("--prompt-prefix", default="")
    ap.add_argument("--report-bucket")
    ap.add_argument("--report-prefix", default="kiro/")
    ap.add_argument("--chat-times", required=True, type=pathlib.Path)
    ap.add_argument("--json", type=pathlib.Path, help="also write the printed lines as JSON")
    args = ap.parse_args(argv)

    import boto3

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    acct = sess.client("sts", region_name=args.region).get_caller_identity()["Account"]
    if acct != args.expected_account:
        print("STOP: profile is not the expected account. Nothing was read.")
        return 2
    s3 = sess.client("s3", region_name=args.region)

    events: list[dict] = []
    for key, _ in iter_objects(s3, args.trail_bucket, args.trail_prefix):
        if key.endswith((".json.gz", ".json")) and "/CloudTrail/" in key:
            events.extend(trail_events(load_json_bytes(s3.get_object(Bucket=args.trail_bucket, Key=key)["Body"].read())))
    records: list[dict] = []
    for key, modified in iter_objects(s3, args.prompt_bucket, args.prompt_prefix):
        if key.endswith((".json", ".json.gz", ".gz")):
            records.extend(prompt_records(load_json_bytes(s3.get_object(Bucket=args.prompt_bucket, Key=key)["Body"].read()), modified))
    rids = report_user_ids(s3, args.report_bucket, args.report_prefix) if args.report_bucket else []

    lines = measure(events, records, read_chat_times(args.chat_times), rids)
    print("\n".join(lines))
    if args.json:
        args.json.write_text(json.dumps(lines, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
