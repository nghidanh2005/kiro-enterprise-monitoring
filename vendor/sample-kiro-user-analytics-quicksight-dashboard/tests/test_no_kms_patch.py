"""Tests for patch P4 (kiro-enterprise-monitoring): no KMS key for the identity map.

P4  cfn/03-identity-mapping.yaml + scripts/deploy.sh + scripts/teardown.sh:
    the identity-map bucket is SSE-S3. Upstream created a dedicated CMK; P4
    removes it, the Lambda's KMS grant, the key output, the QuickSight
    kms:Decrypt wiring and the "key retained" teardown note.

Unlike P1-P3, P4 changes upstream behaviour unconditionally. The optional
SourceKmsKeyArn pass-through (a key the CUSTOMER owns on the report bucket) is
not a key this stack creates and stays as upstream wrote it.

Offline: no AWS, no network.
"""
from __future__ import annotations

import unittest

from _helpers import CFN, SCRIPTS
from test_crossaccount_patches import _cfn_load, _Tag, yaml

IDMAP_TEMPLATE = (CFN / "03-identity-mapping.yaml").read_text()
DEPLOY = (SCRIPTS / "deploy.sh").read_text()
TEARDOWN = (SCRIPTS / "teardown.sh").read_text()


def _actions(statement) -> list[str]:
    act = statement.get("Action", [])
    return [act] if isinstance(act, str) else list(act)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestP4Template(unittest.TestCase):
    def setUp(self):
        self.tpl = _cfn_load(IDMAP_TEMPLATE)
        self.res = self.tpl["Resources"]

    def test_stack_creates_no_kms_resource(self):
        kms = [name for name, r in self.res.items() if str(r["Type"]).startswith("AWS::KMS::")]
        self.assertEqual(kms, [])

    def test_identity_map_bucket_is_sse_s3(self):
        rules = self.res["IdentityMapBucket"]["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"]
        self.assertEqual(len(rules), 1)
        default = rules[0]["ServerSideEncryptionByDefault"]
        self.assertEqual(default, {"SSEAlgorithm": "AES256"})
        self.assertNotIn("BucketKeyEnabled", rules[0])

    def test_lambda_role_has_no_kms_grant_on_its_own_bucket(self):
        policies = self.res["IdentityMapLambdaRole"]["Properties"]["Policies"]
        names = []
        kms_actions = []
        for p in policies:
            if isinstance(p, _Tag):  # !If [cond, policy, NoValue]
                p = p.value[1]
            names.append(p["PolicyName"])
            for st in p["PolicyDocument"]["Statement"]:
                kms_actions += [a for a in _actions(st) if a.startswith("kms:")]
        self.assertNotIn("KmsForIdentityMap", names)
        # Only the optional customer-key decrypt (DecryptSourceData) may remain.
        self.assertEqual(kms_actions, ["kms:Decrypt"])

    def test_key_output_removed(self):
        self.assertNotIn("IdentityMapKeyArn", self.tpl["Outputs"])


class TestP4Scripts(unittest.TestCase):
    def test_deploy_reads_no_key_output(self):
        self.assertNotIn("IdentityMapKeyArn", DEPLOY)
        self.assertNotIn("IDMAP_KEY_ARN=", DEPLOY)

    def test_quicksight_grant_has_no_identity_map_key(self):
        block = DEPLOY[DEPLOY.index('if [[ -n "${IDMAP_BUCKET}" ]]; then\n    QS_S3_BUCKET_SPECS'):]
        block = block[: block.index("fi\n")]
        self.assertIn('QS_S3_BUCKET_SPECS+=("${IDMAP_BUCKET}:read")', block)
        self.assertNotIn("--kms-key-arn", block)

    def test_teardown_has_no_retained_key_note(self):
        self.assertNotIn("identity-map KMS key is retained", TEARDOWN)
        self.assertNotIn("schedule-key-deletion", TEARDOWN)


if __name__ == "__main__":
    unittest.main()
