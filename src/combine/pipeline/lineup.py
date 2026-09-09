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

# A swap worth mentioning. Picked, not derived: under a point of projected edge
# is inside the noise of any projection we have, and flagging it would train us
# to ignore the flags.
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
        return self.bench.projected - self.starter.projected


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


def swaps(starters: list[WeeklyPlayer], bench: list[WeeklyPlayer],
          min_edge: float = MIN_EDGE) -> list[Swap]:
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
        if b.on_bye or b.status in BAD_STATUS:
            continue
        cands = [
            s for s in starters
            if s.player_id not in taken
            and s.slot in b.eligible_slots
            and (b.projected - s.projected) >= min_edge
        ]
        if not cands:
            continue
        worst = min(cands, key=lambda s: s.projected)
        taken.add(worst.player_id)
        out.append(Swap(bench=b, starter=worst, slot=worst.slot))
    return sorted(out, key=lambda s: -s.edge)


def render(m: Matchup, slots: dict[str, int], league_name: str = "") -> str:
    """Compact weekly view. Same discipline as the board: decision-relevant
    fields only, one line per player."""
    starters, bench = split(m.my_lineup)
    starters = order_starters(starters, slots)
    final = all(p.played for p in m.my_lineup if p.starting)
    state = "final" if final else ("live" if any(p.played for p in m.my_lineup) else "pregame")

    head = [
        f"{league_name or 'week'} — week {m.week} ({state})",
        f"{m.my_team} {m.my_proj:.1f} proj"
        + (f" / {m.my_score:.1f} act" if state != "pregame" else "")
        + f"   vs {m.their_team} {m.their_proj:.1f} proj"
        + (f" / {m.their_score:.1f} act" if state != "pregame" else ""),
    ]
    if state == "pregame":
        edge = m.my_proj - m.their_proj
        head.append(f"projected {'up' if edge >= 0 else 'down'} {abs(edge):.1f}")

    def rows(players: list[WeeklyPlayer], label: str) -> list[str]:
        lines = [f"\n{label}",
                 f"  {'SLOT':<8} {'POS':<4} {'PLAYER':<22} {'TM':<3} {'PROJ':>6}"
                 + (f" {'ACT':>6}" if state != "pregame" else "") + "  NOTE"]
        for p in players:
            note = []
            if p.on_bye:
                note.append("BYE")
            if p.status != "OK":
                note.append(p.status)
            lines.append(
                f"  {p.slot:<8} {p.pos:<4} {p.name[:22]:<22} {(p.team or '--'):<3} "
                f"{p.projected:>6.1f}"
                + (f" {p.actual:>6.1f}" if state != "pregame" else "")
                + (("  " + " ".join(note)) if note else ""))
        return lines

    out = head + rows(starters, "STARTERS") + rows(bench, "BENCH")

    bad = problems(starters)
    if bad:
        out.append("\nPROBLEM STARTERS")
        for p in bad:
            out.append(f"  {p.slot:<8} {p.name} ({'bye' if p.on_bye else p.status})")

    hints = swaps(starters, bench)
    if hints:
        out.append("\nBENCH OUTPROJECTS A STARTER (ESPN projection only, not a start/sit call)")
        for s in hints:
            out.append(f"  {s.slot:<8} {s.bench.name} {s.bench.projected:.1f} "
                       f"over {s.starter.name} {s.starter.projected:.1f} "
                       f"(+{s.edge:.1f})")
    return "\n".join(out)
