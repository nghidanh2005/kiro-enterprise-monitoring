"""Tests for tools/reconcile.py (Requirement 16). Offline: fake Athena, Cost Explorer and STS."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import reconcile as rc  # noqa: E402

PAYER, KIRO, OBS, OTHER = "111122223333", "222233334444", "444455556666", "333344445555"


class FakeAthenaClient:
    """Answers by the first regex in `answers` that matches the SQL. 'MISSING' simulates no table."""

    def __init__(self, answers: list[tuple[str, object]]):
        self.answers = answers
        self.sql: list[str] = []

    def start_query_execution(self, QueryString, WorkGroup):
        self.sql.append(QueryString)
        return {"QueryExecutionId": str(len(self.sql) - 1)}

    def _answer(self, qid):
        q = self.sql[int(qid)]
        for pattern, ans in self.answers:
            if re.search(pattern, q, re.S):
                return ans
        raise AssertionError(f"no fake answer for: {q[:120]}")

    def get_query_execution(self, QueryExecutionId):
        if self._answer(QueryExecutionId) == "MISSING":
            return {"QueryExecution": {"Status": {"State": "FAILED", "StateChangeReason": "TABLE_NOT_FOUND: x"}}}
        return {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}

    def get_paginator(self, name):
        outer = self

        class P:
            def paginate(self, QueryExecutionId):
                rows = outer._answer(QueryExecutionId)
                data = [{"Data": [{"VarCharValue": "h"}]}] + [
                    {"Data": [{} if v is None else {"VarCharValue": str(v)} for v in r]} for r in rows]
                return [{"ResultSet": {"Rows": data}}]
        return P()


class FakeCE:
    def __init__(self, amounts: dict[str, float], services=("Kiro",)):
        self.amounts, self.services = amounts, services
        self.filters = []

    def get_cost_and_usage(self, TimePeriod, Granularity, Metrics, Filter):
        self.filters.append(Filter)
        return {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": str(self.amounts[TimePeriod["Start"][:7]])}}}]}

    def get_dimension_values(self, **kw):
        return {"DimensionValues": [{"Value": s} for s in self.services]}


def cfg():
    return {"slug": "acme", "display_name": "ACME", "accounts": {"payer": PAYER, "kiro": KIRO, "identity_center": KIRO},
            "profiles": {"payer": "p", "kiro": "k", "identity_center": "i"}}


def args(**over):
    a = rc.parse_args(["--customer", "acme", "--observability-account", OBS, "--workgroup", "kiro_acme",
                       "--months", "2026-09", "--end", "2026-10-27"])
    for k, v in over.items():
        setattr(a, k, v)
    return a


DEFAULT_ANSWERS = [
    (r"FROM cid_data_export\.cur2 WHERE source_account_id = .* billing_period = '2026-09'", [["100.00"]]),
    (r"count\(DISTINCT userid\) FROM optimization_data\.kiro_user_activity WHERE account_id", [["42"]]),
    (r"count\(DISTINCT userid\) FROM kiro_acme\.report_facts WHERE", [["42"]]),
    (r"FROM optimization_data\.kiro_user_activity WHERE account_id", [["900", "42", "2026-04-01", "2026-10-27"]]),
    (r"FROM kiro_acme\.report_facts$", [["900", "42", "2026-04-01", "2026-10-27"]]),
    (r"count\(DISTINCT line_item_resource_id\)", [["60", "40", "2026-08", "2026-10"]]),
    (r"FROM kiro_acme\.cloudtrail_kiro ct", "MISSING"),
    (r"FROM kiro_acme\.cloudtrail_kiro", "MISSING"),
    (r"kiro_acme\.prompt_metadata", "MISSING"),
    (r"FROM kiro_acme\.kiro_request_audit WHERE", [["200", "180"]]),
    (r"SELECT DISTINCT account_id FROM kiro_acme\.kiro_request_audit", [[KIRO]]),
]


def run_checks(answers=None, ce=None, **over):
    fake = FakeAthenaClient(answers or DEFAULT_ANSWERS)
    results = rc.run_checks(cfg(), rc.Athena(fake, "kiro_acme", poll=0), ce or FakeCE({"2026-09": 100.5}), args(**over))
    return {(r["check"], r["subject"]): r for r in results}, fake


class TestCur(unittest.TestCase):
    def test_within_one_percent_passes_and_names_both_tools(self):
        res, _ = run_checks()
        r = res[("cur", "2026-09")]
        self.assertEqual(r["verdict"], "PASS")
        self.assertIn("Cost Explorer", r["b_source"])
        self.assertIn("not the pipeline", r["b_source"])

    def test_two_percent_fails(self):
        res, _ = run_checks(ce=FakeCE({"2026-09": 102.1}))
        self.assertEqual(res[("cur", "2026-09")]["verdict"], "FAIL")

    def test_cost_explorer_filtered_to_kiro_service_and_account(self):
        ce = FakeCE({"2026-09": 100.0})
        run_checks(ce=ce)
        f = ce.filters[0]["And"]
        self.assertIn({"Dimensions": {"Key": "SERVICE", "Values": ["Kiro"]}}, f)
        self.assertIn({"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [KIRO]}}, f)

    def test_ambiguous_service_name_stops(self):
        with self.assertRaises(rc.Stop):
            rc.ce_service_name(FakeCE({}, services=("Kiro", "Kiro Pro")), ["2026-09"])
        with self.assertRaises(rc.Stop):
            rc.ce_service_name(FakeCE({}, services=()), ["2026-09"])


class TestUsersAndBaseline(unittest.TestCase):
    def test_equal_distinct_users_pass_and_use_the_same_dates(self):
        res, fake = run_checks()
        self.assertEqual(res[("users", "2026-09-28..2026-10-27")]["verdict"], "PASS")
        windows = [re.search(r"BETWEEN DATE '([\d-]+)' AND DATE '([\d-]+)'", q).groups()
                   for q in fake.sql if "count(DISTINCT userid)" in q and "BETWEEN" in q]
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0], windows[1])

    def test_different_counts_fail(self):
        answers = [(r"FROM kiro_acme\.report_facts WHERE", [["41"]])] + DEFAULT_ANSWERS
        res, _ = run_checks(answers=answers)
        self.assertEqual(res[("users", "2026-09-28..2026-10-27")]["verdict"], "FAIL")

    def test_missing_layers_skip(self):
        res, _ = run_checks()
        self.assertEqual(res[("baseline", "CloudTrail")]["verdict"], "SKIP")
        self.assertEqual(res[("promptmeta", "last 30 days")]["verdict"], "SKIP")
        self.assertEqual(res[("baseline", "CID report")]["a"]["earliest"], "2026-04-01")


class TestRatiosAndIsolation(unittest.TestCase):
    def test_identity_ratio(self):
        res, _ = run_checks()
        self.assertEqual(res[("identity", "last 30 days")]["a"], {"requests": 200, "joined": 180, "ratio": 0.9})

    def test_foreign_account_in_audit_view_fails(self):
        answers = [(r"SELECT DISTINCT account_id FROM kiro_acme\.kiro_request_audit", [[KIRO], [OTHER]])] + DEFAULT_ANSWERS
        res, _ = run_checks(answers=answers)
        self.assertEqual(res[("isolation", "audit view")]["verdict"], "FAIL")

    def test_own_account_only_passes(self):
        res, _ = run_checks()
        self.assertEqual(res[("isolation", "audit view")]["verdict"], "PASS")


class FakeSession:
    def __init__(self, account, log):
        self.account, self.log = account, log

    def client(self, service, **kw):
        self.log.append(service)
        if service == "sts":
            s = mock.Mock()
            s.get_caller_identity.return_value = {"Account": self.account}
            return s
        if service == "athena":
            return FakeAthenaClient(DEFAULT_ANSWERS)
        if service == "ce":
            return FakeCE({"2026-09": 100.2})
        raise AssertionError(service)


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        (self.repo / "customers" / "acme").mkdir(parents=True)
        (self.repo / "customers" / "acme" / "config.json").write_text(json.dumps(cfg()), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_wrong_observability_profile_stops_before_any_query(self):
        log: list = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rc.run(self.repo, args(), lambda p, r: FakeSession(OTHER, log))
        self.assertEqual(code, 2)
        self.assertEqual(log, ["sts"])
        self.assertFalse((self.repo / "customers" / "acme" / "BASELINE.md").exists())

    def test_full_run_writes_baseline_and_keeps_ids_off_stdout(self):
        accounts = {None: OBS, "p": PAYER}
        log: list = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rc.run(self.repo, args(), lambda p, r: FakeSession(accounts[p], log))
        self.assertEqual(code, 0)
        md = (self.repo / "customers" / "acme" / "BASELINE.md").read_text(encoding="utf-8")
        self.assertIn("Cost Explorer", md)
        self.assertIn("| cur | 2026-09 |", md)
        for account in (PAYER, KIRO, OBS):
            self.assertNotIn(account, out.getvalue())


if __name__ == "__main__":
    unittest.main()
