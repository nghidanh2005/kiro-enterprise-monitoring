"""Tests for the prompt-metadata Lambda (task 7.4). Written before the Lambda. Offline.

Records are the chat and inline examples from the Kiro prompt-logging docs.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lambda"))
import promptmeta as pm  # noqa: E402

USER = "d-xxxxxxxxxx.b8f1f340-9081-70ad-5fc5-0f37151937a6"
CHAT = {
    "generateAssistantResponseEventRequest": {
        "prompt": "Can you explain how to set up object-level access control in an S3 bucket using AWS CDK?",
        "chatTriggerType": "MANUAL", "customizationArn": None, "userId": USER,
        "timeStamp": "2025-01-06T15:05:56.313070Z"},
    "generateAssistantResponseEventResponse": {
        "assistantResponse": "I'll explain how to set up object-level access control for an S3 bucket using AWS CDK...",
        "followupPrompts": "How can you implement multi-factor authentication for S3 bucket access using AWS CDK?",
        "messageMetadata": {"conversationId": "491bd3cd-d70d-40c0-a452-5ef3f0878422", "utteranceId": None},
        "codeReferenceEvents": [],
        "supplementaryWebLinksEvent": [{
            "uri": "https://docs.aws.amazon.com/AmazonS3/latest/API/s3_example_s3_Scenario_ManageACLs_section.html",
            "title": "[1] Manage access control lists (ACLs) for Amazon S3 buckets using an AWS SDK",
            "snippet": "The following code example shows how to manage access control lists (ACLs) for Amazon S3 buckets..."}],
        "requestId": "dad38fc0-815c-45f7-970a-db916cb7f131"}}
INLINE = {
    "generateCompletionsEventRequest": {
        "leftContext": "import * cdk from 'aws-cdk-lib';\r\nimport * s3 from 'aws-cdk-lib/aws-s3';\r\nexport class MyStack extends Stack {",
        "rightContext": "  }\r\n}", "fileName": "cdk-modified.ts", "customizationArn": None, "userId": USER,
        "timeStamp": "2025-01-06T15:09:16.412719Z"},
    "generateCompletionsEventResponse": {
        "completions": ["synth() {\n    return cdk.App.prototype.synth.apply(this, arguments);\n  }"],
        "requestId": "797c70ee-abc9-4cc7-a148-b9df17f6ce48"}}
STRANGE = {"someFutureEventRequest": {"prompt": "zqx secret future prompt body that must never leave"}}
KEY = "kiro-prompt-logs/AWSLogs/123456789012/KiroLogs/GenerateAssistantResponse/us-east-1/2025/01/06/15/a.json.gz"

FULL_TEXT_KEYS = {"prompt", "assistantResponse", "followupPrompts", "leftContext", "rightContext",
                  "completions", "fileName", "uri", "title", "snippet"}


def full_text_values(node, key=None):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from full_text_values(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from full_text_values(v, key)
    elif isinstance(node, str) and key in FULL_TEXT_KEYS:
        yield node


def substrings(s: str, n: int = 8):
    return {s[i:i + n] for i in range(max(len(s) - n + 1, 0))}


SECRETS = {sub for v in full_text_values([CHAT, INLINE, STRANGE]) for sub in substrings(v)}


class TestReduce(unittest.TestCase):
    def test_chat_has_exactly_the_allowlisted_keys(self):
        out = pm.reduce_record(CHAT, KEY)
        self.assertEqual(set(out), set(pm.OUTPUT_FIELDS))
        self.assertEqual(out["record_type"], "chat")
        self.assertEqual(out["request_id"], "dad38fc0-815c-45f7-970a-db916cb7f131")
        self.assertEqual(out["conversation_id"], "491bd3cd-d70d-40c0-a452-5ef3f0878422")
        self.assertIsNone(out["utterance_id"])
        self.assertEqual(out["chat_trigger_type"], "MANUAL")
        self.assertFalse(out["customization_arn_present"])
        req, resp = CHAT["generateAssistantResponseEventRequest"], CHAT["generateAssistantResponseEventResponse"]
        self.assertEqual(out["prompt_chars"], len(req["prompt"]))
        self.assertEqual(out["response_chars"], len(resp["assistantResponse"]))
        self.assertEqual((out["web_link_count"], out["code_reference_count"]), (1, 0))
        self.assertEqual(out["source_key_sha256"], hashlib.sha256(KEY.encode()).hexdigest())
        self.assertEqual(out["event_ts"], "2025-01-06T15:05:56.313070Z")

    def test_inline_lengths(self):
        out = pm.reduce_record(INLINE, KEY)
        req = INLINE["generateCompletionsEventRequest"]
        self.assertEqual(out["record_type"], "inline")
        self.assertEqual(out["prompt_chars"], len(req["leftContext"]) + len(req["rightContext"]))
        self.assertEqual(out["response_chars"], sum(len(c) for c in INLINE["generateCompletionsEventResponse"]["completions"]))
        self.assertIsNone(out["conversation_id"])
        self.assertIsNone(out["chat_trigger_type"])

    def test_unknown_record_is_skipped(self):
        self.assertIsNone(pm.reduce_record(STRANGE, KEY))

    def test_content_smuggled_into_an_id_field_is_dropped(self):
        drift = json.loads(json.dumps(CHAT))
        drift["generateAssistantResponseEventResponse"]["requestId"] = "please summarise our merger plan with ACME"
        drift["generateAssistantResponseEventRequest"]["chatTriggerType"] = "user typed: our merger plan"
        out = pm.reduce_record(drift, KEY)
        self.assertIsNone(out["request_id"])
        self.assertIsNone(out["chat_trigger_type"])


class FakeS3:
    def __init__(self, body: bytes):
        self.body = body
        self.puts: list[dict] = []

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.body)}

    def put_object(self, **kw):
        self.puts.append(kw)
        return {}


class TestHandler(unittest.TestCase):
    def run_handler(self, records):
        body = gzip.compress(json.dumps({"records": records}).encode())
        s3 = FakeS3(body)
        event = {"Records": [{"s3": {"bucket": {"name": "src"}, "object": {"key": KEY.replace("/", "%2F", 0)}}}]}
        env = {"DEST_BUCKET": "dest", "SOURCE_ACCOUNT_ID": "123456789012"}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            result = pm.handler(event, None, s3=s3)
        return result, s3, out.getvalue()

    def test_writes_jsonl_under_partitioned_key_and_logs_counts_only(self):
        result, s3, logs = self.run_handler([CHAT, INLINE, STRANGE])
        self.assertEqual(result, {"objects": 1, "records": 2, "skipped": 1, "files_written": 1})
        self.assertEqual(len(s3.puts), 1)
        put = s3.puts[0]
        self.assertEqual(put["Bucket"], "dest")
        self.assertTrue(put["Key"].startswith("promptmeta/account_id=123456789012/dt=2025-01-06/"))
        self.assertTrue(put["Key"].endswith(".jsonl"))
        lines = put["Body"].decode().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertEqual(set(json.loads(line)), set(pm.OUTPUT_FIELDS))
        written = put["Body"].decode()
        self.assertEqual([s for s in SECRETS if s in written], [])
        self.assertEqual([s for s in SECRETS if s in logs], [])
        self.assertNotIn(KEY, logs)

    def test_emits_skipped_records_metric(self):
        _, _, logs = self.run_handler([STRANGE])
        emf = [json.loads(l) for l in logs.splitlines() if '"_aws"' in l]
        self.assertEqual(len(emf), 1)
        self.assertEqual(emf[0]["SkippedRecords"], 1)
        self.assertEqual(emf[0]["_aws"]["CloudWatchMetrics"][0]["Namespace"], "KiroMonitoring/PromptMetadata")

    def test_nothing_written_when_all_records_skipped(self):
        result, s3, _ = self.run_handler([STRANGE])
        self.assertEqual((result["files_written"], s3.puts), (0, []))

    def test_url_encoded_key_is_decoded(self):
        self.assertEqual(pm.object_key({"s3": {"object": {"key": "a+b%2Fc.json"}}}), "a b/c.json")

    def test_record_without_valid_timestamp_goes_to_todays_partition(self):
        rec = json.loads(json.dumps(CHAT))
        rec["generateAssistantResponseEventRequest"]["timeStamp"] = "yesterday-ish"
        _, s3, _ = self.run_handler([rec])
        import datetime as dt
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        self.assertIn(f"/dt={today}/", s3.puts[0]["Key"])


if __name__ == "__main__":
    unittest.main()
