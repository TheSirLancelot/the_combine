"""PFF projections from a manual CSV export, one per league.

The PFF API is not out yet, so this is a hand-exported stand-in. It is a real
provider, not a stub: same shape the API adapter will emit, so when the API
ships only fetch() changes.

Verified against the real exports (2026-09-02):
  * 61 columns of stat lines, not points. Passing, rushing, receiving, kicking,
    DST and IDP all present.
  * The raw stats are IDENTICAL between the two league exports. Only
    fantasyPoints and auctionValue differ, because PFF scores one projection
    set under each league's synced rules. Same split ESPN uses.
  * The RCL export carries 701 defensive players; the DMWD export carries K and
    D/ST instead. Each league gets the coverage it actually needs.
  * No player ids. Matching is by name, which is why crosswalk.py exists.

PFF data is licensed to William personally. It stays in data/ (gitignored) and
is never redistributed or exposed raw through a tool.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from ...config import DATA_DIR

PFF_DIR = DATA_DIR / "pff"

# Columns that are metadata rather than projected stats.
_META = {
    "fantasyPointsRank", "playerName", "teamName", "position",
    "byeWeek", "games", "fantasyPoints", "auctionValue",
}


@dataclass(frozen=True)
class PffRow:
    name: str
    team: str
    pos: str            # PFF's own vocabulary, lowercase: rb, wr, cb, dst, ...
    bye: int | None
    games: float
    fantasy_points: float   # already scored for THIS league by PFF
    auction_value: float
    rank: int
    stats: dict[str, float]  # PFF column name -> projected value, zeros dropped


def path_for(league: str) -> Path:
    return PFF_DIR / f"{league}_projections.csv"


def available(league: str) -> bool:
    return path_for(league).exists()


def _num(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def load(league: str) -> list[PffRow]:
    p = path_for(league)
    if not p.exists():
        raise FileNotFoundError(f"no PFF export for '{league}' at {p}")
    out: list[PffRow] = []
    with p.open(newline="") as fh:
        for r in csv.DictReader(fh):
            stats = {k: _num(v) for k, v in r.items() if k not in _META and _num(v)}
            out.append(
                PffRow(
                    name=r["playerName"].strip(),
                    team=(r["teamName"] or "").strip(),
                    pos=(r["position"] or "").strip().lower(),
                    bye=int(_num(r["byeWeek"])) or None,
                    games=_num(r["games"]),
                    fantasy_points=_num(r["fantasyPoints"]),
                    auction_value=_num(r["auctionValue"]),
                    rank=int(_num(r["fantasyPointsRank"])),
                    stats=stats,
                )
            )
    return out
