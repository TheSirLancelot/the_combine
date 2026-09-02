"""PFF draft rankings export. League-agnostic, and the only source of ADP.

Projections tell you who is good. ADP tells you what the room will pay. The
gap between them is the draft signal, so this file earns its place on the
board even though its Projected Points column duplicates what the per-league
exports already give us (and is computed under PFF's default scoring, not
either of William's leagues, so we deliberately ignore it for points).

Two quirks, both real in the 2026 export:
  * ADP saturates around 170. Everyone undrafted in PFF's sample lands at
    169-171, so a "value" computed off those is noise. Treated as unknown.
  * A player who is out for the season stays in the rankings with an ADP from
    before the news and Projected Points of 0. Josh Jacobs is rank 161, ADP
    64.1, 0 points. That combination is a strong "something happened" signal.

The export's title row is stripped before saving; the header must be line 1.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass

from ...config import DATA_DIR

RANKINGS = DATA_DIR / "pff" / "draft_rankings.csv"

ADP_SATURATION = 168.0  # at or beyond this, ADP carries no information


@dataclass(frozen=True)
class RankRow:
    name: str
    team: str
    pos: str
    overall_rank: int
    pos_rank: int
    bye: int | None
    adp: float | None          # None when missing or saturated
    proj_points: float
    auction: float | None


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def available() -> bool:
    return RANKINGS.exists()


def load() -> list[RankRow]:
    if not RANKINGS.exists():
        return []
    out = []
    with RANKINGS.open(newline="") as fh:
        for r in csv.DictReader(fh):
            adp = _num(r.get("ADP", ""))
            if adp is not None and adp >= ADP_SATURATION:
                adp = None
            out.append(
                RankRow(
                    name=r["Full Name"].strip(),
                    team=(r.get("Team Abbreviation") or "").strip(),
                    pos=(r.get("Position") or "").strip(),
                    overall_rank=int(_num(r.get("Overall Rank", "")) or 0),
                    pos_rank=int(_num(r.get("Position Rank", "")) or 0),
                    bye=int(_num(r.get("Bye Week", "")) or 0) or None,
                    adp=adp,
                    proj_points=_num(r.get("Projected Points", "")) or 0.0,
                    auction=_num(r.get("Auction Value", "")),
                )
            )
    return out
