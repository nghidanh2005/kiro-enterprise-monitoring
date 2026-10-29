#!/usr/bin/env python3
"""Scan the files staged for commit for identifiers that must not be published.

Run before every commit (task 0.2, 10.5):

    python tools/scan_staged.py            # scan the index (what `git commit` takes)
    python tools/scan_staged.py --paths a b  # scan files on disk instead

Exit 0 when nothing is found, 1 when anything is found, 2 on usage error.

What it looks for:
  account-id      any 12-digit number on word boundaries, except the AWS
                  documentation placeholders in ALLOWED_ACCOUNT_IDS
  identity-store  "d-" followed by 10 lowercase letters or digits
                  (an Identity Store ID), except d-xxxxxxxxxx / d-1234567890
  sso-instance    "ssoins-" followed by 8 or more hex characters
  customer-name   any slug or display name in customers/*/config.json
                  (customers/ is gitignored, so the names are read from disk)
  em-dash         U+2014 in a *.tf file: AWS resource names and descriptions
                  must use plain hyphens

Hits print as path:line:kind with the matched value MASKED, so running the
scanner does not copy the identifier into a terminal log or a chat transcript.
Binary files (a NUL byte in the first 8 KiB) are skipped.

A reviewed false positive goes in tools/scan_allowlist.txt as
`<path> <kind> <sha256 prefix>`, copied from the hit line. The entry pins one
value in one file, and customer names can never be allowlisted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass

ALLOWED_ACCOUNT_IDS = {
    "123456789012",
    "111122223333",
    "222233334444",
    "333344445555",
    "444455556666",
}
ALLOWED_STORE_IDS = {"d-xxxxxxxxxx", "d-1234567890"}

RE_ACCOUNT = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")
RE_STORE = re.compile(r"(?<![A-Za-z0-9_-])d-[0-9a-z]{10}(?![A-Za-z0-9_])")
RE_SSOINS = re.compile(r"ssoins-[0-9a-f]{8,}")
EM_DASH = "\u2014"


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    kind: str
    masked: str
    digest: str = ""

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.kind}:{self.masked} sha256={self.digest[:16]}"


def mask(value: str) -> str:
    """Keep the first 2 characters and the length, hide the rest."""
    return f"{value[:2]}{'*' * max(len(value) - 2, 0)} (len {len(value)})"


def _hit(path: str, line: int, kind: str, value: str) -> Hit:
    return Hit(path, line, kind, mask(value), hashlib.sha256(value.encode()).hexdigest())


def load_allowlist(repo: pathlib.Path) -> set[tuple[str, str, str]]:
    """(path, kind, sha256-prefix) entries from tools/scan_allowlist.txt.

    One entry per line: `<path> <kind> <first 16 hex of sha256(value)>`, then an
    optional `# reason`. The value itself is never written to the file.
    """
    f = repo / "tools" / "scan_allowlist.txt"
    entries: set[tuple[str, str, str]] = set()
    if not f.exists():
        return entries
    for raw in f.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{16}", parts[2]):
            raise SystemExit(f"bad allowlist entry: {raw!r}")
        entries.add((parts[0], parts[1], parts[2]))
    return entries


def load_customer_names(repo: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for cfg in sorted((repo / "customers").glob("*/config.json")):
        if cfg.parent.name.startswith("_"):
            continue  # _template and other non-customer folders
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"cannot read {cfg}: {exc}") from exc
        for key in ("slug", "display_name"):
            value = str(data.get(key, "")).strip()
            if len(value) >= 3:
                names.add(value)
    return names


def scan_text(path: str, text: str, customer_names: set[str]) -> list[Hit]:
    hits: list[Hit] = []
    name_res = [
        re.compile(rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9])", re.IGNORECASE)
        for n in sorted(customer_names)
    ]
    is_tf = path.endswith(".tf")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in RE_ACCOUNT.finditer(line):
            if m.group(0) not in ALLOWED_ACCOUNT_IDS:
                hits.append(_hit(path, lineno, "account-id", m.group(0)))
        for m in RE_STORE.finditer(line):
            if m.group(0) not in ALLOWED_STORE_IDS:
                hits.append(_hit(path, lineno, "identity-store", m.group(0)))
        for m in RE_SSOINS.finditer(line):
            hits.append(_hit(path, lineno, "sso-instance", m.group(0)))
        for rx in name_res:
            for m in rx.finditer(line):
                hits.append(_hit(path, lineno, "customer-name", m.group(0)))
        if is_tf and EM_DASH in line:
            hits.append(_hit(path, lineno, "em-dash", EM_DASH))
    return hits


def _is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def _git(repo: pathlib.Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True
    ).stdout


def staged_files(repo: pathlib.Path) -> list[tuple[str, bytes]]:
    """(path, content) for every added/copied/modified/renamed path in the index."""
    raw = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    paths = [p for p in raw.decode("utf-8").split("\0") if p]
    return [(p, _git(repo, "show", f":{p}")) for p in paths]


def disk_files(paths: list[str]) -> list[tuple[str, bytes]]:
    return [(p, pathlib.Path(p).read_bytes()) for p in paths]


def scan(
    files: list[tuple[str, bytes]],
    customer_names: set[str],
    allow: set[tuple[str, str, str]] | None = None,
) -> tuple[list[Hit], int, int]:
    hits: list[Hit] = []
    scanned = skipped = 0
    allow = allow or set()
    for path, data in files:
        if _is_binary(data):
            skipped += 1
            continue
        scanned += 1
        for h in scan_text(path, data.decode("utf-8", errors="replace"), customer_names):
            # A customer name is never allowlisted: rename the text instead.
            if h.kind != "customer-name" and (h.path, h.kind, h.digest[:16]) in allow:
                continue
            hits.append(h)
    return hits, scanned, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=".", help="repository root (default: current directory)")
    ap.add_argument("--paths", nargs="+", help="scan these files on disk instead of the index")
    args = ap.parse_args(argv)

    repo = pathlib.Path(args.repo).resolve()
    names = load_customer_names(repo)
    allow = load_allowlist(repo)
    files = disk_files(args.paths) if args.paths else staged_files(repo)
    hits, scanned, skipped = scan(files, names, allow)
    for h in hits:
        print(h)
    print(
        f"scan_staged: {scanned} text file(s) scanned, {skipped} binary skipped, "
        f"{len(names)} customer name(s) loaded, {len(allow)} allowlist entr(y/ies), "
        f"{len(hits)} hit(s)"
    )
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
