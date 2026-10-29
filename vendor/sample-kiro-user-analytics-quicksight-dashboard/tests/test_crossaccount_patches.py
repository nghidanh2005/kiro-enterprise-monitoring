"""Tests for the cross-account patches P1-P3 (kiro-enterprise-monitoring).

P1  cfn/01-data-layer.yaml + scripts/deploy.sh: RAW_PREFIX may name another
    account (SourceAccountId / KIRO_SOURCE_ACCOUNT_ID). Empty keeps upstream
    behaviour: the deploying account.
P2  scripts/preflight.sh: the bucket probe becomes a WARN when the report
    belongs to another account (the deployer has no list permission by design).
P3  scripts/identity_map_lambda.py + cfn/03-identity-mapping.yaml: ExternalId
    is sent on assume_role when IDC_EXTERNAL_ID is set, and only then.

Offline: no AWS, no network. Every patch must keep upstream behaviour when its
new input is empty, so each test checks both cases.
"""
from __future__ import annotations

import os
import re
import unittest
from unittest import mock

from _helpers import CFN, SCRIPTS, load

try:
    import yaml  # PyYAML arrives with cfn-lint in CI; optional here.
except ImportError:  # pragma: no cover
    yaml = None

DATA_LAYER = (CFN / "01-data-layer.yaml").read_text()
IDMAP_TEMPLATE = (CFN / "03-identity-mapping.yaml").read_text()
DEPLOY = (SCRIPTS / "deploy.sh").read_text()
PREFLIGHT = (SCRIPTS / "preflight.sh").read_text()


class _Tag:
    def __init__(self, tag: str, value):
        self.tag = tag
        self.value = value


def _cfn_load(text: str):
    class Loader(yaml.SafeLoader):
        pass

    def construct(loader, suffix, node):
        if isinstance(node, yaml.ScalarNode):
            value = loader.construct_scalar(node)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node, deep=True)
        else:
            value = loader.construct_mapping(node, deep=True)
        return _Tag(suffix, value)

    Loader.add_multi_constructor("!", construct)
    return yaml.load(text, Loader=Loader)  # noqa: S506 - SafeLoader subclass


def _resolve(node, params: dict, conditions: dict):
    """Evaluate the few intrinsics RAW_PREFIX uses: Sub, If, Ref."""
    if isinstance(node, str):
        return node
    if not isinstance(node, _Tag):
        raise AssertionError(f"unexpected node {node!r}")
    if node.tag == "Ref":
        return params[node.value]
    if node.tag == "If":
        cond, yes, no = node.value
        return _resolve(yes if conditions[cond] else no, params, conditions)
    if node.tag == "Sub":
        if isinstance(node.value, list):
            template, local = node.value
            local = {k: _resolve(v, params, conditions) for k, v in local.items()}
        else:
            template, local = node.value, {}
        scope = {**params, **local}
        return re.sub(r"\$\{([^}]+)\}", lambda m: scope[m.group(1)], template)
    raise AssertionError(f"intrinsic !{node.tag} not handled by this test")


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestP1RawPrefix(unittest.TestCase):
    def setUp(self):
        self.tpl = _cfn_load(DATA_LAYER)
        fn = self.tpl["Resources"]["NormalizerLambda"]["Properties"]
        self.raw_prefix = fn["Environment"]["Variables"]["RAW_PREFIX"]

    def _render(self, source_account: str) -> str:
        params = {
            "KiroLogsPrefix": "kiro/",
            "SourceAccountId": source_account,
            "AWS::AccountId": "111122223333",
        }
        cond = self.tpl["Conditions"]["HasSourceAccount"]
        # HasSourceAccount: !Not [!Equals [!Ref SourceAccountId, ""]]
        self.assertEqual(cond.tag, "Not")
        equals = cond.value[0]
        self.assertEqual(equals.tag, "Equals")
        has = _resolve(equals.value[0], params, {}) != equals.value[1]
        return _resolve(self.raw_prefix, params, {"HasSourceAccount": has})

    def test_empty_source_account_keeps_upstream_path(self):
        self.assertEqual(
            self._render(""),
            "kiro/AWSLogs/111122223333/KiroLogs/user_report/",
        )

    def test_source_account_replaces_deploying_account(self):
        self.assertEqual(
            self._render("444455556666"),
            "kiro/AWSLogs/444455556666/KiroLogs/user_report/",
        )

    def test_parameter_is_optional_and_validated(self):
        p = self.tpl["Parameters"]["SourceAccountId"]
        self.assertEqual(p["Default"], "")
        pattern = p["AllowedPattern"]
        self.assertTrue(re.fullmatch(pattern, ""))
        self.assertTrue(re.fullmatch(pattern, "444455556666"))
        self.assertFalse(re.fullmatch(pattern, "44445555666"))
        self.assertFalse(re.fullmatch(pattern, "arn:aws:iam::444455556666:root"))

    def test_single_conditions_block(self):
        self.assertEqual(len(re.findall(r"(?m)^Conditions:", DATA_LAYER)), 1)


class TestP1DeployScript(unittest.TestCase):
    def test_env_var_defaults_to_empty(self):
        self.assertIn('KIRO_SOURCE_ACCOUNT_ID="${KIRO_SOURCE_ACCOUNT_ID:-}"', DEPLOY)

    def test_every_data_stack_deploy_passes_source_account(self):
        blocks = re.findall(
            r'--template-file "\$\{ROOT\}/cfn/01-data-layer\.yaml".*?\n\n',
            DEPLOY,
            flags=re.S,
        )
        self.assertEqual(len(blocks), 2, "upstream deploys the data stack twice")
        for block in blocks:
            self.assertIn('"SourceAccountId=${KIRO_SOURCE_ACCOUNT_ID}"', block)

    def test_prefix_probe_falls_back_to_caller_account(self):
        body = re.search(r"probe_prefix\(\) \{(.*?)\n\}", DEPLOY, flags=re.S).group(1)
        self.assertIn("AWSLogs/${KIRO_SOURCE_ACCOUNT_ID:-$ACCOUNT_ID}/KiroLogs", body)


class TestP2Preflight(unittest.TestCase):
    def test_cross_account_branch_warns_before_probing(self):
        branch = PREFLIGHT.index('if [[ -n "${KIRO_SOURCE_ACCOUNT_ID}" && "${KIRO_SOURCE_ACCOUNT_ID}" != "${ACCOUNT_ID}" ]]; then')
        probe = PREFLIGHT.index('elif [[ -n "${KIRO_LOGS_BUCKET}" && -n "${ACCOUNT_ID}" ]]; then')
        self.assertLess(branch, probe)
        between = PREFLIGHT[branch:probe]
        self.assertIn("warn ", between)
        self.assertNotIn("fail ", between)

    def test_same_account_keeps_upstream_fail(self):
        self.assertIn('fail "user_report not found in s3://${KIRO_LOGS_BUCKET}/.', PREFLIGHT)


class TestP3ExternalId(unittest.TestCase):
    def setUp(self):
        self.mod = load("identity_map_lambda")
        self.sts = mock.Mock()
        self.sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "AKIDEXAMPLE",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
            }
        }

        def fake_client(service, **_kwargs):
            return self.sts if service == "sts" else mock.Mock()

        self.patcher = mock.patch.object(self.mod.boto3, "client", side_effect=fake_client)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _call(self, env: dict):
        base = {
            "IDC_REGION": "us-east-1",
            "IDC_ROLE_ARN": "arn:aws:iam::444455556666:role/idc-read",
        }
        with mock.patch.dict(os.environ, {**base, **env}, clear=False):
            if "IDC_EXTERNAL_ID" not in env:
                os.environ.pop("IDC_EXTERNAL_ID", None)
            self.mod._idc_client()
        return self.sts.assume_role.call_args.kwargs

    def test_external_id_sent_when_set(self):
        kwargs = self._call({"IDC_EXTERNAL_ID": "kiro-mon-example-ab12cd34"})
        self.assertEqual(kwargs["ExternalId"], "kiro-mon-example-ab12cd34")
        self.assertEqual(kwargs["RoleSessionName"], "kiro-identity-map")

    def test_external_id_omitted_when_empty(self):
        kwargs = self._call({"IDC_EXTERNAL_ID": "  "})
        self.assertNotIn("ExternalId", kwargs)

    def test_external_id_omitted_when_unset(self):
        kwargs = self._call({})
        self.assertNotIn("ExternalId", kwargs)

    def test_template_wires_parameter_to_env(self):
        self.assertRegex(IDMAP_TEMPLATE, r'(?m)^  IdcExternalId:\n    Type: String\n    Default: ""')
        self.assertIn("IDC_EXTERNAL_ID: !Ref IdcExternalId", IDMAP_TEMPLATE)
        self.assertIn('"IdcExternalId=${IDC_EXTERNAL_ID}"', DEPLOY)


if __name__ == "__main__":
    unittest.main()
