"""Tests for tools/build_audit_views.py (task 8.1). Offline; SQL is parsed with sqlglot when installed."""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import build_audit_views as bav  # noqa: E402

EXPECTED = [
    "event_time", "event_name", "request_id", "error_code", "error_message_present", "account_id",
    "region", "user_id", "user_label", "client", "conversation_id", "prompt_chars", "response_chars",
]
VARIANTS = [
    dict(identity_map=True, promptmeta=False, oq2_match=False),
    dict(identity_map=False, promptmeta=False, oq2_match=False),
    dict(identity_map=True, promptmeta=True, oq2_match=True),
    dict(identity_map=True, promptmeta=True, oq2_match=False),
]


class TestRender(unittest.TestCase):
    def test_output_columns_are_stable_and_never_forbidden(self):
        for v in VARIANTS:
            with self.subTest(**v):
                cols = bav.output_columns(bav.render("kiro_acme", **v))
                self.assertEqual(cols, EXPECTED)
                self.assertFalse(set(cols) & set(bav.FORBIDDEN_OUTPUT))

    def test_ip_and_raw_payload_columns_are_not_read_at_all(self):
        for v in VARIANTS:
            sql = bav.render("kiro_acme", **v).lower()
            for col in ("sourceipaddress", "requestparameters", "responseelements"):
                self.assertNotIn(col, sql)

    def test_user_agent_only_inside_the_client_extraction(self):
        sql = bav.render("kiro_acme")
        self.assertEqual(sql.count("useragent"), 1)
        self.assertIn("regexp_extract(ct.useragent", sql)

    def test_promptmeta_join_only_when_request_ids_match(self):
        self.assertIn("prompt_metadata", bav.render("kiro_acme", promptmeta=True, oq2_match=True))
        self.assertNotIn("prompt_metadata", bav.render("kiro_acme", promptmeta=True, oq2_match=False))

    def test_identity_map_join_follows_d8(self):
        self.assertIn("identity_map", bav.render("kiro_acme", identity_map=True))
        self.assertNotIn("identity_map", bav.render("kiro_acme", identity_map=False))

    def test_rejects_unsafe_identifiers_and_expressions(self):
        with self.assertRaises(SystemExit):
            bav.render("kiro_acme; drop table x")
        with self.assertRaises(SystemExit):
            bav.render("kiro_acme", user_id_expr="ct.sourceipaddress) --")

    def test_cli_prints_sql_without_apply(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(bav.main(["--slug", "acme-retail"]), 0)
        self.assertIn("CREATE OR REPLACE VIEW kiro_acme_retail.kiro_request_audit", out.getvalue())


try:
    import sqlglot
except ImportError:  # pragma: no cover
    sqlglot = None


@unittest.skipIf(sqlglot is None, "sqlglot not installed")
class TestParses(unittest.TestCase):
    def test_every_variant_parses_as_trino(self):
        for v in VARIANTS:
            with self.subTest(**v):
                sqlglot.parse_one(bav.render("kiro_acme", **v), read="trino")


if __name__ == "__main__":
    unittest.main()
