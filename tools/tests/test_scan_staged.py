"""Tests for tools/scan_staged.py. Offline; the integration test uses a temp git repo."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import scan_staged as ss  # noqa: E402

# A fake account ID assembled at runtime so this file does not trip the scanner.
FAKE_ACCOUNT = "2109" + "8765" + "4321"
FAKE_STORE = "d-" + "9a8b7c6d5e"
FAKE_SSOINS = "ssoins-" + "7223f0a1b2c3d4e5"


def kinds(text: str, path: str = "x.md", names: set[str] | None = None) -> list[str]:
    return [h.kind for h in ss.scan_text(path, text, names or set())]


class TestPatterns(unittest.TestCase):
    def test_account_id_positive(self):
        self.assertEqual(kinds(f"Account: {FAKE_ACCOUNT}"), ["account-id"])
        self.assertEqual(kinds(f"arn:aws:iam::{FAKE_ACCOUNT}:role/x"), ["account-id"])

    def test_account_id_negative(self):
        self.assertEqual(kinds("arn:aws:iam::123456789012:role/x"), [])
        self.assertEqual(kinds("1234567890123 is 13 digits, 12345678901 is 11"), [])

    def test_identity_store_positive(self):
        self.assertEqual(kinds(f"store {FAKE_STORE}"), ["identity-store"])
        self.assertEqual(kinds(f"userId {FAKE_STORE}.uuid"), ["identity-store"])

    def test_identity_store_negative(self):
        self.assertEqual(kinds("dang `d-xxxxxxxxxx.<uuid>` va d-1234567890"), [])
        self.assertEqual(kinds("build-deployment and ad-hoc-run"), [])

    def test_sso_instance_positive(self):
        self.assertEqual(kinds(f"arn:aws:sso:::instance/{FAKE_SSOINS}"), ["sso-instance"])

    def test_sso_instance_negative(self):
        self.assertEqual(kinds("NOT the instance ARN / ssoins- id"), [])

    def test_customer_name_positive(self):
        self.assertEqual(kinds("Bao cao cho ACME-Retail", names={"acme-retail"}), ["customer-name"])

    def test_customer_name_negative(self):
        self.assertEqual(kinds("acme-retailers is another word", names={"acme-retail"}), [])

    def test_em_dash_only_in_tf(self):
        line = 'description = "Trail bucket \u2014 customer"'
        self.assertEqual(kinds(line, path="main.tf"), ["em-dash"])
        self.assertEqual(kinds(line, path="notes.md"), [])
        self.assertEqual(kinds('description = "Trail bucket - customer"', path="main.tf"), [])

    def test_hit_is_masked(self):
        hit = ss.scan_text("x.md", FAKE_ACCOUNT, set())[0]
        self.assertNotIn(FAKE_ACCOUNT, str(hit))
        self.assertIn("len 12", str(hit))


class TestStagedIntegration(unittest.TestCase):
    def _git(self, repo, *args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    def test_seed_file_with_fake_account_exits_1_and_clean_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            self._git(repo, "init", "-q")
            (repo / "clean.md").write_text("placeholder 123456789012\n", encoding="utf-8")
            self._git(repo, "add", "clean.md")
            self.assertEqual(ss.main(["--repo", str(repo)]), 0)

            (repo / "seed.md").write_text(f"leaked {FAKE_ACCOUNT}\n", encoding="utf-8")
            self._git(repo, "add", "seed.md")
            self.assertEqual(ss.main(["--repo", str(repo)]), 1)

    def test_scans_index_not_working_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            self._git(repo, "init", "-q")
            f = repo / "a.md"
            f.write_text("clean\n", encoding="utf-8")
            self._git(repo, "add", "a.md")
            f.write_text(f"dirty {FAKE_ACCOUNT}\n", encoding="utf-8")  # unstaged edit
            self.assertEqual(ss.main(["--repo", str(repo)]), 0)

    def test_customer_names_come_from_config_and_skip_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            for slug in ("acme-retail", "_template"):
                d = repo / "customers" / slug
                d.mkdir(parents=True)
                (d / "config.json").write_text(
                    json.dumps({"slug": slug, "display_name": ""}), encoding="utf-8"
                )
            self.assertEqual(ss.load_customer_names(repo), {"acme-retail"})

    def test_binary_is_skipped(self):
        hits, scanned, skipped = ss.scan([("img.png", b"\x89PNG\0" + FAKE_ACCOUNT.encode())], set())
        self.assertEqual((hits, scanned, skipped), ([], 0, 1))

    def test_allowlist_is_scoped_to_path_kind_and_value(self):
        digest = ss.scan_text("a.py", FAKE_ACCOUNT, set())[0].digest[:16]
        allow = {("a.py", "account-id", digest)}
        files = [("a.py", FAKE_ACCOUNT.encode()), ("b.py", FAKE_ACCOUNT.encode())]
        hits, _, _ = ss.scan(files, set(), allow)
        self.assertEqual([h.path for h in hits], ["b.py"])

    def test_customer_name_cannot_be_allowlisted(self):
        digest = ss.scan_text("a.md", "acme-retail", {"acme-retail"})[0].digest[:16]
        hits, _, _ = ss.scan([("a.md", b"acme-retail")], {"acme-retail"},
                             {("a.md", "customer-name", digest)})
        self.assertEqual(len(hits), 1)

    def test_allowlist_file_rejects_raw_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "tools").mkdir()
            (repo / "tools" / "scan_allowlist.txt").write_text(
                f"a.py account-id {FAKE_ACCOUNT}\n", encoding="utf-8"
            )
            with self.assertRaises(SystemExit):
                ss.load_allowlist(repo)


if __name__ == "__main__":
    unittest.main()
