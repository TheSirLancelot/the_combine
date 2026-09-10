"""Which weeks you are short, before the week arrives.

The rest of this system answers "what do I do now". This answers "what is
coming", and it exists because the two waiver columns mean different things
depending on the answer. Giving up season value for a one week gain reads very
differently when week 7 is the week three of your receivers are on bye.

Feasibility only. No projections are involved and none are invented: ESPN does
not publish a week 7 projection in week 2, and guessing one would be exactly the
kind of prediction this project has twice measured and thrown away. The question
here is arithmetic -- can this roster legally fill its starting slots that week
-- and that is answerable from the schedule alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .optimize import best_lineup

WEEKS_AHEAD = 4


@dataclass(frozen=True)
class WeekAhead:
    week: int
    slots: int                          # starting slots to fill
    fillable: int                       # how many can legally be filled
    on_bye: list[str] = field(default_factory=list)

    @property
    def short(self) -> int:
        return max(0, self.slots - self.fillable)

    @property
    def ok(self) -> bool:
        return self.short == 0

    def describe(self) -> str:
        byes = ", ".join(self.on_bye) if self.on_bye else "nobody"
        if self.ok:
            return f"week {self.week}: fine, {byes} on bye"
        return (f"week {self.week}: {self.short} slot(s) with nobody to fill "
                f"them, {byes} on bye")


def _fillable(players, slot_list: list[str]) -> int:
    """How many slots this set of players can legally cover.

    The optimizer rather than a per-slot count, because eligibility overlaps: two
    flex-eligible backs can cover RB and RB/WR, or one of them, and counting
    each slot separately would say both are covered twice.
    """
    pool = [{"espn_id": p.player_id, "name": p.name, "pos": p.pos,
             "eligible": set(p.eligible_slots), "playable": True}
            for p in players]
    return len(best_lineup(pool, slot_list, key=lambda _p: 1.0))


def look(client, weeks: int = WEEKS_AHEAD, start: int | None = None
         ) -> list[WeekAhead]:
    """The next few weeks, nearest first."""
    now = int(start if start is not None else client.week)
    slots = client.roster_slots()
    slot_list = [slot for slot, count in slots.items() for _ in range(count)]
    roster = client.matchup(now).my_lineup

    out = []
    for week in range(now + 1, now + 1 + weeks):
        try:
            schedule = client.pro_schedule(week)
        except Exception:
            break                      # past the end of the season
        if not schedule:
            break
        playing = [p for p in roster if p.team and p.team in schedule]
        bye = sorted(p.name for p in roster
                     if p.team and p.team not in schedule)
        out.append(WeekAhead(week=week, slots=len(slot_list),
                             fillable=_fillable(playing, slot_list),
                             on_bye=bye))
    return out


def render(weeks: list[WeekAhead], league_name: str) -> str:
    if not weeks:
        return f"{league_name}: no weeks left to look at."
    out = [f"{league_name} — the next {len(weeks)} weeks", ""]
    out.append(f"  {'WK':<4}{'SLOTS':>6}{'FILLABLE':>10}{'SHORT':>7}  ON BYE")
    for w in weeks:
        out.append(f"  {w.week:<4}{w.slots:>6}{w.fillable:>10}{w.short:>7}  "
                   f"{', '.join(w.on_bye) or '--'}")
    out.append("\nFeasibility only: can the roster legally fill its slots that "
               "week.\nNo projections are involved, because ESPN does not "
               "publish them that far\nout and inventing them would be a guess.")
    return "\n".join(out)
