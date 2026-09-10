"""Start/sit calls and head-to-head comparisons.

The projection and the usage are kept apart on purpose. ESPN's weekly
projection is the only weekly number in the system, so it sets the order.
PFF's usage says whether that number rests on a role the player actually has,
which is the part a projection cannot tell you. When the two agree the call is
easy; when they disagree that is the finding, and this says so rather than
averaging them into a single confident-looking number that means nothing.

Only slots with a real question are printed. A slot where the starter is
obviously right does not need a paragraph, and a report you have to scan is a
report you stop reading in week 3.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..platforms import Matchup, WeeklyPlayer
from . import usage as usage_mod
from .lineup import BAD_STATUS, near_misses, optimal_moves, problems, split, swaps

# Both picked, not derived, and both worth revisiting once we have a few weeks
# of projected-versus-actual to calibrate against. A point is inside the noise
# of any projection we have; three points is roughly the gap that survives a
# normal week's variance.
CLEAR = 3.0
LEAN = 1.0


@dataclass(frozen=True)
class Call:
    slot: str
    starter: WeeklyPlayer
    bench: WeeklyPlayer
    proj_edge: float                  # bench projection minus starter's
    opp_edge: float | None            # per-game opportunity difference
    verdict: str
    reasons: tuple[str, ...]

    @property
    def agrees(self) -> bool:
        return self.opp_edge is not None and self.opp_edge > 0


def opportunity_edge(a: WeeklyPlayer, b: WeeklyPlayer,
                     usage: dict[int, usage_mod.Usage], ids: dict[str, int]
                     ) -> tuple[float | None, str]:
    """(a's per-game opportunities minus b's, why it is missing).

    Returns None across position families on purpose. Targets, touches,
    dropbacks and defensive snaps are different units, so the difference
    between a tight end and a running back is not a number.
    """
    if usage_mod.family(a.pos) != usage_mod.family(b.pos):
        return None, "different positions, so their usage is not comparable"
    ua = usage_mod.for_espn(usage, ids, a.player_id)
    ub = usage_mod.for_espn(usage, ids, b.player_id)
    opp_a = ua.opportunities(a.pos) if ua else None
    opp_b = ub.opportunities(b.pos) if ub else None
    if opp_a is None or opp_b is None:
        return None, "no PFF usage on one of them"
    return opp_a - opp_b, ""


def _verdict(proj_edge: float, opp_edge: float | None) -> tuple[str, tuple[str, ...]]:
    """Projection sets the direction; usage sets the confidence.

    CLEAR / LEAN / COIN FLIP are neutral so both the lineup review and the
    head-to-head can phrase them for their own context.
    """
    if opp_edge is None:
        return ("LEAN" if proj_edge >= CLEAR else "COIN FLIP"), (
            "the projection alone, with no usage check behind it",)
    if opp_edge > 0:
        if proj_edge >= CLEAR:
            return "CLEAR", ("projection and usage agree",)
        return "LEAN", ("usage agrees, but the projection gap is small",)
    return "COIN FLIP", (
        "they disagree: the projection prefers one, the usage prefers the other",)


def review(m: Matchup, usage: dict[int, usage_mod.Usage], ids: dict[str, int],
           dist=None) -> tuple[list[Call], list[WeeklyPlayer]]:
    """(calls worth making, starters who cannot play). Both can be empty, and
    an empty report is the correct output for a lineup that is already right."""
    starters, bench = split(m.my_lineup)
    calls: list[Call] = []
    for sw in swaps(starters, bench, dist=dist):
        opp_edge, why = opportunity_edge(sw.bench, sw.starter, usage, ids)
        verdict, reasons = _verdict(sw.edge, opp_edge)
        if why:
            reasons = (*reasons, why)
        calls.append(Call(slot=sw.slot, starter=sw.starter, bench=sw.bench,
                          proj_edge=sw.edge, opp_edge=opp_edge,
                          verdict=verdict, reasons=reasons))
    return calls, problems(starters)


def _player_block(p: WeeklyPlayer, u: usage_mod.Usage | None, in_season: bool,
                  indent: str = "    ", band=None) -> list[str]:
    tags = [t for t in (p.status if p.status != "OK" else "",
                        "LOCKED" if p.locked else "") if t]
    head = (f"{indent}{p.name} ({p.pos} {p.team or '--'}) {p.opponent or ''}".rstrip()
            + f"  proj {p.projected:.1f}"
            + (f"  [{' '.join(tags)}]" if tags else ""))
    out = [head]
    if u is None:
        out.append(f"{indent}  no PFF usage on file")
        return out
    out.append(f"{indent}  {u.line(p.pos)}")
    for note in u.caveats(in_season, season=config.SEASON):
        out.append(f"{indent}  ({note})")
    if band is not None:
        out.append(f"{indent}  {band.describe()}")
    return out


def render(m: Matchup, calls: list[Call], hurt: list[WeeklyPlayer],
           usage: dict[int, usage_mod.Usage], ids: dict[str, int],
           in_season: bool, league_name: str = "",
           slots: dict[str, int] | None = None, dist=None) -> str:
    out = [f"{league_name or 'start/sit'} — week {m.week}"]
    if m.their_lineup:
        out.append(f"{m.my_team} {m.my_proj:.1f} vs {m.their_team} {m.their_proj:.1f}")
    else:
        out.append(f"{m.my_team}: {m.my_proj:.1f} projected (no opponent entered)")

    # The one thing here that is arithmetic rather than judgement, so it leads.
    if slots:
        add, drop, gain = optimal_moves(m.my_lineup, slots)
        if gain > 0.05 and add:
            out.append(f"\nLINEUP IS {gain:.1f} PROJECTED POINTS SHORT OF OPTIMAL")
            for a in add:
                out.append(f"  START  {a.name} ({a.pos}) {a.projected:.1f}")
            for d in drop:
                reason = " (cannot play)" if d.on_bye or d.status in BAD_STATUS else ""
                out.append(f"  BENCH  {d.name} ({d.pos}) {d.projected:.1f}{reason}")
            out.append("  exact slot assignment, not a prediction. worth about "
                       "+3.5pp of win rate in the 2025 backtest.")

    if hurt:
        out.append("\nCANNOT PLAY, STILL STARTING")
        for p in hurt:
            out.append(f"  {p.slot:<8} {p.name} ({'bye' if p.on_bye else p.status})")

    if not calls:
        out.append("\nNo start/sit questions: no bench player is projected far "
                   "enough ahead of a starter he could replace.")
        starters, bench = split(m.my_lineup)
        close = near_misses(starters, bench, dist)
        if close:
            out.append("\nCLOSEST COMPARISONS, none of them close enough")
            for c in close:
                out.append(
                    f"  {c.bench.name} {c.bench.projected:.1f} would need "
                    f"{c.short_by:.1f} more to be worth weighing against "
                    f"{c.starter.name} {c.starter.projected:.1f} "
                    f"({c.starter.slot}); that pair needs a {c.needed:.1f} point edge")
            out.append("  the edge required differs per pair, because it scales "
                       "with how widely\n  those two positions actually scatter "
                       "at those projections. see: combine glossary")
        return "\n".join(out)

    for c in sorted(calls, key=lambda x: -x.proj_edge):
        # In a lineup the CLEAR case is an instruction, so say the instruction.
        label = "SWAP" if c.verdict == "CLEAR" else c.verdict
        out.append(f"\n{label}  {c.slot}  +{c.proj_edge:.1f} projected")
        def band_for(p):
            return dist.for_player(usage_mod.family(p.pos), p.projected) if dist else None

        out.append("  IN")
        out += _player_block(c.bench, usage_mod.for_espn(usage, ids, c.bench.player_id),
                             in_season, band=band_for(c.bench))
        out.append("  OUT")
        out += _player_block(c.starter, usage_mod.for_espn(usage, ids, c.starter.player_id),
                             in_season, band=band_for(c.starter))
        if c.opp_edge is not None:
            direction = "more" if c.opp_edge > 0 else "fewer"
            out.append(f"  usage: {abs(c.opp_edge):.1f} {direction} opportunities "
                       f"a game for {c.bench.name}")
        for r in c.reasons:
            out.append(f"  {r}")
    return "\n".join(out)


def head_to_head(a: WeeklyPlayer, b: WeeklyPlayer, usage: dict[int, usage_mod.Usage],
                 ids: dict[str, int], in_season: bool, week: int) -> str:
    """Two players, same league, same week. Used when the pair you care about
    are not a legal swap for each other, which the lineup review will not
    surface."""
    ua = usage_mod.for_espn(usage, ids, a.player_id)
    ub = usage_mod.for_espn(usage, ids, b.player_id)
    edge = a.projected - b.projected
    lead, trail = (a, b) if edge >= 0 else (b, a)
    opp_edge, why = opportunity_edge(lead, trail, usage, ids)
    verdict, reasons = _verdict(abs(edge), opp_edge)

    out = [f"week {week}: {a.name} vs {b.name}", ""]
    out += _player_block(a, ua, in_season, indent="  ")
    out.append("")
    out += _player_block(b, ub, in_season, indent="  ")
    out.append("")
    if abs(edge) < LEAN:
        out.append(f"Projections are level ({abs(edge):.1f} apart).")
    else:
        out.append(f"{lead.name} by {abs(edge):.1f} projected over {trail.name}.")
    if opp_edge is not None:
        word = "more" if opp_edge > 0 else "fewer"
        out.append(f"{lead.name} sees {abs(opp_edge):.1f} {word} opportunities a game.")
    elif why:
        out.append(why.capitalize() + ".")
    out.append(f"{verdict}: {reasons[0]}")
    if a.locked or b.locked:
        out.append("(one of them has already kicked off)")
    return "\n".join(out)
