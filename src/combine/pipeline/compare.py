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


# --- the deeper comparison --------------------------------------------------

AHEAD = 5          # weeks of schedule to show


def espn_history(client, player_ids, through: int) -> dict[str, dict]:
    """{espn id: {"weeks": {week: points}, "season": full-year projection}}.

    The weeks are ACTUALS, not projections, and that is not a compromise. ESPN
    publishes a projection for the CURRENT week and a season total, and nothing
    for the weeks after: verified on the roster objects, where `stats` carries
    keys 0, 1 and 2 in week 2 and no more. So a week-by-week forecast cannot be
    built from anything we have, and inventing one is the move this project has
    twice measured and thrown away. What IS real is what each man has actually
    scored, week by week, which is the better half of that question anyway.

    One call for both, because they come from the same place and a second round
    trip for the season number would be waste. It also has to be this call
    rather than the roster: `season_values` only covers MY team, so a player on
    a rival roster came back as zero, which read as "projected for nothing"
    rather than "not looked up".
    """
    numeric = [int(i) for i in player_ids if str(i).isdigit()]
    if not numeric:
        return {}
    try:
        found = client.league.player_info(playerId=numeric)
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for player in (found if isinstance(found, list) else [found]):
        if player is None:
            continue
        weeks = {
            int(week): float(stats.get("points") or 0.0)
            for week, stats in (getattr(player, "stats", {}) or {}).items()
            if isinstance(week, int) and 0 < week < through
            and stats.get("points") is not None
        }
        out[str(getattr(player, "playerId", ""))] = {
            "weeks": weeks,
            "season": float(getattr(player, "projected_total_points", 0.0) or 0.0),
        }
    return out


def schedule_ahead(client, team: str | None, from_week: int,
                   weeks: int = AHEAD) -> list[tuple[int, str]]:
    """[(week, 'vs KC' | '@ KC' | 'BYE')] for the next few weeks.

    A bye is a real zero and the look-ahead view already turns on it, so it
    belongs in a comparison too: two players a point apart are not equivalent
    when one of them has a bye inside the fantasy playoffs.
    """
    if not team:
        return []
    out = []
    for week in range(from_week, from_week + weeks):
        try:
            game = client.pro_schedule(week).get(team)
        except Exception:
            break
        out.append((week, game.label if game else "BYE"))
    return out


def stat_rows(a: Side, b: Side, usage, ids) -> list[tuple[str, float | None,
                                                          float | None, int]]:
    """PFF numbers side by side, only when the positions make that meaningful.

    Same family or nothing. A tight end's targets against a running back's
    touches is not a comparison, and lining the two lists up by index would
    put one man's yards per route run beside the other's yards after contact.
    """
    from . import usage as usage_mod

    if usage_mod.family(a.player.pos) != usage_mod.family(b.player.pos):
        return []
    ua = usage_mod.for_espn(usage, ids, a.player.player_id)
    ub = usage_mod.for_espn(usage, ids, b.player.player_id)
    if not ua or not ub:
        return []
    parts_a = {label: (value, dp) for label, value, dp in ua.parts(a.player.pos)}
    parts_b = {label: (value, dp) for label, value, dp in ub.parts(b.player.pos)}
    rows = []
    for label, (value, dp) in parts_a.items():
        other = parts_b.get(label, (None, dp))[0]
        if value is None and other is None:
            continue
        rows.append((label, value, other, dp))
    return rows


@dataclass(frozen=True)
class Detail:
    """Everything the deeper comparison found."""

    result: Result
    form: dict[str, dict[int, float]]           # espn id -> week -> points
    schedule: dict[str, list[tuple[int, str]]]  # espn id -> [(week, opponent)]
    stats: list[tuple[str, float | None, float | None, int]]
    season_a: float = 0.0
    season_b: float = 0.0

    @property
    def season_delta(self) -> float:
        """ESPN's full-season projections, differenced. Both count the games
        already played, so the difference is meaningful while the totals are
        not a rest-of-season number."""
        return self.season_b - self.season_a


def detail(client, result: Result, usage, ids, season: int,
           season_values: dict[str, float] | None = None) -> Detail:
    ids_wanted = [result.a.player.player_id, result.b.player.player_id]
    values = season_values or {}
    history = espn_history(client, ids_wanted, result.week)

    def season_for(side: Side) -> float:
        pid = side.player.player_id
        return (side.season
                or history.get(pid, {}).get("season", 0.0)
                or values.get(pid, 0.0))

    return Detail(
        result=result,
        form={pid: row["weeks"] for pid, row in history.items()},
        schedule={p.player_id: schedule_ahead(client, p.team, result.week)
                  for p in (result.a.player, result.b.player)},
        stats=stat_rows(result.a, result.b, usage, ids),
        season_a=season_for(result.a),
        season_b=season_for(result.b),
    )


def render_detail(d: Detail) -> str:
    a, b = d.result.a.player, d.result.b.player
    out = []

    if d.form:
        weeks = sorted({w for f in d.form.values() for w in f})
        if weeks:
            out.append("FORM so far, points actually scored")
            head = "  " + " ".join(f"{'wk' + str(w):>7}" for w in weeks)
            out.append(f"  {'':<22}{head.strip()}")
            for player in (a, b):
                got = d.form.get(player.player_id, {})
                cells = " ".join(
                    f"{got[w]:>7.1f}" if w in got else f"{'--':>7}" for w in weeks)
                total = sum(got.values())
                out.append(f"  {player.name[:21]:<22}{cells}   total {total:>6.1f}")
            out.append("")

    if d.season_a or d.season_b:
        out.append("SEASON, ESPN's full-year projection")
        out.append(f"  {a.name[:21]:<22}{d.season_a:>8.0f}")
        out.append(f"  {b.name[:21]:<22}{d.season_b:>8.0f}")
        favours = b.name if d.season_delta > 0 else a.name
        out.append(f"  {'difference':<22}{d.season_delta:>+8.0f}"
                   f"   favours {favours}")
        out.append("  These count the whole year including games already "
                   "played, so read\n  the difference rather than the totals. "
                   "ESPN publishes no weekly\n  projection past the current "
                   "week, so a week-by-week forecast is\n  not available from "
                   "any source here and is not invented.")
        out.append("")

    if any(d.schedule.values()):
        out.append("SCHEDULE ahead")
        for player in (a, b):
            games = d.schedule.get(player.player_id, [])
            cells = "  ".join(f"wk{w} {label}" for w, label in games)
            out.append(f"  {player.name[:21]:<22}{cells}")
        byes = [player.name for player in (a, b)
                if any(label == "BYE" for _w, label in d.schedule.get(
                    player.player_id, []))]
        if byes:
            out.append(f"  bye inside this window: {', '.join(byes)}")
        out.append("")

    if d.stats:
        out.append(f"PFF, same position so these compare ({a.pos})")
        out.append(f"  {'':<10}{a.name[:16]:>17}{b.name[:16]:>17}")
        for label, va, vb, dp in d.stats:
            left = f"{va:.{dp}f}" if va is not None else "--"
            right = f"{vb:.{dp}f}" if vb is not None else "--"
            out.append(f"  {label:<10}{left:>17}{right:>17}")
    else:
        out.append("PFF: different position groups, so the usage numbers are "
                   "not comparable.\nA tight end's targets and a back's touches "
                   "are different units.")
    return "\n".join(out)
