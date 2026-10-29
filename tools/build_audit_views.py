#!/usr/bin/env python3
"""Build the audit view kiro_<slug>.kiro_request_audit (task 8.1).

    python tools/build_audit_views.py --slug acme                       # print SQL only
    python tools/build_audit_views.py --slug acme --promptmeta --oq2-match \
        --apply --workgroup kiro_acme --region ap-southeast-1 --profile <p>

CREATE OR REPLACE VIEW, so running it again gives the same view.

Sources (design.md 3.6):
  cloudtrail_kiro     layer 4 table (infra/observability/trail-bucket)
  identity_map        the sample's identity-map table (user_id, username, display_name, email)
  report_facts        the sample's normalized report (userid, email), fallback label
  prompt_metadata     layer 5b table, only with --promptmeta

The view never selects sourceipaddress, requestparameters, responseelements,
errormessage or the full useragent. The user ID field
useridentity.onbehalfof.userid follows the IDE event example in AWS re:Post
(log-amazonq-developer-events); OQ-3 confirms it for Kiro, and --user-id-expr
overrides it.
"""
from __future__ import annotations

import argparse
import re
import sys
import time

FORBIDDEN_OUTPUT = ("sourceipaddress", "requestparameters", "responseelements", "errormessage", "useragent")
RE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,254}$")


def _ident(name: str, what: str) -> str:
    if not RE_IDENT.match(name):
        raise SystemExit(f"{what} {name!r} is not a plain lowercase Athena identifier")
    return name


def render(database: str, *, identity_map: bool = True, promptmeta: bool = False, oq2_match: bool = False,
           user_id_expr: str = "ct.useridentity.onbehalfof.userid") -> str:
    db = _ident(database, "database")
    if not re.fullmatch(r"ct\.[a-z0-9_.]+", user_id_expr):
        raise SystemExit("--user-id-expr must be a ct.<field>[.<field>] path")
    joins, label_parts, meta_cols = [], [], []
    if identity_map:
        # Column names and the one-row-per-user collapse follow the sample's own
        # identity_map table (scripts/build_views.py lines 277-280 and 352-365).
        joins.append(
            "LEFT JOIN (SELECT idc_user_id, max(idc_username) AS idc_username, "
            f"max(idc_display_name) AS idc_display_name FROM {db}.identity_map GROUP BY idc_user_id) im "
            "ON im.idc_user_id = base.user_id"
        )
        label_parts += ["NULLIF(im.idc_display_name, '')", "NULLIF(im.idc_username, '')"]
    joins.append(
        "LEFT JOIN (SELECT userid, max(NULLIF(email, '')) AS email "
        f"FROM {db}.report_facts GROUP BY userid) rf ON rf.userid = base.user_id"
    )
    label_parts.append("rf.email")
    if promptmeta and oq2_match:
        joins.append(
            "LEFT JOIN (SELECT request_id, max(conversation_id) AS conversation_id, "
            "max(prompt_chars) AS prompt_chars, max(response_chars) AS response_chars "
            f"FROM {db}.prompt_metadata GROUP BY request_id) pm ON pm.request_id = base.request_id"
        )
        meta_cols = ["pm.conversation_id", "pm.prompt_chars", "pm.response_chars"]
    else:
        meta_cols = ["CAST(NULL AS varchar) AS conversation_id", "CAST(NULL AS integer) AS prompt_chars",
                     "CAST(NULL AS integer) AS response_chars"]
    label = "COALESCE(" + ", ".join(label_parts + ["base.user_id"]) + ")"
    join_sql = "\n".join(joins)
    meta_sql = ",\n  ".join(meta_cols)
    # The last product token of the user agent (e.g. AmazonQ-For-VSCode/1.23.0,
    # or app/AmazonQ-For-CLI) identifies the client without exposing the rest.
    return f"""CREATE OR REPLACE VIEW {db}.kiro_request_audit AS
WITH base AS (
  SELECT
    from_iso8601_timestamp(ct.eventtime) AS event_time,
    ct.eventname AS event_name,
    ct.requestid AS request_id,
    ct.errorcode AS error_code,
    ct.errormessage IS NOT NULL AS error_message_present,
    ct.recipientaccountid AS account_id,
    ct.awsregion AS region,
    {user_id_expr} AS user_id,
    regexp_replace(regexp_extract(ct.useragent, '(\\S+)\\s*$', 1), '^app/', '') AS client
  FROM {db}.cloudtrail_kiro ct
)
SELECT
  base.event_time,
  base.event_name,
  base.request_id,
  base.error_code,
  base.error_message_present,
  base.account_id,
  base.region,
  base.user_id,
  {label} AS user_label,
  base.client,
  {meta_sql}
FROM base
{join_sql}"""


def output_columns(sql: str) -> list[str]:
    """Column names of the outer SELECT (text after the last 'AS' or the last dotted part)."""
    outer = sql.split("\nSELECT\n", 1)[1].split("\nFROM base", 1)[0]
    cols = []
    depth, cur = 0, ""
    for ch in outer:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            cols.append(cur)
            cur = ""
        else:
            cur += ch
    cols.append(cur)
    names = []
    for c in cols:
        c = c.strip()
        m = re.search(r"\bAS\s+([a-z_][a-z0-9_]*)\s*$", c, re.IGNORECASE)
        names.append(m.group(1) if m else c.split(".")[-1])
    return names


def apply(sql: str, *, database: str, workgroup: str, region: str, profile: str | None) -> int:
    import boto3

    athena = boto3.Session(profile_name=profile, region_name=region).client("athena")
    qid = athena.start_query_execution(QueryString=sql, QueryExecutionContext={"Database": database},
                                       WorkGroup=workgroup)["QueryExecutionId"]
    while True:
        st = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if st["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    print(f"view create: {st['State']}")
    if st["State"] != "SUCCEEDED":
        print(f"reason: {st.get('StateChangeReason', '')[:300]}")
        return 1
    qid = athena.start_query_execution(QueryString=f"SELECT count(*) FROM {database}.kiro_request_audit",
                                       QueryExecutionContext={"Database": database}, WorkGroup=workgroup)["QueryExecutionId"]
    while athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"] in ("QUEUED", "RUNNING"):
        time.sleep(1)
    rows = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
    print(f"rows in view: {rows[1]['Data'][0]['VarCharValue'] if len(rows) > 1 else 'unknown'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render the Kiro request audit view.")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--no-identity-map", action="store_true", help="D8 = no")
    ap.add_argument("--promptmeta", action="store_true", help="D4 = yes")
    ap.add_argument("--oq2-match", action="store_true", help="OQ-2 showed request IDs match")
    ap.add_argument("--user-id-expr", default="ct.useridentity.onbehalfof.userid")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--workgroup")
    ap.add_argument("--region", default="ap-southeast-1")
    ap.add_argument("--profile")
    args = ap.parse_args(argv)

    database = "kiro_" + args.slug.replace("-", "_")
    sql = render(database, identity_map=not args.no_identity_map, promptmeta=args.promptmeta,
                 oq2_match=args.oq2_match, user_id_expr=args.user_id_expr)
    bad = [c for c in output_columns(sql) if c in FORBIDDEN_OUTPUT]
    if bad:
        raise SystemExit(f"refusing: view would expose {bad}")
    if not args.apply:
        print(sql)
        return 0
    if not args.workgroup:
        raise SystemExit("--apply needs --workgroup")
    return apply(sql, database=database, workgroup=args.workgroup, region=args.region, profile=args.profile)


if __name__ == "__main__":
    sys.exit(main())
