#!/usr/bin/env python3
"""
Cluster claims into deals by PLAYER, not destination club.

The plan's outside voice flagged `to_club` as the volatile field: a hijack moves
the destination (Eze: Spurs -> Arsenal) while it's still the same underlying saga.
The player is the stable identity, so the cluster key is the normalized player name
plus the transfer window. Same player + same window = same deal, regardless of which
clubs are rumoured.
"""
import re
import unicodedata


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
