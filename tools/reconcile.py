#!/usr/bin/env python3
"""Reconcile dashboard numbers with a second, independent source (Requirement 16).

    python tools/reconcile.py --customer <slug> --observability-account <12 digits> \
        --observability-profile <p> --workgroup kiro_<slug> --months 2026-09 2026-10 --days 30

Writes customers/<slug>/BASELINE.md and customers/<slug>/baseline/<date>.json.
Stdout carries verdicts and numbers only, never account IDs or user IDs.

Checks (each names the tool behind every number, Requirement 16.6):
  cur        Kiro cost per month: Athena on the CID CUR table  vs  Cost Explorer in the payer account (task 4.12)
  users      distinct users over the same dates: CID table  vs  sample report_facts (task 5.9)
  baseline   rows, distinct users, earliest and latest date of every layer (Requirement 16.1)
  identity   share of CloudTrail requests resolved to a user label (task 8.3, 16.4)
  promptmeta share of CloudTrail requests that have prompt metadata (task 8.3, 16.5)
  isolation  distinct account_id per customer dataset equals the customer's accounts (task 8.4, 13.4)

A missing table (layer not deployed) makes its check SKIP, not FAIL.
Before any query, every profile is checked against the expected account and the
run stops on a mismatch, like tools/survey_customer.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time
from typing import Any, Callable

RE_ACCOUNT = re.compile(r"^[0-9]{12}$")
RE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,254}$")
RE_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
CUR_TOLERANCE = 0.01
CE_REGION = "us-east-1"
ALL_CHECKS = ("cur", "users", "baseline", "identity", "promptmeta", "isolation")


class Stop(Exception):
    pass


class TableMissing(Exception):
    pass


def ident(v: str) -> str:
    if not RE_IDENT.match(v):
        raise Stop(f"{v!r} is not a plain Athena identifier")
    return v


def acct(v: str) -> str:
    if not RE_ACCOUNT.match(str(v)):
        raise Stop("account IDs must be 12 digits")
    return v


# --------------------------------------------------------------------------- data sources

class Athena:
    """Runs one query and returns data rows as lists of strings (header dropped)."""

    def __init__(self, client, workgroup: str, poll: float = 1.0):
        self.c, self.wg, self.poll = client, workgroup, poll

    def query(self, sql: str) -> list[list[str | None]]:
        qid = self.c.start_query_execution(QueryString=sql, WorkGroup=self.wg)["QueryExecutionId"]
        while True:
            st = self.c.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            if st["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(self.poll)
        if st["State"] != "SUCCEEDED":
            reason = st.get("StateChangeReason", "")
            if "TABLE_NOT_FOUND" in reason or "does not exist" in reason or "not found" in reason.lower():
                raise TableMissing(reason[:120])
            raise RuntimeError(f"Athena {st['State']}: {reason[:200]}")
        rows: list[list[str | None]] = []
        for page in self.c.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
            rows.extend([d.get("VarCharValue") for d in r["Data"]] for r in page["ResultSet"]["Rows"])
        return rows[1:]


def ce_kiro_cost(ce, kiro_account: str, month: str, service: str) -> float:
    start = dt.date.fromisoformat(month + "-01")
    end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        Filter={"And": [
            {"Dimensions": {"Key": "SERVICE", "Values": [service]}},
            {"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [kiro_account]}},
        ]},
    )
    return sum(float(r["Total"]["UnblendedCost"]["Amount"]) for r in resp["ResultsByTime"])


def ce_service_name(ce, months: list[str]) -> str:
    """Cost Explorer's SERVICE value for Kiro, found by search instead of assumed."""
    start = min(months) + "-01"
    last = dt.date.fromisoformat(max(months) + "-01")
    end = (last.replace(day=28) + dt.timedelta(days=4)).replace(day=1).isoformat()
    vals = [v["Value"] for v in ce.get_dimension_values(
        TimePeriod={"Start": start, "End": end}, Dimension="SERVICE", SearchString="Kiro")["DimensionValues"]]
    if len(vals) != 1:
        raise Stop(f"Cost Explorer has {len(vals)} SERVICE values matching 'Kiro' ({vals}); pass --ce-service")
    return vals[0]


# --------------------------------------------------------------------------- checks

def result(check: str, subject: str, a: Any, a_src: str, b: Any = None, b_src: str = "", verdict: str = "INFO",
           note: str = "") -> dict:
    return {"check": check, "subject": subject, "a": a, "a_source": a_src, "b": b, "b_source": b_src,
            "verdict": verdict, "note": note}


def one(rows) -> list:
    return rows[0] if rows else []


def check_cur(athena: Athena, ce, cfg: dict, cur_db: str, months: list[str], service: str | None) -> list[dict]:
    service = service or ce_service_name(ce, months)
    out = []
    for m in months:
        sql = (f"SELECT sum(line_item_unblended_cost) FROM {cur_db}.cur2 "
               f"WHERE source_account_id = '{cfg['accounts']['payer']}' AND billing_period = '{m}' "
               f"AND line_item_product_code = 'Kiro' AND line_item_usage_account_id = '{cfg['accounts']['kiro']}'")
        a = float(one(athena.query(sql))[0] or 0)
        b = ce_kiro_cost(ce, cfg["accounts"]["kiro"], m, service)
        if a == 0 and b == 0:
            verdict, note = "PASS", "both zero"
        else:
            diff = abs(a - b) / max(abs(b), 1e-9)
            verdict, note = ("PASS" if diff < CUR_TOLERANCE else "FAIL"), f"diff {diff:.2%} (limit 1%)"
        out.append(result("cur", m, round(a, 4), "Athena, CID CUR table (pipeline)", round(b, 4),
                          f"Cost Explorer API in the payer account, SERVICE '{service}' (not the pipeline)", verdict, note))
    return out


def check_users(athena: Athena, cfg: dict, cid_db: str, sample_db: str, start: dt.date, end: dt.date) -> list[dict]:
    rng = f"TRY(CAST(date AS date)) BETWEEN DATE '{start}' AND DATE '{end}'"
    a = int(one(athena.query(
        f"SELECT count(DISTINCT userid) FROM {cid_db}.kiro_user_activity "
        f"WHERE account_id = '{cfg['accounts']['kiro']}' AND {rng}"))[0] or 0)
    b = int(one(athena.query(f"SELECT count(DISTINCT userid) FROM {sample_db}.report_facts WHERE {rng}"))[0] or 0)
    return [result("users", f"{start}..{end}", a, "Athena, CID kiro_user_activity (collector copy)", b,
                   "Athena, sample report_facts (normalizer copy)", "PASS" if a == b else "FAIL")]


def check_baseline(athena: Athena, cfg: dict, cid_db: str, sample_db: str, cur_db: str) -> list[dict]:
    k, p = cfg["accounts"]["kiro"], cfg["accounts"]["payer"]
    layers = [
        ("CID report", f"SELECT count(*), count(DISTINCT userid), min(date), max(date) FROM {cid_db}.kiro_user_activity WHERE account_id = '{k}'"),
        ("sample report", f"SELECT count(*), count(DISTINCT userid), min(date), max(date) FROM {sample_db}.report_facts"),
        ("CUR Kiro lines", f"SELECT count(*), count(DISTINCT line_item_resource_id), min(billing_period), max(billing_period) FROM {cur_db}.cur2 WHERE source_account_id = '{p}' AND line_item_product_code = 'Kiro'"),
        ("CloudTrail", f"SELECT count(*), count(DISTINCT useridentity.onbehalfof.userid), min(substr(eventtime, 1, 10)), max(substr(eventtime, 1, 10)) FROM {sample_db}.cloudtrail_kiro"),
        ("prompt metadata", f"SELECT count(*), count(DISTINCT user_id), min(dt), max(dt) FROM {sample_db}.prompt_metadata"),
    ]
    out = []
    for name, sql in layers:
        try:
            rows, users, first, last = one(athena.query(sql))
        except TableMissing:
            out.append(result("baseline", name, None, "Athena", verdict="SKIP", note="table not deployed"))
            continue
        out.append(result("baseline", name, {"rows": int(rows or 0), "distinct_users": int(users or 0),
                                             "earliest": first, "latest": last},
                          f"Athena: {sql.split(' FROM ')[1].split(' ')[0]}"))
    return out


def ratio_check(athena: Athena, check: str, subject: str, sql: str, source: str) -> list[dict]:
    try:
        total, hit = (int(x or 0) for x in one(athena.query(sql)))
    except TableMissing:
        return [result(check, subject, None, source, verdict="SKIP", note="layer not deployed")]
    return [result(check, subject, {"requests": total, "joined": hit,
                                    "ratio": round(hit / total, 4) if total else None}, source)]


def check_isolation(athena: Athena, cfg: dict, sample_db: str) -> list[dict]:
    expected = {cfg["accounts"]["kiro"]}
    out = []
    for name, sql in (
        ("audit view", f"SELECT DISTINCT account_id FROM {sample_db}.kiro_request_audit"),
        ("prompt metadata", f"SELECT DISTINCT \"$path\" FROM {sample_db}.prompt_metadata"),
    ):
        try:
            rows = athena.query(sql)
        except TableMissing:
            out.append(result("isolation", name, None, "Athena", verdict="SKIP", note="layer not deployed"))
            continue
        if name == "prompt metadata":
            found = {m.group(1) for r in rows if r[0] and (m := re.search(r"account_id=(\d{12})/", r[0]))}
        else:
            found = {r[0] for r in rows if r[0]}
        ok = found <= expected
        out.append(result("isolation", name, len(found), "Athena, distinct account_id", len(expected),
                          "customers/<slug>/config.json", "PASS" if ok else "FAIL",
                          "no rows yet" if not found else ("" if ok else "foreign account present")))
    out.append(result("isolation", "sample report", None, "", verdict="INFO",
                      note="no account column; isolated by RAW_PREFIX pinned to the customer account (patch P1)"))
    return out


def run_checks(cfg: dict, athena: Athena, ce, args) -> list[dict]:
    sample_db = ident("kiro_" + cfg["slug"].replace("-", "_"))
    cid_db, cur_db = ident(args.cid_database), ident(args.cur_database)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today() - dt.timedelta(days=2)
    start = end - dt.timedelta(days=args.days - 1)
    checks = set(args.checks)
    out: list[dict] = []
    if "cur" in checks:
        try:
            out += check_cur(athena, ce, cfg, cur_db, args.months, args.ce_service)
        except TableMissing:
            out.append(result("cur", "all", None, "Athena", verdict="SKIP", note="CUR table not deployed"))
    if "users" in checks:
        try:
            out += check_users(athena, cfg, cid_db, sample_db, start, end)
        except TableMissing:
            out.append(result("users", f"{start}..{end}", None, "Athena", verdict="SKIP", note="CID or sample not deployed"))
    if "baseline" in checks:
        out += check_baseline(athena, cfg, cid_db, sample_db, cur_db)
    if "identity" in checks:
        out += ratio_check(athena, "identity", f"last {args.days} days",
                           f"SELECT count(*), count_if(user_label <> user_id) FROM {sample_db}.kiro_request_audit "
                           f"WHERE event_time >= current_timestamp - INTERVAL '{args.days}' DAY",
                           "Athena, kiro_request_audit (user_label resolved vs raw user_id)")
    if "promptmeta" in checks:
        out += ratio_check(athena, "promptmeta", f"last {args.days} days",
                           f"SELECT count(*), count_if(pm.request_id IS NOT NULL) FROM {sample_db}.cloudtrail_kiro ct "
                           f"LEFT JOIN (SELECT DISTINCT request_id FROM {sample_db}.prompt_metadata) pm "
                           f"ON pm.request_id = ct.requestid "
                           f"WHERE from_iso8601_timestamp(ct.eventtime) >= current_timestamp - INTERVAL '{args.days}' DAY",
                           "Athena, cloudtrail_kiro joined to prompt_metadata on request ID")
    if "isolation" in checks:
        out += check_isolation(athena, cfg, sample_db)
    return out


# --------------------------------------------------------------------------- output

def render(cfg: dict, results: list[dict], now: str) -> str:
    lines = [f"# Baseline: {cfg.get('display_name') or cfg['slug']}", "",
             f"Đo lúc {now} UTC bằng `tools/reconcile.py`. Mỗi số ghi rõ công cụ đo (Requirement 16.6).", "",
             "| Kiểm | Đối tượng | Số A | Nguồn A | Số B | Nguồn B | Kết quả | Ghi chú |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        a = json.dumps(r["a"], ensure_ascii=False) if isinstance(r["a"], dict) else ("" if r["a"] is None else r["a"])
        b = "" if r["b"] is None else r["b"]
        lines.append(f"| {r['check']} | {r['subject']} | {a} | {r['a_source']} | {b} | {r['b_source']} | {r['verdict']} | {r['note']} |")
    return "\n".join(lines) + "\n"


def summary_line(r: dict) -> str:
    """Stdout line: numbers and verdicts only."""
    a = r["a"]
    if isinstance(a, dict):
        a = ", ".join(f"{k}={v}" for k, v in a.items() if k in ("rows", "distinct_users", "requests", "joined", "ratio"))
    return f"{r['verdict']:5} {r['check']:10} {r['subject']:24} a={a} b={'' if r['b'] is None else r['b']} {r['note']}"


def check_identity(make_session: Callable[[str | None, str], Any], profile: str | None, region: str, expected: str, role: str):
    sess = make_session(profile, region)
    try:
        got = sess.client("sts", region_name=region).get_caller_identity()["Account"]
    except Exception as exc:  # noqa: BLE001
        raise Stop(f"{role}: sts:GetCallerIdentity failed ({type(exc).__name__}); log in to '{profile}' first") from exc
    if got != expected:
        raise Stop(f"{role}: profile '{profile}' is not the expected account")
    return sess


def run(repo: pathlib.Path, args, make_session) -> int:
    try:
        cfg = json.loads((repo / "customers" / args.customer / "config.json").read_text(encoding="utf-8"))
        for role in ("payer", "kiro"):
            acct(cfg["accounts"][role])
        acct(args.observability_account)
        for m in args.months:
            if not RE_MONTH.match(m):
                raise Stop(f"month {m!r} must be YYYY-MM")
        obs = check_identity(make_session, args.observability_profile, args.region, args.observability_account, "observability")
        ce = None
        if "cur" in args.checks:
            payer = check_identity(make_session, cfg["profiles"].get("payer") or None, CE_REGION,
                                   cfg["accounts"]["payer"], "payer")
            ce = payer.client("ce", region_name=CE_REGION)
        athena = Athena(obs.client("athena", region_name=args.region), args.workgroup)
        results = run_checks(cfg, athena, ce, args)
    except (Stop, FileNotFoundError, KeyError) as exc:
        print(f"STOP: {exc}")
        print("Nothing was written.")
        return 2
    now = dt.datetime.now(dt.timezone.utc)
    out = repo / "customers" / args.customer
    (out / "baseline").mkdir(parents=True, exist_ok=True)
    (out / "baseline" / f"{now:%Y-%m-%d}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "BASELINE.md").write_text(render(cfg, results, now.strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    for r in results:
        print(summary_line(r))
    return 1 if any(r["verdict"] == "FAIL" for r in results) else 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Reconcile dashboard numbers with independent sources.")
    ap.add_argument("--customer", required=True)
    ap.add_argument("--observability-account", required=True)
    ap.add_argument("--observability-profile")
    ap.add_argument("--region", default="ap-southeast-1")
    ap.add_argument("--workgroup", required=True)
    ap.add_argument("--cid-database", default="optimization_data")
    ap.add_argument("--cur-database", default="cid_data_export")
    ap.add_argument("--months", nargs="*", default=[])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--end", help="last day of the users window, default today - 2 (report lag)")
    ap.add_argument("--ce-service", help="Cost Explorer SERVICE value; found by search when omitted")
    ap.add_argument("--checks", nargs="*", default=list(ALL_CHECKS), choices=ALL_CHECKS)
    ap.add_argument("--repo", default=".")
    args = ap.parse_args(argv)
    if "cur" in args.checks and not args.months:
        args.checks = [c for c in args.checks if c != "cur"]
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    import boto3

    return run(pathlib.Path(args.repo).resolve(), args,
               lambda profile, region: boto3.Session(profile_name=profile, region_name=region))


if __name__ == "__main__":
    sys.exit(main())
