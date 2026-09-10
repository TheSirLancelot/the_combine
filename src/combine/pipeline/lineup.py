"""The weekly lineup view: who is starting, who is on the bench, and where the
two are obviously the wrong way round.

This is deliberately NOT the start/sit optimizer. It compares ESPN's own weekly
projection and nothing else, because that is the only weekly number the system
currently has. The real call, blended projections plus PFF usage and efficiency,
lands once the PFF client and the id crosswalk are in. What this gives us today
is the input surface that work sits on: slot eligibility, statuses and byes,
read live from the box score.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..platforms import Matchup, WeeklyPlayer

# How big a projection gap has to be before a swap is worth your attention.
#
# Derived, not picked. Over 174,384 comparable pairs in 2025, the chance that
# the higher-projected player actually outscored the other runs:
#
#     gap 0.0-0.5   51.0%      gap 2-3     58.4%
#     gap 0.5-1.0   53.0%      gap 3-4     61.5%
#     gap 1.0-1.5   54.5%      gap 4-6     66.5%
#     gap 1.5-2.0   56.4%      gap 6-8     71.7%
#
# So a flat one-point threshold was surfacing coin flips: right 54% of the time,
# which trains you to ignore the flags.
#
# The gap alone is the wrong unit, though. The same two points means more
# between two defenders, whose outcomes have a standard deviation around 6, than
# between two backs projected 20+, where it is 10.6. Dividing the gap by the
# spread of the two players' outcome distributions lines the curve up across
# positions, which the raw gap does not:
#
#     normalized gap   idp    pass-catcher   qb     rb
#     0.10-0.15        51.8%   53.0%        49.8%  54.9%
#     0.15-0.20        54.2%   55.5%        54.4%  55.4%
#     0.25-0.30        57.9%   56.5%        57.9%  58.7%
#     0.50-0.75        61.7%   65.3%        62.6%  68.2%
#
# 0.25 is the knee: it is where every position clears 57%, and the curve
# flattens above it. In points that is about 1.2 for a low-projected defender
# and 2.6 for a back projected 20+, which is the behaviour we want and a flat
# number cannot give.
MIN_Z = 0.25

# Never go below this however small the spread. A gap under a point is inside
# the rounding of the projections themselves.
MIN_EDGE = 1.0

# Statuses that make a starter a problem regardless of what he is projected for.
BAD_STATUS = frozenset({"O", "IR", "SUSP", "OUT", "D"})


@dataclass(frozen=True)
class Swap:
    bench: WeeklyPlayer
    starter: WeeklyPlayer
    slot: str

    @property
    def edge(self) -> float:
        """Against the starter's effective value, so a ruled-out starter shows
        the full gain rather than a gap against points he will never score."""
        return self.bench.projected - effective(self.starter)


def split(lineup: list[WeeklyPlayer]) -> tuple[list[WeeklyPlayer], list[WeeklyPlayer]]:
    """(starters, bench) with starters in the league's own slot order."""
    starters = [p for p in lineup if p.starting]
    bench = [p for p in lineup if not p.starting]
    bench.sort(key=lambda p: -p.projected)
    return starters, bench


def order_starters(starters: list[WeeklyPlayer], slots: dict[str, int]) -> list[WeeklyPlayer]:
    """Sort by the league's declared slot order, so RCL's IDP slots and DMWD's
    K/DST land where the league itself puts them rather than alphabetically."""
    rank = {slot: i for i, slot in enumerate(slots)}
    return sorted(starters, key=lambda p: (rank.get(p.slot, len(rank)), -p.projected))


def problems(starters: list[WeeklyPlayer]) -> list[WeeklyPlayer]:
    """Starters who cannot or probably will not play. Status beats projection:
    ESPN keeps projecting a player it has already marked OUT."""
    return [p for p in starters if p.on_bye or p.status in BAD_STATUS]


def effective(p: WeeklyPlayer) -> float:
    """What a starter is really worth this week.

    ESPN keeps projecting players it has already marked OUT: a ruled-out back
    still carries 11 points, which is enough to beat every healthy bench
    player and suppress the one swap you most need to be told about. So a
    starter who cannot play is worth zero regardless of what the projection
    says. Status beats projection, the same rule problems() uses.
    """
    if p.on_bye or p.status in BAD_STATUS:
        return 0.0
    return p.projected


def required_edge(a: WeeklyPlayer, b: WeeklyPlayer, dist=None,
                  min_z: float = MIN_Z, floor: float = MIN_EDGE) -> float:
    """How many projected points apart these two have to be before the gap is
    worth reading. Scales with how noisy their outcomes actually are.

    Falls back to the flat floor when there is no outcome history to measure
    spread from, which is the correct behaviour rather than a degraded one: with
    nothing measured, a picked constant is all we have.
    """
    if dist is None:
        return floor
    from .usage import family

    bands = [dist.for_player(family(p.pos), p.projected) for p in (a, b)]
    # getattr rather than attribute access: this must never be the thing that
    # takes a lineup down, and callers hand us whatever distribution they have.
    spreads = [s for s in (getattr(x, "spread", 0.0) for x in bands if x is not None)
               if s and s > 0]
    if not spreads:
        return floor
    pooled = (sum(s ** 2 for s in spreads) / len(spreads)) ** 0.5
    return max(floor, min_z * pooled)


def swaps(starters: list[WeeklyPlayer], bench: list[WeeklyPlayer],
          min_edge: float = MIN_EDGE, dist=None) -> list[Swap]:
    """Bench players outprojecting a starter whose slot they are eligible for.

    Greedy and one-for-one on purpose. A real optimizer solves the whole lineup
    at once, because moving one player frees a slot that changes the next
    decision, and that belongs in start/sit with a blended number behind it.
    Each starter is offered at most once so the same weak starter does not
    generate five near-identical hints.
    """
    out: list[Swap] = []
    taken: set[str] = set()
    for b in sorted(bench, key=lambda p: -p.projected):
        if b.on_bye or b.status in BAD_STATUS or b.locked:
            continue
        cands = [
            s for s in starters
            if s.player_id not in taken
            and s.slot in b.eligible_slots
            and (b.projected - effective(s)) >= required_edge(s, b, dist, floor=min_edge)
            and not s.locked          # his game kicked off, the call is made
        ]
        if not cands:
            continue
        worst = min(cands, key=effective)
        taken.add(worst.player_id)
        out.append(Swap(bench=b, starter=worst, slot=worst.slot))
    return sorted(out, key=lambda s: -s.edge)


def as_candidate(p: WeeklyPlayer, cal=None) -> dict:
    """A WeeklyPlayer in the shape the optimizer wants.

    `cal` makes projections comparable across positions. Measured benefit to the
    lineup is inside noise (+0.2pp of win rate over 430 team-weeks against a
    2.4pp standard error), because a roster offers few cross-position choices.
    It is applied anyway: it is directionally positive and theoretically right,
    and the same correction is decisive on the waiver pool, where hundreds of
    players make cross-position comparison the common case rather than the rare
    one.
    """
    return {
        "espn_id": p.player_id, "name": p.name, "pos": p.pos,
        "eligible": set(p.eligible_slots), "player": p,
        "proj": effective(p) if cal is None else cal.adjust(p.pos, effective(p)),
        "started": p.starting,
        # A player whose game has kicked off cannot be moved in or out, so he is
        # pinned rather than optimized: a suggestion you cannot act on is noise.
        "playable": not p.locked or p.starting,
    }


def optimal_moves(lineup: list[WeeklyPlayer], slots: dict[str, int], cal=None
                  ) -> tuple[list[WeeklyPlayer], list[WeeklyPlayer], float]:
    """(players to start, players to bench, projected points gained).

    Exact assignment over slot eligibility, which sees rearrangements the
    pairwise swap check cannot: moving a receiver into the flex so a back can
    take the RB slot, and a tight end comes off the bench. Backtested on 2025
    at about +3.5pp of win rate and +2.5 points a week against lineups as
    actually fielded, with no forecasting involved.
    """
    from .optimize import best_lineup

    slot_list = [slot for slot, count in slots.items() for _ in range(count)]
    candidates = [as_candidate(p, cal) for p in lineup]
    locked_in = [c for c in candidates if c["player"].locked and c["started"]]
    # Locked starters keep their slots; the optimizer works on what is left.
    for c in locked_in:
        if c["player"].slot in slot_list:
            slot_list.remove(c["player"].slot)
    movable = [c for c in candidates if c not in locked_in]

    chosen = best_lineup(movable, slot_list, key=lambda c: c["proj"])
    chosen_ids = {c["espn_id"] for c in chosen} | {c["espn_id"] for c in locked_in}
    current_ids = {p.player_id for p in lineup if p.starting}

    add = [c["player"] for c in chosen if c["espn_id"] not in current_ids]
    drop = [p for p in lineup if p.starting and p.player_id not in chosen_ids]
    gain = (sum(c["proj"] for c in chosen)
            - sum(effective(p) for p in lineup
                  if p.starting and p.player_id not in {c["espn_id"] for c in locked_in}))
    return add, drop, gain


@dataclass(frozen=True)
class NearMiss:
    """A comparison the tool considered and rejected, with the reason in points.

    Exists so an empty report is legible. "No start/sit questions" alone is
    indistinguishable from a broken report, and it does not say whether nothing
    was close or something missed by a tenth.
    """
    bench: WeeklyPlayer
    starter: WeeklyPlayer
    needed: float             # how far ahead the bench player must be

    @property
    def short_by(self) -> float:
        """Points the bench player would have to gain before this is a
        question. Always positive: these are the ones that did not qualify."""
        return (effective(self.starter) + self.needed) - self.bench.projected


def near_misses(starters: list[WeeklyPlayer], bench: list[WeeklyPlayer],
                dist=None, limit: int = 3) -> list[NearMiss]:
    """The closest comparisons that did NOT qualify, nearest first."""
    out: list[NearMiss] = []
    for b in bench:
        if b.on_bye or b.status in BAD_STATUS or b.locked:
            continue
        cands = [s for s in starters if s.slot in b.eligible_slots and not s.locked]
        if not cands:
            continue
        target = min(cands, key=effective)
        miss = NearMiss(bench=b, starter=target,
                        needed=required_edge(target, b, dist))
        if miss.short_by > 0:
            out.append(miss)
    return sorted(out, key=lambda m: m.short_by)[:limit]


def render(m: Matchup, slots: dict[str, int], league_name: str = "", dist=None,
           pulled_at: str = "") -> str:
    """Compact weekly view. Same discipline as the board: decision-relevant
    fields only, one line per player."""
    starters, bench = split(m.my_lineup)
    starters = order_starters(starters, slots)
    final = all(p.played for p in m.my_lineup if p.starting)
    state = "final" if final else ("live" if any(p.played for p in m.my_lineup) else "pregame")

    # A hand-entered league has no opponent, so the matchup line and the margin
    # would both be fiction.
    have_opponent = bool(m.their_lineup)
    # ESPN moves its weekly projections through the day, so a number here that
    # disagrees with the site by a little is usually just a different moment.
    # Stamping the pull makes that checkable instead of mysterious.
    head = [f"{league_name or 'week'} — week {m.week} ({state})"
            + (f"   projections as of {pulled_at}" if pulled_at else "")]
    if have_opponent:
        head.append(
            f"{m.my_team} {m.my_proj:.1f} proj"
            + (f" / {m.my_score:.1f} act" if state != "pregame" else "")
            + f"   vs {m.their_team} {m.their_proj:.1f} proj"
            + (f" / {m.their_score:.1f} act" if state != "pregame" else ""))
        if state == "pregame":
            edge = m.my_proj - m.their_proj
            head.append(f"projected {'up' if edge >= 0 else 'down'} {abs(edge):.1f}")
    else:
        head.append(f"{m.my_team}: {m.my_proj:.1f} projected from your starters "
                    f"(no opponent entered)")

    def rows(players: list[WeeklyPlayer], label: str) -> list[str]:
        lines = [f"\n{label}",
                 f"  {'SLOT':<8} {'POS':<4} {'PLAYER':<22} {'TM':<3} {'OPP':<7} {'PROJ':>6}"
                 + (f" {'ACT':>6}" if state != "pregame" else "") + "  NOTE"]
        for p in players:
            note = []
            if p.status != "OK":
                note.append(p.status)
            if p.locked and not p.played:
                note.append("LOCK")
            lines.append(
                f"  {p.slot:<8} {p.pos:<4} {p.name[:22]:<22} {(p.team or '--'):<3} "
                f"{p.opponent or '--':<7} {p.projected:>6.1f}"
                + (f" {p.actual:>6.1f}" if state != "pregame" else "")
                + (("  " + " ".join(note)) if note else ""))
        return lines

    out = head + rows(starters, "STARTERS") + rows(bench, "BENCH")

    bad = problems(starters)
    if bad:
        out.append("\nPROBLEM STARTERS")
        for p in bad:
            out.append(f"  {p.slot:<8} {p.name} ({'bye' if p.on_bye else p.status})")

    hints = swaps(starters, bench, dist=dist)
    if hints:
        out.append("\nBENCH OUTPROJECTS A STARTER (ESPN projection only, not a start/sit call)")
        for s in hints:
            out.append(f"  {s.slot:<8} {s.bench.name} {s.bench.projected:.1f} "
                       f"over {s.starter.name} {s.starter.projected:.1f} "
                       f"(+{s.edge:.1f})")
    return "\n".join(out)
