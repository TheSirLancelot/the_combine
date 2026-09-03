"""What your roster still needs, once the draft is under way.

Rounds 1 to 3 are best-player-available. After that the question changes: a
fourth running back is worth less than a first tight end no matter what the
projections say, because you can only start so many. ESPN's roster endpoint
populates live during a draft, so this reads what you actually have.

Slot filling is greedy and deliberately simple: dedicated slots first, then
flex with whoever is left over. That can be marginally suboptimal in a corner
case (a player eligible for two dedicated slots), but it never misreports an
empty slot as filled, which is the failure that would matter.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..platforms import PlayerState
from .vorp import SLOT_ELIGIBILITY

# Dedicated slots get filled before flex ones, so a flex never eats a player
# that a required slot needs.
FLEX_SLOTS = {"RB/WR", "WR/TE", "RB/WR/TE", "OP", "DP"}


@dataclass
class Needs:
    filled: dict[str, list[str]] = field(default_factory=dict)
    empty: dict[str, int] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)
    bye_load: Counter = field(default_factory=Counter)
    bench_players: list[str] = field(default_factory=list)

    @property
    def open_positions(self) -> set[str]:
        """Positions that could still fill a starting slot."""
        out: set[str] = set()
        for slot in self.empty:
            out.update(SLOT_ELIGIBILITY.get(slot, []))
        return out


def compute(roster: list[PlayerState], slots: dict[str, int],
            byes: dict[str, int] | None = None) -> Needs:
    byes = byes or {}
    needs = Needs()
    remaining = list(roster)

    ordered = ([s for s in slots if s not in FLEX_SLOTS]
               + [s for s in slots if s in FLEX_SLOTS])

    for slot in ordered:
        eligible_positions = SLOT_ELIGIBILITY.get(slot, [])
        for _ in range(slots.get(slot, 0)):
            hit = next((p for p in remaining if p.pos in eligible_positions), None)
            if hit is None:
                needs.empty[slot] = needs.empty.get(slot, 0) + 1
            else:
                remaining.remove(hit)
                needs.filled.setdefault(slot, []).append(hit.name)
                bye = byes.get(hit.name)
                if bye:
                    needs.bye_load[bye] += 1

    needs.counts = Counter(p.pos for p in roster)
    needs.bench_players = [p.name for p in remaining]
    return needs


def render(needs: Needs, roster_size: int, drafted: int) -> str:
    lines = [f"roster {drafted}/{roster_size}"]

    if needs.empty:
        gaps = ", ".join(f"{slot} x{n}" for slot, n in needs.empty.items())
        lines.append(f"STARTING SLOTS UNFILLED: {gaps}")
        lines.append(f"  positions that fill them: {', '.join(sorted(needs.open_positions))}")
    else:
        lines.append("all starting slots filled. everything from here is depth.")

    if needs.filled:
        lines.append("starters:")
        for slot, names in needs.filled.items():
            lines.append(f"  {slot:<9} {', '.join(names)}")
    if needs.bench_players:
        lines.append(f"bench: {', '.join(needs.bench_players)}")

    stacked = [(wk, n) for wk, n in sorted(needs.bye_load.items()) if n >= 3]
    if stacked:
        worst = ", ".join(f"week {wk}: {n} starters" for wk, n in stacked)
        lines.append(f"BYE PILEUP: {worst}")

    return "\n".join(lines)
