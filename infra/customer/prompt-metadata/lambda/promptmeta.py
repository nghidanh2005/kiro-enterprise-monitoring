"""kiro-mon-promptmeta: prompt-log metadata extractor (task 7.4, only when D4 = yes).

Runs in the CUSTOMER account on every new prompt-log object. Reads the full
record, keeps only the allow-list of design.md 3.5, and writes one JSON line
per record to the observability account:

    s3://$DEST_BUCKET/promptmeta/account_id=$SOURCE_ACCOUNT_ID/dt=YYYY-MM-DD/<sha256 of source key>.jsonl

Content never leaves: no full-text field is copied, only lengths and counts.
ID-like fields are also checked against a strict pattern, so a schema change
that put text into an ID field yields null instead of content. Logs carry
counts only, plus one CloudWatch embedded-metric line (SkippedRecords).
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import re
import urllib.parse
from collections import defaultdict
from typing import Any

OUTPUT_FIELDS = (
    "record_type", "request_id", "conversation_id", "utterance_id", "user_id", "event_ts",
    "chat_trigger_type", "customization_arn_present", "prompt_chars", "response_chars",
    "web_link_count", "code_reference_count", "source_key_sha256",
)
RE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RE_TRIGGER = re.compile(r"^[A-Z][A-Z_]{0,31}$")
RE_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$")
METRIC_NAMESPACE = "KiroMonitoring/PromptMetadata"


def _id(v: Any) -> str | None:
    return v if isinstance(v, str) and RE_ID.match(v) else None


def _len(v: Any) -> int:
    return len(v) if isinstance(v, str) else 0


def _count(v: Any) -> int:
    return len(v) if isinstance(v, list) else 0


def reduce_record(rec: dict, source_key: str) -> dict | None:
    """Allow-listed metadata of one record, or None for an unknown structure."""
    key_hash = hashlib.sha256(source_key.encode("utf-8")).hexdigest()
    if "generateAssistantResponseEventRequest" in rec:
        req = rec.get("generateAssistantResponseEventRequest") or {}
        resp = rec.get("generateAssistantResponseEventResponse") or {}
        meta = resp.get("messageMetadata") or {}
        trigger = req.get("chatTriggerType")
        out = {
            "record_type": "chat",
            "request_id": _id(resp.get("requestId")),
            "conversation_id": _id(meta.get("conversationId")),
            "utterance_id": _id(meta.get("utteranceId")),
            "chat_trigger_type": trigger if isinstance(trigger, str) and RE_TRIGGER.match(trigger) else None,
            "prompt_chars": _len(req.get("prompt")),
            "response_chars": _len(resp.get("assistantResponse")),
            "web_link_count": _count(resp.get("supplementaryWebLinksEvent")),
            "code_reference_count": _count(resp.get("codeReferenceEvents")),
        }
    elif "generateCompletionsEventRequest" in rec:
        req = rec.get("generateCompletionsEventRequest") or {}
        resp = rec.get("generateCompletionsEventResponse") or {}
        completions = resp.get("completions") if isinstance(resp.get("completions"), list) else []
        out = {
            "record_type": "inline",
            "request_id": _id(resp.get("requestId")),
            "conversation_id": None,
            "utterance_id": None,
            "chat_trigger_type": None,
            "prompt_chars": _len(req.get("leftContext")) + _len(req.get("rightContext")),
            "response_chars": sum(_len(c) for c in completions),
            "web_link_count": 0,
            "code_reference_count": _count(resp.get("codeReferenceEvents")),
        }
    else:
        return None
    ts = req.get("timeStamp")
    out.update({
        "user_id": _id(req.get("userId")),
        "event_ts": ts if isinstance(ts, str) and RE_TS.match(ts) else None,
        "customization_arn_present": req.get("customizationArn") is not None,
        "source_key_sha256": key_hash,
    })
    return {k: out[k] for k in OUTPUT_FIELDS}


def object_key(s3_record: dict) -> str:
    return urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"])


def _load(body: bytes) -> dict:
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return json.loads(body.decode("utf-8"))


def _emf(skipped: int, records: int) -> str:
    return json.dumps({
        "_aws": {
            "Timestamp": 0,
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE,
                "Dimensions": [[]],
                "Metrics": [{"Name": "SkippedRecords", "Unit": "Count"}, {"Name": "Records", "Unit": "Count"}],
            }],
        },
        "SkippedRecords": skipped,
        "Records": records,
    })


def handler(event, _context, s3=None):
    if s3 is None:
        import boto3
        s3 = boto3.client("s3")
    dest = os.environ["DEST_BUCKET"]
    account = os.environ["SOURCE_ACCOUNT_ID"]
    objects = records = skipped = written = 0
    for r in event.get("Records", []):
        bucket = r["s3"]["bucket"]["name"]
        key = object_key(r)
        objects += 1
        try:
            doc = _load(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        except (ValueError, OSError):
            skipped += 1  # unreadable object: counted, never logged by name
            continue
        by_day: dict[str, list[str]] = defaultdict(list)
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        raw = doc.get("records") if isinstance(doc, dict) else None
        for rec in raw if isinstance(raw, list) else []:
            out = reduce_record(rec, key) if isinstance(rec, dict) else None
            if out is None:
                skipped += 1
                continue
            records += 1
            # A record without a valid timestamp goes to today's partition: a
            # dt=unknown folder would be invisible to the date projection.
            day = out["event_ts"][:10] if out["event_ts"] else today
            by_day[day].append(json.dumps(out, separators=(",", ":")))
        for day, lines in by_day.items():
            s3.put_object(
                Bucket=dest,
                Key=f"promptmeta/account_id={account}/dt={day}/{key_hash}.jsonl",
                Body=("\n".join(lines) + "\n").encode("utf-8"),
                ContentType="application/x-ndjson",
            )
            written += 1
    result = {"objects": objects, "records": records, "skipped": skipped, "files_written": written}
    print(json.dumps(result))
    print(_emf(skipped, records))
    return result
