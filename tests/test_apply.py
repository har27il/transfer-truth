"""apply.py tests — the write-back step must be safe and idempotent.

All offline: a fake resolver stands in for the network/LLM source, so we control
exactly what each deal resolves to.
"""
import csv
import time
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


def test_blank_destination_completion_records_joined_club(tmp_path):
    """The Konate case: a deal with NO rumoured destination resolves COMPLETED. The
    resolver knows which club he joined, so apply records it into to_club -- otherwise
    the feed renders '-> ?'. Positive-evidence-only: only the verified club is written."""
    p = tmp_path / "deals.csv"
    _write(p, [_row("1", "Departed Player", "Liverpool", "")])   # to_club blank
    resolver = lambda row: {"status": "moved", "joined_club": "Real Madrid", "window_closed": True}
    changes = apply_mod.apply(p, resolver=resolver, dry_run=False, rebuild=False)
    assert len(changes) == 1
    row = _read(p)[0]
    assert row["outcome"] == "completed"
    assert row["to_club"] == "Real Madrid"      # filled from positive evidence for display


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


# --- concurrency: the fan-out must not change a single observable ------------------
#
# resolve_unknowns fans the resolver call across workers but gathers in SUBMISSION
# order. These tests are the ONLY guard on that ordering: if a future refactor
# gathered by COMPLETION order instead, deals.csv rows would still all be written --
# just with `changes` in a nondeterministic order -- and nothing else would notice.

def _slow_ordered_resolver(players, base=0.02):
    """Resolver whose delay is INVERTED against submission order.

    The first-submitted deal sleeps longest, the last sleeps least, so completion
    order is guaranteed to be the REVERSE of submission order. Without this a fake
    resolver returns instantly, every future completes in submission order by
    accident, and the determinism test below passes even against an implementation
    that gathers by completion order -- i.e. it would prove nothing.
    """
    delays = {p: base * (len(players) - i) for i, p in enumerate(players)}

    def resolver(row):
        time.sleep(delays[row["player"]])
        return {"status": "moved", "joined_club": "Arsenal", "window_closed": True}
    return resolver


def test_concurrent_matches_sequential_exactly(tmp_path):
    """4 workers must produce byte-identical output to 1 worker: same `changes`
    order, same file contents. The resolver completes in reverse submission order."""
    players = [f"Player {i}" for i in range(6)]
    rows = [_row(str(i), p, "A", "Arsenal") for i, p in enumerate(players)]

    seq_path, con_path = tmp_path / "seq.csv", tmp_path / "con.csv"
    _write(seq_path, rows)
    _write(con_path, rows)

    seq = apply_mod.apply(seq_path, resolver=_slow_ordered_resolver(players),
                          dry_run=False, rebuild=False, concurrency=1)
    con = apply_mod.apply(con_path, resolver=_slow_ordered_resolver(players),
                          dry_run=False, rebuild=False, concurrency=4)

    assert seq == con, "changes order diverged between sequential and concurrent"
    assert [c[1] for c in con] == players, "changes not in submission order"
    assert seq_path.read_text("utf-8") == con_path.read_text("utf-8")


def test_one_failing_resolver_does_not_abort_the_batch(tmp_path):
    """A single deal's network/API blow-up must not lose the other 3. The failed
    deal stays `unknown` so the next run retries it."""
    p = tmp_path / "deals.csv"
    _write(p, [_row(str(i), f"P{i}", "A", "Arsenal") for i in range(4)])

    def resolver(row):
        if row["player"] == "P2":
            raise RuntimeError("NIM 500")
        return {"status": "moved", "joined_club": "Arsenal", "window_closed": True}

    changes = apply_mod.apply(p, resolver=resolver, dry_run=False, rebuild=False,
                              concurrency=4)
    assert [c[1] for c in changes] == ["P0", "P1", "P3"]   # order preserved around the hole
    rows = {r["player"]: r for r in _read(p)}
    assert rows["P2"]["outcome"] == ""                     # untouched, retried next run
    assert all(rows[f"P{i}"]["outcome"] == "completed" for i in (0, 1, 3))


def test_concurrency_one_takes_the_sequential_path(tmp_path):
    p = tmp_path / "deals.csv"
    _write(p, [_row("1", "Solo", "A", "Arsenal")])
    resolver = lambda row: {"status": "moved", "joined_club": "Arsenal", "window_closed": True}
    changes = apply_mod.apply(p, resolver=resolver, dry_run=False, rebuild=False,
                              concurrency=1)
    assert len(changes) == 1
    assert _read(p)[0]["outcome"] == "completed"


def test_no_pending_deals_does_not_crash_worker_math(tmp_path):
    """max(1, min(n, len(pending))) would raise on an empty pending list if the
    early return were ever removed. Every row here is already resolved."""
    p = tmp_path / "deals.csv"
    _write(p, [_row("1", "Done", "A", "Arsenal", outcome="completed")])

    def resolver(row):
        raise AssertionError("resolver must not be called when nothing is pending")

    changes = apply_mod.apply(p, resolver=resolver, dry_run=False, rebuild=False,
                              concurrency=4)
    assert changes == []


def test_progress_line_printed_per_deal(capsys, tmp_path):
    """The step must stream progress, not go silent for 20+ minutes -- the silence is
    why the 2026-08-06 timeout was invisible while it was happening."""
    p = tmp_path / "deals.csv"
    _write(p, [_row(str(i), f"P{i}", "A", "Arsenal") for i in range(3)])
    resolver = lambda row: {"status": "moved", "joined_club": "Arsenal", "window_closed": True}
    apply_mod.apply(p, resolver=resolver, dry_run=False, rebuild=False, concurrency=2)
    out = capsys.readouterr().out
    for i in range(1, 4):
        assert f"[{i}/3]" in out, f"missing progress line {i}/3 in:\n{out}"


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
