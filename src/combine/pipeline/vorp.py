"""Value over replacement. Converts projected points into draft order.

Raw points do not determine when a player is drafted, and comparing a
points-rank against ADP is comparing two different things. A QB projected for
300 when the 12th best QB gets 280 is worth 20 points of edge. An RB projected
for 250 when the 24th best RB gets 130 is worth 120. The RB goes far earlier
despite scoring fewer points.

This matters most in RCL, where the pool is full of linebackers projected for
230 and quarterbacks over 300 who nobody drafts early. Ranking on raw points
pushed every running back down the board and made every VAL negative.

Replacement level = the last player at that position who will actually start
somewhere in the league. Demand comes from the league's own slot counts times
the number of teams, with flex slots split evenly across the positions
eligible for them.
"""

from __future__ import annotations

import math

# ESPN slot -> positions eligible for it. ESPN reports IDP players with real
# positions (DE, DT, CB, S, LB) while the slots are the coarse DL/DB/DP.
SLOT_ELIGIBILITY = {
    "QB": ["QB"], "RB": ["RB"], "WR": ["WR"], "TE": ["TE"],
    "K": ["K"], "D/ST": ["D/ST"],
    "RB/WR": ["RB", "WR"],
    "WR/TE": ["WR", "TE"],
    "RB/WR/TE": ["RB", "WR", "TE"],
    "OP": ["QB", "RB", "WR", "TE"],
    "LB": ["LB"],
    "DL": ["DE", "DT"],
    "DB": ["CB", "S"],
    "DP": ["LB", "DE", "DT", "CB", "S"],
}


def demand(slots: dict[str, int], teams: int) -> dict[str, float]:
    """Starting jobs per position across the whole league."""
    out: dict[str, float] = {}
    for slot, count in slots.items():
        eligible = SLOT_ELIGIBILITY.get(slot)
        if not eligible or not count:
            continue
        share = (count * teams) / len(eligible)
        for pos in eligible:
            out[pos] = out.get(pos, 0.0) + share
    return out


def replacement_points(rows, slots: dict[str, int], teams: int) -> dict[str, float]:
    """Points of the last startable player at each position.

    rows need .state.pos and .avg. Positions with no demand (a kicker in
    a league with no K slot) get 0, which correctly makes them worthless.
    """
    need = demand(slots, teams)
    by_pos: dict[str, list[float]] = {}
    for r in rows:
        by_pos.setdefault(r.state.pos, []).append(r.avg)

    out: dict[str, float] = {}
    for pos, points in by_pos.items():
        points.sort(reverse=True)
        n = need.get(pos, 0)
        if n <= 0:
            out[pos] = 0.0
            continue
        # index of the last starter, clamped to what we actually have
        idx = min(max(int(math.ceil(n)) - 1, 0), len(points) - 1)
        out[pos] = points[idx]
    return out
