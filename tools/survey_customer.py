#!/usr/bin/env python3
"""Read-only survey of a customer's accounts (task 2.1 / 2.2).

    python tools/survey_customer.py --customer <slug>

Reads customers/<slug>/config.json, then:

1. Validates the config. Any bad field stops the run before AWS is called.
2. For each role (kiro, payer, identity_center) calls sts:GetCallerIdentity with
   that role's CLI profile and compares the account with the config. Any
   mismatch stops the run BEFORE any other AWS call and before any file is
   written (exit 2).
3. Only then runs read-only calls and writes customers/<slug>/SURVEY.md plus
   raw configuration under customers/<slug>/survey/.

Never calls a write API. Never stores user names or emails: Identity Store
users are only counted. Stdout carries counts and verdicts only, no IDs, so the
run can be pasted into a chat without leaking customer identifiers.

The Kiro profile itself has no public API in boto3 or the AWS CLI (checked
2026-10-29), so its region and ARN are recorded as "read on the Kiro console".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from typing import Any, Callable

ROLES = ("kiro", "payer", "identity_center")
RE_ACCOUNT = re.compile(r"^[0-9]{12}$")
RE_SLUG = re.compile(r"^[a-z][a-z0-9-]{0,14}$")
RE_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]$")
REPORT_MARKER = "/KiroLogs/user_report/"
COMMON_PREFIXES = ("", "kiro/", "usage-activity/", "user-activity/", "kiro-logs/")
KIRO_RESOURCE_TYPE = "AWS::CodeWhisperer::Profile"
DATA_EXPORTS_REGION = "us-east-1"  # Data Exports and legacy CUR APIs live here


class Stop(Exception):
    """Raised to stop the survey before anything is written."""


# --------------------------------------------------------------------------- config

def load_config(repo: pathlib.Path, slug: str) -> dict:
    path = repo / "customers" / slug / "config.json"
    if not path.exists():
        raise Stop(f"config not found: customers/{slug}/config.json (copy customers/_template/)")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_config(cfg)
    if cfg.get("slug") != slug:
        errors.append("slug in config.json must equal the folder name")
    if errors:
        raise Stop("config invalid: " + "; ".join(errors))
    return cfg


def validate_config(cfg: dict) -> list[str]:
    errors = []
    if not RE_SLUG.match(str(cfg.get("slug", ""))):
        errors.append("slug must be lowercase letters/digits/hyphens, start with a letter, max 15")
    for role in ROLES:
        if not RE_ACCOUNT.match(str(cfg.get("accounts", {}).get(role, ""))):
            errors.append(f"accounts.{role} must be 12 digits")
    for key in ("kiro_profile", "identity_center"):
        if not RE_REGION.match(str(cfg.get("regions", {}).get(key, ""))):
            errors.append(f"regions.{key} must be a region code")
    if not str(cfg.get("report_bucket", "")).strip():
        errors.append("report_bucket is empty")
    prefix = str(cfg.get("report_prefix", ""))
    if prefix and not prefix.endswith("/"):
        errors.append("report_prefix must end with '/' (or be empty)")
    return errors


# --------------------------------------------------------------------------- helpers

def error_code(exc: Exception) -> str:
    resp = getattr(exc, "response", None) or {}
    return resp.get("Error", {}).get("Code") or type(exc).__name__


def safe(fn: Callable[..., Any], **kwargs) -> tuple[Any, str | None]:
    """Call fn; return (result, None) or (None, error code). Never raises."""
    try:
        return fn(**kwargs), None
    except Exception as exc:  # noqa: BLE001 - every failure is a survey finding
        return None, error_code(exc)


def parse_report_key(key: str) -> tuple[str, str, dt.date] | None:
    """(account, region, date) from .../AWSLogs/<acct>/KiroLogs/user_report/<region>/<yyyy>/<mm>/<dd>/..."""
    parts = key.split("/")
    try:
        acct = parts[parts.index("AWSLogs") + 1]
        r = parts.index("user_report")
        region, y, m, d = parts[r + 1], parts[r + 2], parts[r + 3], parts[r + 4]
        return acct, region, dt.date(int(y), int(m), int(d))
    except (ValueError, IndexError):
        return None


# --------------------------------------------------------------------------- identity guard

def check_identities(cfg: dict, make_session: Callable[[str | None, str], Any]) -> dict[str, Any]:
    """Return {role: session}. Raises Stop on the first mismatch, before any other call."""
    sessions: dict[str, Any] = {}
    region = cfg["regions"]["kiro_profile"]
    for role in ROLES:
        profile = (cfg.get("profiles", {}).get(role) or "").strip() or None
        # The region goes on the session: an `aws login` profile with no region
        # otherwise fails credential refresh with NoRegionError.
        sess = make_session(profile, region)
        sts = sess.client("sts", region_name=region)
        ident, err = safe(sts.get_caller_identity)
        if err:
            raise Stop(f"{role}: sts:GetCallerIdentity failed ({err}); log in to profile '{profile}' first")
        if ident["Account"] != cfg["accounts"][role]:
            raise Stop(f"{role}: profile '{profile}' is NOT the {role} account in config.json")
        sessions[role] = sess
    return sessions


# --------------------------------------------------------------------------- sections

def survey_report_bucket(s3, bucket: str, prefix: str, account: str) -> dict:
    """OQ-5: encryption, policy, versioning, and the earliest/latest report under each prefix."""
    out: dict[str, Any] = {"bucket": bucket, "configured_prefix": prefix}
    loc, err = safe(s3.get_bucket_location, Bucket=bucket)
    out["region"] = err or (loc.get("LocationConstraint") or "us-east-1")
    enc, err = safe(s3.get_bucket_encryption, Bucket=bucket)
    if err:
        out["encryption"] = err
    else:
        rules = enc["ServerSideEncryptionConfiguration"]["Rules"]
        out["encryption"] = [r["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] for r in rules]
        out["kms_key_set"] = any(
            r["ApplyServerSideEncryptionByDefault"].get("KMSMasterKeyID") for r in rules
        )
    pol, err = safe(s3.get_bucket_policy, Bucket=bucket)
    out["policy_raw"] = None if err else pol["Policy"]
    out["policy_error"] = err
    if not err:
        doc = json.loads(pol["Policy"])
        out["policy_sids"] = [s.get("Sid", "(no Sid)") for s in doc.get("Statement", [])]
        out["policy_service_principals"] = sorted({
            p for s in doc.get("Statement", [])
            for p in _as_list((s.get("Principal") or {}).get("Service") if isinstance(s.get("Principal"), dict) else None)
        })
    ver, err = safe(s3.get_bucket_versioning, Bucket=bucket)
    out["versioning"] = err or ver.get("Status", "Disabled")

    out["prefixes"] = {}
    candidates = list(dict.fromkeys([prefix, *COMMON_PREFIXES, *_top_level_prefixes(s3, bucket)]))
    for p in candidates:
        stats = _report_stats(s3, bucket, f"{p}AWSLogs/{account}/KiroLogs/user_report/")
        if stats["files"]:
            out["prefixes"][p] = stats
    return out


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _top_level_prefixes(s3, bucket: str) -> list[str]:
    res, err = safe(s3.list_objects_v2, Bucket=bucket, Delimiter="/")
    if err:
        return []
    return [cp["Prefix"] for cp in res.get("CommonPrefixes", [])]


def _report_stats(s3, bucket: str, key_prefix: str) -> dict:
    files = 0
    dates: list[dt.date] = []
    regions: set[str] = set()
    error = None
    try:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".csv") or REPORT_MARKER not in key:
                    continue
                parsed = parse_report_key(key)
                if not parsed:
                    continue
                files += 1
                regions.add(parsed[1])
                dates.append(parsed[2])
    except Exception as exc:  # noqa: BLE001
        error = error_code(exc)
    return {
        "files": files,
        "earliest": min(dates).isoformat() if dates else None,
        "latest": max(dates).isoformat() if dates else None,
        "regions": sorted(regions),
        "error": error,
    }


def survey_trails(ct_factory: Callable[[str], Any], home_region: str) -> list[dict]:
    ct = ct_factory(home_region)
    res, err = safe(ct.describe_trails, includeShadowTrails=True)
    if err:
        return [{"error": err}]
    seen, out = set(), []
    for t in res.get("trailList", []):
        arn = t["TrailARN"]
        if arn in seen:
            continue
        seen.add(arn)
        info = {
            "name": t["Name"],
            "home_region": t.get("HomeRegion"),
            "multi_region": t.get("IsMultiRegionTrail"),
            "organization_trail": t.get("IsOrganizationTrail"),
            "s3_bucket": t.get("S3BucketName"),
        }
        sel, err = safe(ct_factory(t.get("HomeRegion") or home_region).get_event_selectors, TrailName=arn)
        if err:
            info["selectors_error"] = err
        else:
            info.update(_summarize_selectors(sel))
        out.append(info)
    return out


def _summarize_selectors(sel: dict) -> dict:
    mgmt, types = False, set()
    for s in sel.get("EventSelectors") or []:
        mgmt = mgmt or s.get("IncludeManagementEvents", False)
        types.update(r["Type"] for r in s.get("DataResources", []))
    for a in sel.get("AdvancedEventSelectors") or []:
        fields = {f["Field"]: f for f in a.get("FieldSelectors", [])}
        cat = fields.get("eventCategory", {}).get("Equals", [])
        if "Management" in cat:
            mgmt = True
        types.update(fields.get("resources.type", {}).get("Equals", []))
    return {
        "management_events": mgmt,
        "data_event_types": sorted(types),
        "captures_kiro_profile": KIRO_RESOURCE_TYPE in types,
    }


def survey_payer(sess, account: str) -> dict:
    """OQ-6: CUR 2.0 exports, legacy CUR, organization."""
    out: dict[str, Any] = {}
    org = sess.client("organizations")
    res, err = safe(org.describe_organization)
    out["organization"] = (
        {"error": err} if err else {
            "management_account_is_this": res["Organization"]["MasterAccountId"] == account,
            "feature_set": res["Organization"].get("FeatureSet"),
        }
    )
    bcm = sess.client("bcm-data-exports", region_name=DATA_EXPORTS_REGION)
    res, err = safe(bcm.list_exports)
    exports = []
    if err:
        exports.append({"error": err})
    for e in (res or {}).get("Exports", []):
        detail, derr = safe(bcm.get_export, ExportArn=e["ExportArn"])
        status = e.get("ExportStatus", {})
        item = {
            "name": e.get("ExportName"),
            "status": status.get("StatusCode"),
            "created_at": _iso(status.get("CreatedAt")),
            "last_refreshed_at": _iso(status.get("LastRefreshedAt")),
        }
        if derr:
            item["detail_error"] = derr
        else:
            ex = detail["Export"]
            query = ex["DataQuery"]["QueryStatement"]
            tables = ex["DataQuery"].get("TableConfigurations", {})
            dest = ex["DestinationConfigurations"]["S3Destination"]
            item.update({
                "table": next(iter(tables), _from_table(query)),
                "include_resources": {t: c.get("INCLUDE_RESOURCES") for t, c in tables.items()},
                "selects_line_item_resource_id": "line_item_resource_id" in query,
                "has_where": bool(re.search(r"\bWHERE\b", query, re.IGNORECASE)),
                "dest_bucket": dest.get("S3Bucket"),
                "dest_prefix": dest.get("S3Prefix"),
                "dest_region": dest.get("S3Region"),
                "dest_bucket_owner": dest.get("S3BucketOwner"),
                "refresh": ex.get("RefreshCadence", {}).get("Frequency"),
            })
        exports.append(item)
    out["data_exports"] = exports
    cur = sess.client("cur", region_name=DATA_EXPORTS_REGION)
    res, err = safe(cur.describe_report_definitions)
    out["legacy_cur"] = [{"error": err}] if err else [
        {
            "name": r["ReportName"],
            "time_unit": r.get("TimeUnit"),
            "resources": "RESOURCES" in r.get("AdditionalSchemaElements", []),
            "s3_bucket": r.get("S3Bucket"),
            "s3_region": r.get("S3Region"),
        }
        for r in res.get("ReportDefinitions", [])
    ]
    return out


def _from_table(query: str) -> str | None:
    m = re.search(r"\bFROM\s+([A-Z_]+)", query, re.IGNORECASE)
    return m.group(1) if m else None


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)


def survey_identity_center(sess, region: str, account: str) -> dict:
    out: dict[str, Any] = {"region": region}
    sso = sess.client("sso-admin", region_name=region)
    res, err = safe(sso.list_instances)
    if err:
        return {**out, "error": err}
    out["instances"] = [
        {
            "identity_store_id": i.get("IdentityStoreId"),
            "owner_is_this_account": i.get("OwnerAccountId") == account,
            "status": i.get("Status"),
        }
        for i in res.get("Instances", [])
    ]
    ids = sess.client("identitystore", region_name=region)
    for inst in out["instances"]:
        inst["user_count"] = count_users(ids, inst["identity_store_id"])
    return out


def count_users(ids, store_id: str) -> int | str:
    """Count only. Each page is dropped as soon as it is counted."""
    n = 0
    try:
        for page in ids.get_paginator("list_users").paginate(IdentityStoreId=store_id):
            n += len(page.get("Users", []))
    except Exception as exc:  # noqa: BLE001
        return error_code(exc)
    return n


# --------------------------------------------------------------------------- report

def render(cfg: dict, data: dict, now: str) -> str:
    rb = data["report_bucket"]
    lines = [
        f"# Survey: {cfg['display_name'] or cfg['slug']}",
        "",
        f"Run at {now} UTC by `tools/survey_customer.py`. Read-only. Raw JSON in `survey/`.",
        "",
        "## Accounts (identity checked before any other call)",
        "",
        "| Role | Account | Profile |",
        "|---|---|---|",
    ]
    for role in ROLES:
        lines.append(f"| {role} | {cfg['accounts'][role]} | {cfg['profiles'].get(role) or '(default)'} |")
    lines += [
        "",
        "## Kiro profile",
        "",
        "- Region and ARN: read on the Kiro console (no public API).",
        f"- Region in config: `{cfg['regions']['kiro_profile']}`.",
        "",
        "## OQ-5: report bucket",
        "",
        f"- Bucket `{rb['bucket']}`, region `{rb['region']}`, versioning `{rb['versioning']}`.",
        f"- Encryption: `{rb['encryption']}`" + (", KMS key set" if rb.get("kms_key_set") else "") + ".",
        f"- Bucket policy: {'none or unreadable: ' + str(rb['policy_error']) if rb['policy_error'] else 'Sids ' + ', '.join(rb['policy_sids'])}"
        + (f"; service principals {', '.join(rb.get('policy_service_principals') or []) or 'none'}" if not rb["policy_error"] else "")
        + ".",
        "",
        "| Prefix | Files | Earliest | Latest | Regions | Error |",
        "|---|---|---|---|---|---|",
    ]
    if not rb["prefixes"]:
        lines.append("| (none found) | 0 | | | | |")
    for p, s in rb["prefixes"].items():
        lines.append(f"| `{p or '(root)'}` | {s['files']} | {s['earliest']} | {s['latest']} | {', '.join(s['regions'])} | {s['error'] or ''} |")
    other = [p for p in rb["prefixes"] if p != cfg["report_prefix"]]
    if other:
        lines += ["", f"- Reports exist under a prefix other than `{cfg['report_prefix']}`: "
                  + ", ".join(f"`{p or '(root)'}` (latest {rb['prefixes'][p]['latest']})" for p in other)
                  + ". CID and the sample only read `kiro/`."]
    lines += ["", "## CloudTrail trails in the Kiro account", ""]
    for t in data["trails"]:
        if "error" in t:
            lines.append(f"- describe-trails failed: {t['error']}")
            continue
        lines.append(
            f"- `{t['name']}` home `{t['home_region']}`, multi-region {t['multi_region']}, org {t['organization_trail']}, "
            f"management events {t.get('management_events')}, data event types {t.get('data_event_types')}, "
            f"captures {KIRO_RESOURCE_TYPE}: {t.get('captures_kiro_profile')}" + (f", selectors error {t['selectors_error']}" if t.get("selectors_error") else "")
        )
    if not data["trails"]:
        lines.append("- No trails.")
    payer = data["payer"]
    lines += ["", "## OQ-6: CUR in the payer account", "", f"- Organization: {payer['organization']}.", ""]
    lines += ["| Export | Status | Created | Table | INCLUDE_RESOURCES | resource_id selected | WHERE | Destination |", "|---|---|---|---|---|---|---|---|"]
    for e in payer["data_exports"]:
        if "error" in e:
            lines.append(f"| list-exports failed: {e['error']} | | | | | | | |")
            continue
        lines.append(
            f"| {e['name']} | {e['status']} | {e['created_at']} | {e.get('table')} | {e.get('include_resources')} | "
            f"{e.get('selects_line_item_resource_id')} | {e.get('has_where')} | "
            f"{e.get('dest_bucket')}/{e.get('dest_prefix')} ({e.get('dest_region')}) |"
        )
    if not payer["data_exports"]:
        lines.append("| (no CUR 2.0 export) | | | | | | | |")
    lines += ["", f"- Legacy CUR: {payer['legacy_cur'] or 'none'}.", ""]
    idc = data["identity_center"]
    lines += ["## Identity Center", ""]
    if "error" in idc:
        lines.append(f"- list-instances failed in `{idc['region']}`: {idc['error']}")
    for i in idc.get("instances", []):
        lines.append(
            f"- Store `{i['identity_store_id']}` in `{idc['region']}`, status {i['status']}, "
            f"owned by this account {i['owner_is_this_account']}, users (count only) {i['user_count']}."
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- main

def run(repo: pathlib.Path, slug: str, make_session: Callable[[str | None, str], Any]) -> int:
    try:
        cfg = load_config(repo, slug)
        sessions = check_identities(cfg, make_session)
    except Stop as exc:
        print(f"STOP: {exc}")
        print("Nothing was written and no other AWS call was made.")
        return 2

    kiro = sessions["kiro"]
    kiro_region = cfg["regions"]["kiro_profile"]
    s3 = kiro.client("s3", region_name=kiro_region)
    data = {
        "report_bucket": survey_report_bucket(s3, cfg["report_bucket"], cfg["report_prefix"], cfg["accounts"]["kiro"]),
        "trails": survey_trails(lambda r: kiro.client("cloudtrail", region_name=r), kiro_region),
        "payer": survey_payer(sessions["payer"], cfg["accounts"]["payer"]),
        "identity_center": survey_identity_center(
            sessions["identity_center"], cfg["regions"]["identity_center"], cfg["accounts"]["identity_center"]
        ),
    }

    out_dir = repo / "customers" / slug
    raw_dir = out_dir / "survey"
    raw_dir.mkdir(parents=True, exist_ok=True)
    policy = data["report_bucket"].pop("policy_raw")
    if policy:
        (raw_dir / "bucket-policy-report.json").write_text(
            json.dumps(json.loads(policy), indent=2) + "\n", encoding="utf-8"
        )
    (raw_dir / "survey.json").write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    (out_dir / "SURVEY.md").write_text(render(cfg, data, now), encoding="utf-8")

    rb = data["report_bucket"]
    print(f"OK: wrote customers/{slug}/SURVEY.md and survey/*.json")
    print(f"  report prefixes with files: {len(rb['prefixes'])}; encryption: {rb['encryption']}")
    print(f"  trails: {len(data['trails'])}; CUR 2.0 exports: {len(data['payer']['data_exports'])}; "
          f"legacy CUR: {len(data['payer']['legacy_cur'])}")
    print(f"  identity center instances: {len(data['identity_center'].get('instances', []))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only survey of a customer's accounts.")
    ap.add_argument("--customer", required=True, help="slug: folder name under customers/")
    ap.add_argument("--repo", default=".", help="repository root (default: current directory)")
    args = ap.parse_args(argv)

    import boto3  # imported here so tests can run run() with a fake session factory

    return run(pathlib.Path(args.repo).resolve(), args.customer,
               lambda profile, region: boto3.Session(profile_name=profile, region_name=region))


if __name__ == "__main__":
    sys.exit(main())
