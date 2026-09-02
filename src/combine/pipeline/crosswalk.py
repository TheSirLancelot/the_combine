"""Resolve one source's players onto another's. Currently ESPN <-> PFF by name,
because the PFF export carries no ids.

Deliberately conservative. A wrong match is worse than no match, since it
silently attaches someone else's projection to a player you are about to draft.
Anything ambiguous goes to the unmatched report for a human to resolve in
config/crosswalk_overrides.csv.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

from ..config import CONFIG_DIR

OVERRIDES = CONFIG_DIR / "crosswalk_overrides.csv"

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
_PUNCT = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")

FUZZ_MIN = 92      # rapidfuzz score to accept
FUZZ_MARGIN = 4    # best must beat runner-up by this much


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = _PUNCT.sub(" ", s.lower())
    s = _SUFFIX.sub(" ", s)
    return _WS.sub(" ", s).strip()


def load_overrides() -> dict[str, str]:
    """espn player name (normalized) -> pff player name (verbatim). Empty pff
    name means 'deliberately no match, stop reporting it'."""
    if not OVERRIDES.exists():
        return {}
    with OVERRIDES.open(newline="") as fh:
        return {
            norm(r["espn_name"]): r["pff_name"].strip()
            for r in csv.DictReader(fh)
            if r.get("espn_name")
        }


def build_index(pff_rows) -> dict[str, list]:
    idx: dict[str, list] = {}
    for row in pff_rows:
        idx.setdefault(norm(row.name), []).append(row)
    return idx


def match(espn_name: str, espn_team: str | None, espn_pos: str | None,
          idx: dict[str, list], overrides: dict[str, str]):
    """Return (row, how) where how is exact|team|pos|fuzzy|override|None."""
    key = norm(espn_name)

    if key in overrides:
        target = overrides[key]
        if not target:
            return None, "ignored"
        for cands in idx.values():
            for row in cands:
                if row.name == target:
                    return row, "override"
        return None, "override-miss"

    cands = idx.get(key, [])
    if len(cands) == 1:
        return cands[0], "exact"
    if len(cands) > 1:
        by_team = [c for c in cands if espn_team and c.team.upper() == espn_team.upper()]
        if len(by_team) == 1:
            return by_team[0], "team"
        by_pos = [c for c in cands if espn_pos and c.pos.upper() == espn_pos.upper()]
        if len(by_pos) == 1:
            return by_pos[0], "pos"
        return None, "ambiguous"

    try:
        from rapidfuzz import process, fuzz
    except ImportError:
        return None, "unmatched"

    hits = process.extract(key, list(idx), scorer=fuzz.WRatio, limit=2)
    if hits and hits[0][1] >= FUZZ_MIN and (len(hits) == 1 or hits[0][1] - hits[1][1] >= FUZZ_MARGIN):
        cands = idx[hits[0][0]]
        if len(cands) == 1:
            return cands[0], "fuzzy"
    return None, "unmatched"


def write_unmatched(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["espn_name", "espn_pos", "espn_team", "reason", "pff_name"])
        w.writeheader()
        w.writerows(rows)
