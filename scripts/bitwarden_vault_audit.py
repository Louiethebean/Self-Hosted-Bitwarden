#!/usr/bin/env python3
"""Audit a Bitwarden vault export for reused, weak, and stale passwords --
without ever printing, logging, or persisting the actual password values.

Usage:
    bw export --format json --output vault_export.json
    python bitwarden_vault_audit.py vault_export.json --min-length 12 --max-age-days 365 --format md

Security note: every password is hashed (SHA-256) the moment it's read and
the plaintext is discarded immediately. Only the hash, length, and character
class composition are retained for analysis -- the report can prove two
items share a password without ever revealing what that password is.
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class VaultEntry:
    name: str
    uri: str
    password_hash: Optional[str]
    length: int
    has_upper: bool
    has_lower: bool
    has_digit: bool
    has_special: bool
    revision_date: Optional[datetime]

    @property
    def char_class_count(self) -> int:
        return sum([self.has_upper, self.has_lower, self.has_digit, self.has_special])

    def is_weak(self, min_length: int) -> bool:
        if self.password_hash is None:
            return False
        return self.length < min_length or self.char_class_count < 3

    def age_days(self, as_of: datetime) -> Optional[int]:
        if self.revision_date is None:
            return None
        return (as_of - self.revision_date).days


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _classify(password: str) -> Dict[str, bool]:
    return {
        "has_upper": bool(re.search(r"[A-Z]", password)),
        "has_lower": bool(re.search(r"[a-z]", password)),
        "has_digit": bool(re.search(r"[0-9]", password)),
        "has_special": bool(re.search(r"[^A-Za-z0-9]", password)),
    }


def _parse_revision_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_export(path: str) -> List[VaultEntry]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries: List[VaultEntry] = []
    for item in data.get("items", []):
        login = item.get("login") or {}
        password = login.get("password")

        password_hash = None
        length = 0
        classes = {"has_upper": False, "has_lower": False, "has_digit": False, "has_special": False}
        if password:
            password_hash = _hash_password(password)
            length = len(password)
            classes = _classify(password)
            password = None  # discard plaintext immediately; do not retain in memory beyond this point

        uris = login.get("uris") or []
        uri = uris[0]["uri"] if uris and isinstance(uris[0], dict) else ""

        entries.append(
            VaultEntry(
                name=item.get("name", "Unnamed item"),
                uri=uri,
                password_hash=password_hash,
                length=length,
                revision_date=_parse_revision_date(item.get("revisionDate")),
                **classes,
            )
        )

    return entries


def find_reused_passwords(entries: List[VaultEntry]) -> Dict[str, List[VaultEntry]]:
    by_hash: Dict[str, List[VaultEntry]] = {}
    for e in entries:
        if e.password_hash is None:
            continue
        by_hash.setdefault(e.password_hash, []).append(e)
    return {h: items for h, items in by_hash.items() if len(items) > 1}


def find_weak_passwords(entries: List[VaultEntry], min_length: int = 12) -> List[VaultEntry]:
    return [e for e in entries if e.is_weak(min_length)]


def find_stale_passwords(entries: List[VaultEntry], max_age_days: int, as_of: datetime) -> List[VaultEntry]:
    stale = []
    for e in entries:
        age = e.age_days(as_of)
        if age is not None and age >= max_age_days:
            stale.append(e)
    return stale


def to_markdown(
    entries: List[VaultEntry],
    reused: Dict[str, List[VaultEntry]],
    weak: List[VaultEntry],
    stale: List[VaultEntry],
) -> str:
    lines = ["# Bitwarden Vault Hygiene Audit", ""]
    lines.append(f"**Total items scanned:** {len(entries)}")
    lines.append(f"**Reused password groups:** {len(reused)}")
    lines.append(f"**Weak passwords:** {len(weak)}")
    lines.append(f"**Stale passwords (not rotated recently):** {len(stale)}")
    lines.append("")
    lines.append("_No password values are shown below -- only item names and the issue found._")
    lines.append("")

    if reused:
        lines.append("## Reused Passwords")
        for h, items in reused.items():
            names = ", ".join(e.name for e in items)
            lines.append(f"- Shared by {len(items)} items: {names} (hash prefix `{h[:8]}`)")
        lines.append("")

    if weak:
        lines.append("## Weak Passwords")
        lines.append("| Item | Length | Character Classes |")
        lines.append("|---|---|---|")
        for e in weak:
            lines.append(f"| {e.name} | {e.length} | {e.char_class_count}/4 |")
        lines.append("")

    if stale:
        lines.append("## Stale Passwords")
        lines.append("| Item | Last Changed |")
        lines.append("|---|---|")
        for e in stale:
            lines.append(f"| {e.name} | {e.revision_date.date() if e.revision_date else 'unknown'} |")

    return "\n".join(lines) + "\n"


def to_csv(weak: List[VaultEntry], stale: List[VaultEntry], out) -> None:
    writer = csv.writer(out)
    writer.writerow(["category", "item", "length", "char_classes"])
    for e in weak:
        writer.writerow(["weak", e.name, e.length, e.char_class_count])
    for e in stale:
        writer.writerow(["stale", e.name, e.length, e.char_class_count])


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a Bitwarden vault export for password hygiene issues.")
    parser.add_argument("export_file", help="Path to a `bw export --format json` output file")
    parser.add_argument("--min-length", type=int, default=12)
    parser.add_argument("--max-age-days", type=int, default=365)
    parser.add_argument("--format", choices=["md", "csv"], default="md")
    args = parser.parse_args()

    entries = load_export(args.export_file)
    reused = find_reused_passwords(entries)
    weak = find_weak_passwords(entries, args.min_length)
    stale = find_stale_passwords(entries, args.max_age_days, datetime.now(timezone.utc))

    if args.format == "md":
        sys.stdout.write(to_markdown(entries, reused, weak, stale))
    else:
        to_csv(weak, stale, sys.stdout)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
