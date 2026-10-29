"""Tests for tools/measure_oq.py (task 3.5). Offline.

The two prompt-log records are the examples published in the Kiro prompt
logging documentation (chat and inline). The test asserts that no substring
of 8+ characters from any full-text field reaches the output.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import measure_oq as mo  # noqa: E402

USER = "d-xxxxxxxxxx.b8f1f340-9081-70ad-5fc5-0f37151937a6"
CHAT_DOC = {"records": [{
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
        "requestId": "dad38fc0-815c-45f7-970a-db916cb7f131"}}]}
INLINE_DOC = {"records": [{
    "generateCompletionsEventRequest": {
        "leftContext": "import * cdk from 'aws-cdk-lib';\r\nimport * s3 from 'aws-cdk-lib/aws-s3';\r\nexport class MyStack extends Stack {",
        "rightContext": "", "fileName": "cdk-modified.ts", "customizationArn": None, "userId": USER,
        "timeStamp": "2025-01-06T15:09:16.412719Z"},
    "generateCompletionsEventResponse": {
        "completions": ["synth() {\n    return cdk.App.prototype.synth.apply(this, arguments);\n  }"],
        "requestId": "797c70ee-abc9-4cc7-a148-b9df17f6ce48"}}]}
STRANGE_DOC = {"records": [{"someFutureEvent": {"prompt": "secret prompt text that must never print"}}]}

TRAIL_DOC = {"Records": [
    {"eventName": "GenerateAssistantResponse", "eventTime": "2025-01-06T15:05:57Z",
     "requestID": "dad38fc0-815c-45f7-970a-db916cb7f131",
     "userIdentity": {"type": "AssumedRole", "onBehalfOf": {"userId": "b8f1f340-9081-70ad-5fc5-0f37151937a6"}},
     "resources": [{"type": "AWS::CodeWhisperer::Profile", "ARN": "arn:aws:codewhisperer:us-east-1:123456789012:profile/X"}],
     "requestParameters": {"conversationState": "zq9 payload xk7 qwv4 mmn8 pp2"}},
    {"eventName": "GenerateAssistantResponse", "eventTime": "2025-01-06T15:06:30Z", "requestID": "other-id",
     "errorCode": "ThrottlingException",
     "userIdentity": {"onBehalfOf": {"userId": "b8f1f340-9081-70ad-5fc5-0f37151937a6"}},
     "resources": [{"type": "AWS::CodeWhisperer::Profile"}]},
    {"eventName": "GetObject", "eventTime": "2025-01-06T15:06:00Z", "resources": [{"type": "AWS::S3::Object"}]},
]}

FULL_TEXT_VALUES = []
for doc in (CHAT_DOC, INLINE_DOC, STRANGE_DOC, TRAIL_DOC):
    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in mo.FULL_TEXT_FIELDS | {"prompt", "uri", "title", "conversationState"}:
            FULL_TEXT_VALUES.append(node)
    walk(doc)


def substrings(s: str, n: int = 8):
    return {s[i:i + n] for i in range(0, max(len(s) - n + 1, 0))}


def run_measure():
    modified = dt.datetime(2025, 1, 6, 15, 12, tzinfo=dt.timezone.utc)
    records = [*mo.prompt_records(CHAT_DOC, modified), *mo.prompt_records(INLINE_DOC, modified),
               *mo.prompt_records(STRANGE_DOC, modified)]
    events = list(mo.trail_events(TRAIL_DOC))
    chats = [(dt.datetime(2025, 1, 6, 15, 5, 50, tzinfo=dt.timezone.utc), "ide"),
             (dt.datetime(2025, 1, 6, 15, 6, 25, tzinfo=dt.timezone.utc), "error")]
    return mo.measure(events, records, chats, ["b8f1f340-9081-70ad-5fc5-0f37151937a6"])


class TestNoContentLeaks(unittest.TestCase):
    def test_output_has_no_substring_from_full_text_fields(self):
        text = "\n".join(run_measure())
        self.assertTrue(FULL_TEXT_VALUES, "fixture must contain full-text values")
        leaked = [s for v in FULL_TEXT_VALUES for s in substrings(v) if s in text]
        self.assertEqual(leaked, [])

    def test_reduced_records_hold_only_allowlisted_keys(self):
        for doc in (CHAT_DOC, INLINE_DOC, STRANGE_DOC):
            for r in mo.prompt_records(doc, None):
                self.assertEqual(set(r), {"kind", "request_id", "user_id", "ts", "file_modified"})


class TestEmitGuard(unittest.TestCase):
    def test_rejects_long_strings(self):
        with self.assertRaises(mo.ContentGuardError):
            mo.emit("x", "a" * 40, [])

    def test_rejects_full_text_field_in_label_or_value(self):
        with self.assertRaises(mo.ContentGuardError):
            mo.emit("assistantResponse length", 3, [])
        with self.assertRaises(mo.ContentGuardError):
            mo.emit("x", "prompt", [])

    def test_rejects_containers(self):
        with self.assertRaises(mo.ContentGuardError):
            mo.emit("x", ["a"], [])

    def test_accepts_numbers_and_short_strings(self):
        out: list[str] = []
        mo.emit("OQ-2 promptlog records", 3, out)
        mo.emit("OQ-3 head", "d-", out)
        self.assertEqual(out, ["OQ-2 promptlog records: 3", "OQ-3 head: d-"])


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.lines = dict(line.split(": ", 1) for line in run_measure())

    def test_oq1_counts_only_kiro_profile_events(self):
        self.assertEqual(self.lines["OQ-1 kiro profile data events"], "2")
        self.assertEqual(self.lines["OQ-1 eventName GenerateAssistantResponse"], "2")

    def test_oq2_ratio(self):
        self.assertEqual(self.lines["OQ-2 promptlog records with requestId"], "2")
        self.assertEqual(self.lines["OQ-2 of those also in CloudTrail"], "1")
        self.assertEqual(self.lines["OQ-2 match ratio"], "0.5")

    def test_oq3_shapes(self):
        self.assertEqual(self.lines["OQ-3 promptlog user id dot_at"], "12")
        self.assertEqual(self.lines["OQ-3 promptlog user id head2"], "d-")
        self.assertEqual(self.lines["OQ-3 cloudtrail user id dot_at"], "-1")
        self.assertEqual(self.lines["OQ-3 report user id length"], "36")
        self.assertEqual(self.lines["OQ-3 cloudtrail user id from userId"], "2")

    def test_oq4_error_window(self):
        self.assertEqual(self.lines["OQ-4 chat 2 trail events with errorCode"], "1")

    def test_oq9_delay(self):
        self.assertEqual(self.lines["OQ-9 records with timing"], "2")
        self.assertEqual(self.lines["OQ-9 delay minutes min"], "2.7")

    def test_unknown_record_is_counted(self):
        self.assertEqual(self.lines["record kinds unknown"], "1")


if __name__ == "__main__":
    unittest.main()
