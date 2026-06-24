import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from bitwarden_vault_audit import (  # noqa: E402
    find_reused_passwords,
    find_stale_passwords,
    find_weak_passwords,
    load_export,
    to_markdown,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample_export.json")
WEAK_PASSWORD = "password123"
STRONG_PASSWORD = "Tr0ub4dor&3xampleStrong!"


def test_load_export_reads_all_login_items():
    entries = load_export(FIXTURE)
    assert len(entries) == 5


def test_load_export_handles_item_without_password():
    entries = load_export(FIXTURE)
    note = next(e for e in entries if e.name == "Secure Note (no login)")
    assert note.password_hash is None


def test_load_export_never_retains_plaintext_password():
    entries = load_export(FIXTURE)
    for e in entries:
        # the dataclass should have no attribute holding the raw password
        assert not hasattr(e, "password")
        assert WEAK_PASSWORD not in repr(e)
        assert STRONG_PASSWORD not in repr(e)


def test_find_reused_passwords_groups_correctly():
    entries = load_export(FIXTURE)
    reused = find_reused_passwords(entries)
    assert len(reused) == 1
    group = next(iter(reused.values()))
    assert len(group) == 3
    names = {e.name for e in group}
    assert names == {"Old Forum Account", "Streaming Service A", "Streaming Service B"}


def test_find_weak_passwords_flags_low_entropy():
    entries = load_export(FIXTURE)
    weak = find_weak_passwords(entries, min_length=12)
    weak_names = {e.name for e in weak}
    assert "Old Forum Account" in weak_names
    assert "Example Bank" not in weak_names


def test_find_stale_passwords_uses_revision_date():
    entries = load_export(FIXTURE)
    as_of = datetime(2026, 6, 23, tzinfo=timezone.utc)
    stale = find_stale_passwords(entries, max_age_days=365, as_of=as_of)
    stale_names = {e.name for e in stale}
    assert "Old Forum Account" in stale_names
    assert "Example Bank" not in stale_names


def test_to_markdown_never_contains_password_values():
    entries = load_export(FIXTURE)
    reused = find_reused_passwords(entries)
    weak = find_weak_passwords(entries, min_length=12)
    stale = find_stale_passwords(entries, max_age_days=365, as_of=datetime(2026, 6, 23, tzinfo=timezone.utc))
    md = to_markdown(entries, reused, weak, stale)
    assert WEAK_PASSWORD not in md
    assert STRONG_PASSWORD not in md
    assert "No password values are shown" in md


def test_to_markdown_reports_counts():
    entries = load_export(FIXTURE)
    reused = find_reused_passwords(entries)
    weak = find_weak_passwords(entries, min_length=12)
    stale = find_stale_passwords(entries, max_age_days=365, as_of=datetime(2026, 6, 23, tzinfo=timezone.utc))
    md = to_markdown(entries, reused, weak, stale)
    assert "Reused password groups:** 1" in md
