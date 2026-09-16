"""Two players, any position, anywhere in the league, and what swapping costs.

The existing `head_to_head` compares two players already in this week's
matchup. This is the wider question: any two players, whether on your roster,
somebody else's, or nobody's, and what actually happens to your lineup if you
end up with one instead of the other.

Two things it is careful about.

**The impact is the whole lineup, not the pair.** Swapping a player changes who
fills every slot he was eligible for, so the honest number is what the lineup is
worth afterwards minus what it is worth now. A head-to-head difference of
projections ignores the cascade and flatters a swap that frees nothing.

**Opportunities still do not cross positions.** A tight end's targets and a
running back's touches are different units, so the usage lines sit side by side
and are not subtracted. Points compare across positions; the things that produce
them do not.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..platforms import WeeklyPlayer
from .lineup import as_candidate, effective
from .optimize import best_lineup

MINE = "yours"
FREE = "free agent"


@dataclass(frozen=True)
class Side:
    """One player, with everything known about him this week."""

    player: WeeklyPlayer
    owner: str                    # MINE, FREE, or another team's name
    season: float = 0.0

    @property
    def mine(self) -> bool:
        return self.owner == MINE


@dataclass(frozen=True)
class Result:
    a: Side
    b: Side
    week: int
    now: float = 0.0              # lineup value as it stands
    swapped: float = 0.0          # lineup value with the other man instead
    replaced: str = ""            # who the incoming player pushes out
    swappable: bool = False       # is there a lineup change to evaluate at all

    @property
    def delta(self) -> float:
        return self.swapped - self.now

    @property
    def incoming(self) -> Side:
        return self.b if self.a.mine else self.a

    @property
    def outgoing(self) -> Side:
        return self.a if self.a.mine else self.b


def _index(client, week: int) -> dict[str, Side]:
    """Every player worth comparing: rostered anywhere, or free.

    Rostered players come from the box scores, which is the only place weekly
    projections live. Free agents come from the pool and are shaped the same
    way, so the caller never has to know which is which.
    """
    from .waivers import _synthetic

    my_names = {p.player_id for p in client.matchup(week).my_lineup}
    try:
        schedule = client.pro_schedule(week)
    except Exception:
        schedule = {}

    out: dict[str, Side] = {}
    for team, _versus, player in client.player_weeks(week):
        owner = MINE if player.player_id in my_names else team
        out[player.name.lower()] = Side(player=player, owner=owner)

    for raw in client.league.free_agents(size=400):
        player = _synthetic(raw, week, schedule)
        if player is None or player.name.lower() in out:
            continue
        out[player.name.lower()] = Side(
            player=player, owner=FREE,
            season=float(getattr(raw, "projected_total_points", 0.0) or 0.0))
    return out


def resolve(index: dict[str, Side], want: str) -> tuple[Side | None, str]:
    """(match, complaint). Substring, because nobody types 'Jr.' correctly."""
    needle = want.strip().lower()
    if not needle:
        return None, "no name given"
    exact = index.get(needle)
    if exact:
        return exact, ""
    hits = [side for name, side in index.items() if needle in name]
    if len(hits) == 1:
        return hits[0], ""
    if not hits:
        return None, f"`{want}` is not in this league, rostered or free"
    names = ", ".join(sorted(s.player.name for s in hits)[:6])
    return None, f"`{want}` matches {len(hits)}: {names}"


def swap_value(client, week: int, out_player: WeeklyPlayer,
               in_player: WeeklyPlayer, cal=None) -> tuple[float, float, str]:
    """(lineup now, lineup with the swap, who the incoming man displaces).

    The whole lineup both times, because that is the only way a cascade shows
    up: a player who frees a flex slot is worth more than his own projection
    says, and one who only duplicates cover is worth less.
    """
    lineup = client.matchup(week).my_lineup
    slots = client.roster_slots()
    slot_list = [slot for slot, count in slots.items() for _ in range(count)]

    def value(players):
        chosen = best_lineup([as_candidate(p, cal) for p in players], slot_list,
                             key=lambda c: c["proj"])
        return sum(c["proj"] for c in chosen), {c["espn_id"] for c in chosen}

    now, before_ids = value(lineup)
    trial = [p for p in lineup if p.player_id != out_player.player_id]
    trial.append(in_player)
    after, after_ids = value(trial)

    displaced = ""
    for p in lineup:
        if p.player_id in before_ids and p.player_id not in after_ids \
                and p.player_id != out_player.player_id:
            displaced = p.name
            break
    return now, after, displaced


def compare(client, first: str, second: str, week: int | None = None,
            cal=None) -> tuple[Result | None, str]:
    """(result, complaint)."""
    wk = int(week or client.week)
    index = _index(client, wk)
    a, err_a = resolve(index, first)
    b, err_b = resolve(index, second)
    if a is None or b is None:
        return None, " ".join(x for x in (err_a, err_b) if x)
    if a.player.player_id == b.player.player_id:
        return None, "those are the same player"

    # A swap is only meaningful when exactly one of them is yours. Two of your
    # own players is a lineup question the optimizer already answers, and two of
    # somebody else's is not a move you can make.
    swappable = a.mine != b.mine
    now = swapped = 0.0
    replaced = ""
    if swappable:
        out_side = a if a.mine else b
        in_side = b if a.mine else a
        now, swapped, replaced = swap_value(
            client, wk, out_side.player, in_side.player, cal)
    return Result(a=a, b=b, week=wk, now=now, swapped=swapped,
                  replaced=replaced, swappable=swappable), ""


def _line(side: Side, usage, ids, dist, cal) -> list[str]:
    """One player's block: what he is, what he might do, and how he gets there."""
    from . import usage as usage_mod

    p = side.player
    where = {MINE: "yours", FREE: "free agent"}.get(side.owner, side.owner)
    head = (f"{p.name} ({p.pos} {p.team or '--'}) — {where}")
    out = [head, f"  proj {p.projected:.1f}  {p.opponent or 'no game'}"
                 + (f"  [{p.status}]" if p.status not in ("OK", "ACTIVE", "") else "")
                 + ("  LOCKED" if p.locked else "")]

    if cal is not None:
        adjusted = cal.adjust(p.pos, effective(p))
        if abs(adjusted - p.projected) >= 0.05:
            out.append(f"  calibrated {adjusted:.1f} "
                       f"({adjusted - p.projected:+.1f} for {p.pos})")
    if side.season:
        out.append(f"  season {side.season:.0f}")
    if dist is not None:
        band = dist.for_player(usage_mod.family(p.pos), p.projected)
        if band is not None:
            out.append(f"  floor {band.floor:.1f}  ceiling {band.ceiling:.1f}  "
                       f"boom {band.boom * 100:.0f}%  bust {band.bust * 100:.0f}%")
    u = usage_mod.for_espn(usage, ids, p.player_id)
    if u:
        out.append(f"  {u.line(p.pos)}")
    return out


def render(result: Result, usage, ids, dist=None, cal=None) -> str:
    a, b = result.a, result.b
    out = [f"week {result.week}: {a.player.name} vs {b.player.name}", ""]
    out += _line(a, usage, ids, dist, cal)
    out.append("")
    out += _line(b, usage, ids, dist, cal)
    out.append("")

    edge = a.player.projected - b.player.projected
    lead, trail = (a, b) if edge >= 0 else (b, a)
    if abs(edge) < 0.05:
        out.append("Projections are level.")
    else:
        out.append(f"{lead.player.name} by {abs(edge):.1f} projected over "
                   f"{trail.player.name}.")

    from . import usage as usage_mod

    if usage_mod.family(a.player.pos) != usage_mod.family(b.player.pos):
        out.append("Different positions, so the usage lines sit side by side "
                   "rather than\nbeing subtracted: targets and touches are not "
                   "the same unit.")

    if not result.swappable:
        both = "both yours" if a.mine and b.mine else (
            "neither is yours" if not a.mine and not b.mine else "")
        out.append("")
        out.append(f"No swap to price: {both}." if both else "No swap to price.")
        return "\n".join(out)

    out.append("")
    out.append(f"SWAPPING {result.outgoing.player.name} for "
               f"{result.incoming.player.name}")
    out.append(f"  lineup now      {result.now:.1f}")
    out.append(f"  lineup after    {result.swapped:.1f}")
    out.append(f"  difference      {result.delta:+.1f}")
    if result.replaced:
        out.append(f"  {result.incoming.player.name} also pushes "
                   f"{result.replaced} out of the lineup")
    out.append("\nThe whole lineup both times, not the pair: a player who frees "
               "a slot is\nworth more than his own projection says, and one who "
               "only duplicates\ncover is worth less.")
    if result.incoming.owner not in (MINE, FREE):
        out.append(f"\n{result.incoming.player.name} is on "
                   f"{result.incoming.owner}, so this is a trade to propose "
                   f"rather than a move to make.")
    return "\n".join(out)
