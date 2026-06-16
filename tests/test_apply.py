"""apply.py tests — the write-back step must be safe and idempotent.

All offline: a fake resolver stands in for the network/LLM source, so we control
exactly what each deal resolves to.
"""
import csv
from pathlib import Path

from outcome import apply as apply_mod

HEADER = ["deal_id", "player", "from_club", "to_club", "window", "outcome",
          "fee_eur_actual", "outcome_date", "outcome_source_url", "verified", "notes"]


def _row(did, player, frm, to, outcome=""):
    return {k: v for k, v in zip(HEADER, [
        did, player, frm, to, "2025-summer", outcome, "", "", "", "", ""])}


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_resolves_unknowns_but_leaves_resolved_and_ambiguous(tmp_path):
    p = tmp_path / "deals.csv"
    _write(p, [
        _row("1", "Mover Joins Target", "A", "Arsenal"),        # -> completed
        _row("2", "Mover Joins Other", "A", "Chelsea"),         # -> collapsed
        _row("3", "Stayer", "A", "Liverpool"),                  # -> collapsed (window closed)
        _row("4", "Ambiguous Player", "A", "Spurs"),            # -> stays unknown
        _row("5", "Already Done", "A", "Arsenal", outcome="completed"),  # untouched
    ])

    def fake_resolver(row):
        return {
            "Mover Joins Target": {"status": "moved", "joined_club": "Arsenal", "window_closed": True},
            "Mover Joins Other": {"status": "moved", "joined_club": "Everton", "window_closed": True},
            "Stayer": {"status": "stayed", "joined_club": None, "window_closed": True},
            "Ambiguous Player": {"status": "unclear", "joined_club": None, "window_closed": True},
        }[row["player"]]

    changes = apply_mod.apply(p, resolver=fake_resolver, dry_run=False, rebuild=False)
    assert len(changes) == 3
    rows = {r["deal_id"]: r for r in _read(p)}
    assert rows["1"]["outcome"] == "completed" and rows["1"]["verified"] == "auto"
    assert rows["2"]["outcome"] == "collapsed"
    assert rows["3"]["outcome"] == "collapsed"
    assert rows["4"]["outcome"] == ""           # ambiguous never written
    assert rows["5"]["outcome"] == "completed"  # pre-existing untouched
    assert rows["5"]["verified"] == ""          # not re-stamped


def test_idempotent_second_run_changes_nothing(tmp_path):
    p = tmp_path / "deals.csv"
    _write(p, [_row("1", "Stayer", "A", "Liverpool")])
    resolver = lambda row: {"status": "stayed", "joined_club": None, "window_closed": True}
    apply_mod.apply(p, resolver=resolver, dry_run=False, rebuild=False)
    first = p.read_text("utf-8")
    changes = apply_mod.apply(p, resolver=resolver, dry_run=False, rebuild=False)
    assert changes == []
    assert p.read_text("utf-8") == first  # byte-identical, no churn


def test_dry_run_writes_nothing(tmp_path):
    p = tmp_path / "deals.csv"
    _write(p, [_row("1", "Stayer", "A", "Liverpool")])
    before = p.read_text("utf-8")
    changes = apply_mod.apply(p, resolver=lambda row: {"status": "stayed", "window_closed": True},
                              dry_run=True, rebuild=False)
    assert len(changes) == 1
    assert p.read_text("utf-8") == before  # untouched


def test_atomic_write_leaves_original_intact_on_crash(tmp_path, monkeypatch):
    p = tmp_path / "deals.csv"
    original_rows = [_row("1", "Stayer", "A", "Liverpool")]
    _write(p, original_rows)
    before = p.read_text("utf-8")

    # Simulate a crash during os.replace (after the temp file is written).
    def boom(src, dst):
        raise OSError("disk exploded mid-replace")
    monkeypatch.setattr(apply_mod.os, "replace", boom)

    try:
        apply_mod.apply(p, resolver=lambda row: {"status": "stayed", "window_closed": True},
                        dry_run=False, rebuild=False)
    except OSError:
        pass
    # Ground truth must be exactly as before; no temp leftovers in the dir.
    assert p.read_text("utf-8") == before
    leftovers = [f for f in p.parent.iterdir() if f.name.startswith(".deals.")]
    assert leftovers == [], f"temp files leaked: {leftovers}"
