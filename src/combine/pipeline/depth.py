"""Who on the roster is worse than what is freely available at his position.

A different question from `waivers.py`, which is why it is a different module.
That one asks "does this free agent improve my lineup THIS WEEK", and answers
correctly and uselessly when the problem is a bench player who will not play for
a month: replacing him gains nothing on Sunday, so he never appears.

The live example: DMWD carried De'Zhaun Stribling at 108 points of
rest-of-season projection while Kenyon Sadiq sat unrostered at 147. The weekly
view had nothing to say about it, because neither man was going to start.

**Replacement level is observed here, not modelled.** The draft board estimates
it from league-wide demand, because in August the waiver wire does not exist
yet. In season it does: the best free agent at a position IS the replacement,
by definition, and the number is sitting right there. Nothing is invented.

**Points are compared only inside a position, never across.** Ranking the pool
on raw rest-of-season projection puts six backup quarterbacks on top, because
quarterbacks score more than receivers. Malik Willis at 284 is not worth more
than Sadiq at 147 to a team already starting Jayden Daniels at 367. Same
mistake as ranking a draft board on raw points, and the reason this compares a
receiver only against receivers.
"""

from __future__ import annotations

from dataclasses import dataclass

from .waivers import IR_STATUS, NEVER_STREAM, pending_adds


@dataclass(frozen=True)
class Gap:
    """A rostered player the wire can beat at his own position."""

    name: str
    pos: str
    season: float                 # his rest-of-season projection
    best_name: str                # best free agent at the same position
    best_season: float
    slot: str
    in_lineup: bool               # started this week
    status: str
    only_one: bool                # the only player he has at this position

    @property
    def surplus(self) -> float:
        """His projection minus the wire's. Negative means the wire is better."""
        return self.season - self.best_season

    @property
    def gain(self) -> float:
        return max(0.0, -self.surplus)

    def describe(self) -> str:
        line = (f"{self.name} ({self.pos}) {self.season:.0f} vs "
                f"{self.best_name} {self.best_season:.0f} free: "
                f"{self.gain:.0f} points of season")
        notes = []
        if self.status and self.status.upper() not in ("OK", "ACTIVE"):
            notes.append(self.status)
        if self.in_lineup:
            notes.append("currently starting")
        if self.only_one:
            notes.append(f"your only {self.pos}")
        return line + (f"  [{', '.join(notes)}]" if notes else "")


def best_available(client, pool_size: int = 350,
                   skip: dict[str, str] | None = None) -> dict[str, tuple[str, float]]:
    """{position: (name, rest-of-season projection)} for the best free agent.

    This is replacement level, observed. A player already claimed is skipped:
    he is not available to be added again.
    """
    skip = skip or {}
    out: dict[str, tuple[str, float]] = {}
    for raw in client.league.free_agents(size=pool_size):
        pos = (getattr(raw, "position", "") or "").upper()
        if not pos or pos in NEVER_STREAM:
            continue
        if str(getattr(raw, "playerId", "")) in skip:
            continue
        season = float(getattr(raw, "projected_total_points", 0.0) or 0.0)
        if season <= 0:
            continue
        current = out.get(pos)
        if current is None or season > current[1]:
            out[pos] = (getattr(raw, "name", "?"), season)
    return out


def find(client, season_value: dict[str, float], lineup, base_ids: set[str],
         pool_size: int = 350, limit: int = 8) -> list[Gap]:
    """Rostered players the wire beats at their own position, worst first."""
    claimed = pending_adds(client)
    wire = best_available(client, pool_size, skip=claimed)
    counts: dict[str, int] = {}
    for p in lineup:
        counts[p.pos.upper()] = counts.get(p.pos.upper(), 0) + 1

    gaps = []
    for p in lineup:
        if p.slot == "IR":
            continue          # deliberately being kept
        if (p.status or "").upper() in IR_STATUS and p.slot == "IR":
            continue
        pos = p.pos.upper()
        best = wire.get(pos)
        if not best:
            continue
        mine = season_value.get(p.player_id, 0.0)
        if mine <= 0 or mine >= best[1]:
            continue
        gaps.append(Gap(
            name=p.name, pos=p.pos, season=mine, best_name=best[0],
            best_season=best[1], slot=p.slot,
            in_lineup=p.player_id in base_ids, status=p.status or "",
            only_one=counts.get(pos, 0) <= 1))
    gaps.sort(key=lambda g: g.surplus)
    return gaps[:limit]


def for_league(league: str, pool_size: int = 350, limit: int = 8) -> list[Gap]:
    """The whole question for one league, so three callers cannot drift."""
    from ..platforms import client_for
    from .calibration import load as load_cal
    from .waivers import _value, season_values

    client = client_for(league)
    lineup = client.matchup(client.week).my_lineup
    slots = client.roster_slots()
    slot_list = [s for s, n in slots.items() for _ in range(n)]
    _value_now, base_ids = _value(lineup, slot_list, load_cal(league))
    return find(client, season_values(client), lineup, base_ids,
                pool_size=pool_size, limit=limit)


def render(gaps: list[Gap], league_name: str) -> str:
    if not gaps:
        return (f"{league_name} — roster depth\n"
                f"Nobody on your roster is beaten by the wire at his own "
                f"position.")
    out = [f"{league_name} — roster depth", ""]
    out.append(f"  {'YOURS':<22}{'POS':<5}{'SEASON':>7}  {'BEST FREE':<22}"
               f"{'THEIRS':>7}{'GAIN':>7}")
    for g in gaps:
        out.append(f"  {g.name[:21]:<22}{g.pos[:4]:<5}{g.season:>7.0f}  "
                   f"{g.best_name[:21]:<22}{g.best_season:>7.0f}{g.gain:>7.0f}")
    notes = [g for g in gaps if g.only_one or g.in_lineup or
             (g.status and g.status.upper() not in ("OK", "ACTIVE"))]
    if notes:
        out.append("")
        for g in notes:
            out.append(f"  {g.describe()}")
    out.append("\nRest-of-season projections, compared only inside a position: "
               "a quarterback's\npoints are not a receiver's. Replacement level "
               "is the best free agent at\nthat position, which in season is "
               "observed rather than estimated.\n\nThis is a roster question, "
               "not a lineup one. None of these change what you\nscore on "
               "Sunday, which is why the waiver view does not raise them.")
    return "\n".join(out)
