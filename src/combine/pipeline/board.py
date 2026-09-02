"""Merged draft board: ESPN's league-scored projection plus PFF's, per player.

Deliberate shortcut for the 2026 draft. Both sources already score their own
projections under this league's rules, so the board blends the finished point
totals rather than rescoring stat lines through scoring.py. That is phase 2
work and it is not needed to draft. The PFF stat lines are loaded and kept, so
doing it properly later changes the blend, not the ingest.

The interesting output is not the consensus number, it is the disagreement.
Two sources agreeing tells you nothing you did not already know.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..format import tiers
from ..platforms import PlayerState
from .crosswalk import build_index, load_overrides, match
from .providers import pff_csv

DISAGREE_AT = 10  # positional rank gap worth flagging


@dataclass
class BoardRow:
    state: PlayerState
    espn_pts: float
    pff_pts: float | None
    pff_name: str | None
    pff_auction: float | None
    bye: int | None
    how: str
    consensus: float = 0.0
    flag: str = ""


def build(league: str, states: list[PlayerState]) -> tuple[list[BoardRow], dict]:
    """states are ESPN players (already league-scored). Returns rows sorted by
    consensus, plus a small stats dict for reporting match quality."""
    rows: list[BoardRow] = []
    have_pff = pff_csv.available(league)
    idx, overrides = ({}, {})
    if have_pff:
        idx = build_index(pff_csv.load(league))
        overrides = load_overrides()

    counts: dict[str, int] = {}
    for s in states:
        hit, how = (None, "no-source")
        if have_pff:
            hit, how = match(s.name, s.team, s.pos, idx, overrides)
        counts[how] = counts.get(how, 0) + 1
        rows.append(
            BoardRow(
                state=s,
                espn_pts=s.platform_proj or 0.0,
                pff_pts=hit.fantasy_points if hit else None,
                pff_name=hit.name if hit else None,
                pff_auction=hit.auction_value if hit else None,
                bye=hit.bye if hit else None,
                how=how,
            )
        )

    for r in rows:
        r.consensus = (r.espn_pts + r.pff_pts) / 2 if r.pff_pts is not None else r.espn_pts

    # positional rank in each source, to surface where they disagree
    for src in ("espn_pts", "pff_pts"):
        by_pos: dict[str, list[BoardRow]] = {}
        for r in rows:
            if getattr(r, src) is not None:
                by_pos.setdefault(r.state.pos, []).append(r)
        for group in by_pos.values():
            group.sort(key=lambda r: getattr(r, src), reverse=True)
            for i, r in enumerate(group):
                setattr(r, f"_{src}_rank", i + 1)

    for r in rows:
        e, p = getattr(r, "_espn_pts_rank", None), getattr(r, "_pff_pts_rank", None)
        if r.pff_pts is None:
            r.flag = "no-pff"
        elif e and p and abs(e - p) >= DISAGREE_AT:
            r.flag = f"{'PFF' if p < e else 'ESPN'}+{abs(e - p)}"

    rows.sort(key=lambda r: r.consensus, reverse=True)
    return rows, counts


def render(rows: list[BoardRow], header: str) -> str:
    tier_of = tiers([r.consensus for r in rows])
    out = [header,
           f"{'#':>3} {'POS':<4} {'PLAYER':<21} {'TM':<3} {'ESPN':>6} {'PFF':>6} {'CONS':>6} {'BYE':>3} TIER FLAG"]
    for i, r in enumerate(rows):
        s = r.state
        pff = f"{r.pff_pts:>6.1f}" if r.pff_pts is not None else "     -"
        bye = f"{r.bye:>3}" if r.bye else "  -"
        line = (f"{i+1:>3} {s.pos:<4} {s.name[:21]:<21} {(s.team or '--'):<3} "
                f"{r.espn_pts:>6.1f} {pff} {r.consensus:>6.1f} {bye} T{tier_of[i]:<3}")
        extras = [x for x in (r.flag, s.status if s.status != "OK" else "") if x]
        out.append(line + (" " + " ".join(extras) if extras else ""))
    return "\n".join(out)
