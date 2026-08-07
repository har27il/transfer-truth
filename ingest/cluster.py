#!/usr/bin/env python3
"""
Cluster claims into deals by PLAYER, not destination club.

The plan's outside voice flagged `to_club` as the volatile field: a hijack moves
the destination (Eze: Spurs -> Arsenal) while it's still the same underlying saga.
The player is the stable identity, so the cluster key is the normalized player name
plus the transfer window. Same player + same window = same deal, regardless of which
clubs are rumoured.

SPLIT PLAYERS (the alias layer, below). Extraction sometimes returns a bare surname
("Olise") where it elsewhere returns the full name ("Michael Olise"). Those hash to
different keys, so ONE transfer becomes two deals with the claims divided between
them -- which is worse than cosmetic: it split the "Real Madrid deny interest" claim
away from the three interest claims, so one deal's meter never saw the denial and
read too high while the other looked like a dead rumour. It also suppresses the
corroboration boost and the contested spread, both of which need 2+ sources on ONE
deal.

The fix is an ALIAS MAP rather than a change to deal_key's format. deal_key's output
is stored in ingest.db, which is cached across CI runs and never committed -- change
the format and old cached claims keep old keys while new claims get new ones, so the
same deal re-splits by epoch. The alias map is instead recomputed from the current
claim population on every run, so a warm cache and a cold one group identically.

INVARIANT: a canonical key is always an existing raw key of an observed full-name
cluster, i.e. exactly f"{normalize_name(full_name)}|{window}". Only single-token keys
are ever aliased and a canonical key is never itself an alias, so there are no chains
and recovering the window by pipe index stays correct.
"""
import re
import unicodedata
from collections import Counter

from outcome.detect import same_club


def normalize_name(name):
    """Lowercase, strip accents/punctuation, collapse spaces. 'João Pedro' -> 'joao pedro'."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def deal_key(player, window):
    """Stable cluster key for a deal. Empty player -> '' (caller should drop it)."""
    p = normalize_name(player)
    return f"{p}|{window}" if p else ""


def surname(player):
    """Last token of the normalized name. 'Michael Olise' -> 'olise'."""
    parts = normalize_name(player).split()
    return parts[-1] if parts else ""


def key_window(key):
    """Recover the window from a cluster key. Inverse of deal_key's f-string."""
    return key.split("|", 1)[1] if "|" in key else ""


def provisional(claims, field):
    """Most common non-empty value of `field`; ties broken by the most recent claim."""
    vals = [((c.get("claim_date") or ""), c[field]) for c in claims if c.get(field)]
    if not vals:
        return ""
    counts = Counter(v for _, v in vals)
    top = max(counts.values())
    winners = {v for v, n in counts.items() if n == top}
    for _, v in sorted(vals, reverse=True):   # most recent claim first
        if v in winners:
            return v
    return next(iter(winners))


def cluster_profiles(conn):
    """raw deal_key -> {player, from_club, to_club, n_claims} for every stored cluster."""
    from ingest import store                  # local: keeps this module's import graph flat
    out = {}
    for key in store.deal_keys(conn):
        claims = store.claims_for_deal(conn, key)
        if not claims:
            continue
        out[key] = {"player": provisional(claims, "player"),
                    "from_club": provisional(claims, "from_club"),
                    "to_club": provisional(claims, "to_club"),
                    "n_claims": len(claims)}
    return out


def alias_map(profiles):
    """{bare_surname_key: canonical_full_name_key} for confidently-same players.

    PURE -- give it profiles from the ingest store or from deals.csv rows, same rule.
    Only entries that actually differ are returned; unmergeable keys are absent.

    THE RULE, validated against all 271 ledger rows (11 merges, 2 refusals): merge a
    bare-surname cluster into a full-name one when surname + window match AND the two
    agree on a destination OR an origin club. same_club already returns False for an
    empty side, so "both non-empty" is implicit.

    Requiring only ONE of the two clubs is what makes this work where a
    destination-only rule fails: deal 73 (Diomande / RB Leipzig / Liverpool) and deal
    124 (Yan Diomande / RB Leipzig / PSG) are the same player whose destinations
    disagree only because one of them is stale. from_club is the stable side -- a
    hijack moves the destination, never the origin.

    Ambiguity is REFUSED, not guessed: 2+ candidate full names means no alias. That
    keeps Ousmane Diomande (Sporting CP) apart from Yan, and bounds any club error to
    over-refusal rather than attributing one player's claims to another -- which would
    be ground-truth corruption, not a display bug.
    """
    groups = {}
    for key in sorted(profiles):                     # sorted => order-independent result
        p = profiles[key]
        sur = surname(p.get("player"))
        if sur:
            groups.setdefault((sur, key_window(key)), []).append((key, p))

    out = {}
    for members in groups.values():
        bare = [(k, p) for k, p in members if len(normalize_name(p.get("player")).split()) == 1]
        full = [(k, p) for k, p in members if len(normalize_name(p.get("player")).split()) > 1]
        if not bare or not full:
            continue
        for bkey, bp in bare:
            cands = [fkey for fkey, fp in full
                     if same_club(bp.get("to_club"), fp.get("to_club"))
                     or same_club(bp.get("from_club"), fp.get("from_club"))]
            if len(cands) == 1 and cands[0] != bkey:
                out[bkey] = cands[0]
    return out


def alias_map_for(conn):
    """alias_map over the live ingest store."""
    return alias_map(cluster_profiles(conn))


def group_keys(keys, alias):
    """canonical_key -> [raw keys], preserving input order. Ungrouped keys map to themselves."""
    groups = {}
    for k in keys:
        groups.setdefault(alias.get(k, k), []).append(k)
    return groups
