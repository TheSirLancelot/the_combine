"""Would anyone on the wire actually improve the lineup this week.

The naive version of this question is useless. Asking "does a free agent
out-project one of my starters" returned 94 players in RCL, and enforcing slot
eligibility only narrowed it to 18, every one of them a cornerback or linebacker
projected to beat a defensive end. Corrected for ESPN's per-position bias, 17 of
those vanished. So two things are load-bearing before anything is worth showing:
eligibility, and calibration.

The value of an add is NOT "this player beats that starter". It is what the
lineup is worth afterwards:

    best_lineup(roster - drop + candidate)  -  best_lineup(roster)

which reuses the optimizer, and therefore handles the cascades a one-for-one
comparison cannot see: the candidate starts, someone shifts into the flex, and a
third player comes off. It also separates the add from the drop, which is the
point. You do not drop the starter being replaced, you drop the least useful
player on the roster, and he is usually already on the bench.

The drop is chosen on SEASON value, never on this week's projection. Ranking by
this week would cheerfully drop your best receiver during his bye, because he
projects zero.

Two currencies, reported separately and deliberately not combined. Points gained
this week, and season value surrendered. A +1.5 this week that costs a useful
bench asset is usually a bad trade, and no single number says so.

READ ONLY. This says what a claim would be worth. It never makes one, and there
is no add or drop anywhere in this repo.

TODO: kickers and team defenses. Streaming a defense is the classic waiver win,
DMWD's D/ST are under-projected by 1.17 +-0.37 points which suggests real value
there, and none of it can be backtested because PFF publishes no stat lines for
kickers or team defenses. Deferred rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..platforms import ProGame, WeeklyPlayer
from .calibration import EMPTY, Calibration
from .lineup import BAD_STATUS, as_candidate, effective, required_edge, split
from .optimize import best_lineup

# How many of the least valuable bench players to consider dropping. The best
# drop is usually the worst one, but not always: the worst player may be the only
# body eligible for a slot the lineup needs, and dropping him costs more than he
# is worth. Three is enough to catch that without running the optimizer hundreds
# of times.
DROP_CHOICES = 3

# Kickoff far enough out that a synthetic free agent is never treated as locked.
_UNPLAYED = 4_000_000_000_000


@dataclass(frozen=True)
class Candidate:
    league: str
    week: int
    name: str
    pos: str
    team: str | None
    week_proj: float          # ESPN's number, as published
    season_proj: float
    drop_name: str
    drop_pos: str
    drop_season_proj: float
    week_gain: float          # what the lineup gains, calibrated
    displaces: str | None     # who he pushes out of the lineup this week
    correction: float = 0.0   # calibration applied to his projection
    correction_n: int = 0     # how many observations that correction rests on

    @property
    def correction_carries_it(self) -> bool:
        """The correction is doing the work, so the reader should know how well
        measured it is.

        DeForest Buckner is the worked example: he projects 6.6 against a starter
        at 7.4, so on ESPN's raw numbers he is worse, and he only ranks first
        because defensive tackles measured +2.49 on 40 player-weeks. That is a
        real finding resting on a thin sample, which is worth saying out loud.

        Only an UPWARD correction can carry a candidate. One that pushed him down
        and left him clearing the bar anyway is the opposite of a caveat.
        """
        return self.correction > 0 and self.correction >= self.week_gain

    @property
    def clears_despite_correction(self) -> bool:
        """He was marked down for his position and still qualifies."""
        return self.correction < 0

    @property
    def season_cost(self) -> float:
        """Season value given up. Positive means the add is also an upgrade for
        the rest of the year; negative means you are trading the season for a
        week."""
        return self.season_proj - self.drop_season_proj

    @property
    def trades_down(self) -> bool:
        return self.season_cost < 0

    def describe(self) -> str:
        line = (f"{self.name} ({self.pos} {self.team or '--'}) "
                f"{self.week_proj:.1f} projected, +{self.week_gain:.1f} to the lineup")
        if self.displaces:
            line += f", starting over {self.displaces}"
        if self.correction_carries_it:
            line += (f"\n    NOTE: ranks here only because {self.pos} projections "
                     f"are corrected UP by {self.correction:.1f}, measured on "
                     f"{self.correction_n} player-weeks. On ESPN's raw number he "
                     f"does not clear the bar.")
        elif self.clears_despite_correction:
            line += (f"\n    clears the bar even after {self.pos} projections are "
                     f"marked DOWN {abs(self.correction):.1f} for being "
                     f"systematically over-projected")
        line += f"\n    drop {self.drop_name} ({self.drop_pos})"
        if self.trades_down:
            line += (f": costs {abs(self.season_cost):.0f} projected points of "
                     f"season value, so this buys a week and pays for it later")
        else:
            line += (f": gains {self.season_cost:.0f} projected points of season "
                     f"value too")
        return line


def _synthetic(player, week: int) -> WeeklyPlayer | None:
    """A free agent in the shape the lineup code expects.

    ESPN's pool players carry a weekly projection and their eligible slots, which
    is everything needed to ask whether they would start.
    """
    stats = (getattr(player, "stats", {}) or {}).get(week, {}) or {}
    proj = stats.get("projected_points") or 0.0
    slots = frozenset(getattr(player, "eligibleSlots", ()) or ())
    if proj <= 0 or not slots:
        return None      # on bye, not projected, or no slot data: nothing to say
    status = (getattr(player, "injuryStatus", "") or "OK").upper()
    if status in BAD_STATUS:
        return None      # ruled out; adding him this week achieves nothing
    return WeeklyPlayer(
        player_id=str(getattr(player, "playerId", "")),
        name=getattr(player, "name", "?"),
        team=getattr(player, "proTeam", None),
        pos=getattr(player, "position", "?"),
        slot="FA",
        eligible_slots=slots,
        status=status,
        projected=float(proj),
        game=ProGame(opponent="?", home=True, kickoff_ms=_UNPLAYED),
    )


def _value(players: list[WeeklyPlayer], slot_list: list[str],
           cal: Calibration) -> tuple[float, set[str]]:
    """(calibrated value of the best lineup, the ids in it)."""
    chosen = best_lineup([as_candidate(p, cal) for p in players], slot_list,
                         key=lambda c: c["proj"])
    return sum(c["proj"] for c in chosen), {c["espn_id"] for c in chosen}


def find(client, week: int | None = None, cal: Calibration | None = None,
         pool_size: int = 350, limit: int = 5,
         season_value: dict[str, float] | None = None,
         dist=None) -> list[Candidate]:
    """Pool players who would improve this week's lineup, best first.

    The gain has to clear the same measured threshold a bench-for-starter swap
    does, scaled to how widely those positions actually scatter. Without it this
    reported three near-identical defenses for four tenths of a point, which is
    the noise the threshold work exists to suppress.
    """
    cal = cal or EMPTY
    wk = int(week or client.week)
    lineup = client.matchup(wk).my_lineup
    slots = client.roster_slots()
    slot_list = [slot for slot, count in slots.items() for _ in range(count)]

    base_value, base_ids = _value(lineup, slot_list, cal)
    starters, bench = split(lineup)

    # Season value per rostered player, for the drop decision. Falls back to this
    # week's projection only if the season numbers are unavailable, which is
    # worse but better than refusing to answer.
    values = season_value or {}
    ranked_drops = sorted(
        (p for p in lineup if p.player_id not in base_ids),
        key=lambda p: values.get(p.player_id, p.projected))[:DROP_CHOICES]
    if not ranked_drops:
        # Every rostered player is in the lineup, so a drop must cost a starter.
        ranked_drops = sorted(lineup, key=lambda p: values.get(p.player_id,
                                                               p.projected))[:1]

    # The cheap filter. A candidate can only help if his calibrated projection
    # beats the weakest calibrated starter he is eligible to replace. Without
    # this the optimizer would run hundreds of times for nothing.
    weakest: dict[str, float] = {}
    for s in starters:
        weakest[s.slot] = min(weakest.get(s.slot, 1e9),
                              cal.adjust(s.pos, effective(s)))

    out: list[Candidate] = []
    for raw in client.league.free_agents(size=pool_size):
        candidate = _synthetic(raw, wk)
        if candidate is None:
            continue
        adjusted = cal.adjust(candidate.pos, candidate.projected)
        if not any(adjusted > weakest.get(slot, 1e9)
                   for slot in candidate.eligible_slots):
            continue

        best: tuple[float, WeeklyPlayer, str | None] | None = None
        for drop in ranked_drops:
            trial = [p for p in lineup if p.player_id != drop.player_id]
            trial.append(candidate)
            value, ids = _value(trial, slot_list, cal)
            gain = value - base_value
            if best is None or gain > best[0]:
                displaced = next(
                    (p.name for p in starters
                     if p.player_id in base_ids and p.player_id not in ids), None)
                best = (gain, drop, displaced)

        gain, drop, displaced = best
        # The bar: whoever he displaces, scaled by their outcome spread. With no
        # displaced starter identified, fall back to the flat floor.
        target = next((p for p in starters if p.name == displaced), None)
        needed = required_edge(target, candidate, dist) if target else 1.0
        if gain < needed:
            continue
        if candidate.player_id not in _value(
                [p for p in lineup if p.player_id != drop.player_id] + [candidate],
                slot_list, cal)[1]:
            continue      # he does not actually make the lineup
        out.append(Candidate(
            league=client.slug, week=wk, name=candidate.name, pos=candidate.pos,
            team=candidate.team, week_proj=candidate.projected,
            season_proj=float(getattr(raw, "projected_total_points", 0.0) or 0.0),
            drop_name=drop.name, drop_pos=drop.pos,
            drop_season_proj=values.get(drop.player_id, 0.0),
            week_gain=gain, displaces=displaced,
            correction=cal.offset(candidate.pos),
            correction_n=(cal.biases.get(candidate.pos.upper()).n
                          if cal.biases.get(candidate.pos.upper()) else 0),
        ))

    out.sort(key=lambda c: -c.week_gain)
    return out[:limit]


def season_values(client) -> dict[str, float]:
    """{espn player id: ESPN's season projection} for my roster.

    Season numbers live on the roster objects rather than the box score, which is
    why this is a separate call from the weekly lineup.
    """
    try:
        team = next(t for t in client.league.teams
                    if str(t.team_id) == str(client.cfg.team_id))
    except (StopIteration, AttributeError):
        return {}
    return {str(getattr(p, "playerId", "")):
            float(getattr(p, "projected_total_points", 0.0) or 0.0)
            for p in team.roster}


def render(candidates: list[Candidate], league_name: str, week: int) -> str:
    if not candidates:
        return (f"{league_name} — week {week}\n"
                f"Nobody on the wire improves the lineup. That is the normal "
                f"answer:\nthe pool is unrostered for a reason.")
    out = [f"{league_name} — week {week}", "WAIVER UPGRADES", ""]
    out.append(f"  {'':<3}{'ADD':<22}{'POS':<5}{'PROJ':>6}{'WEEK':>7}"
               f"{'SEASON':>8}  {'DROP':<22}{'STARTS OVER'}")
    for i, c in enumerate(candidates, start=1):
        flag = "*" if c.correction_carries_it else " "
        out.append(f"  {str(i) + flag:<3}{c.name[:21]:<22}{c.pos[:4]:<5}"
                   f"{c.week_proj:>6.1f}{c.week_gain:>+7.1f}"
                   f"{c.season_cost:>+8.1f}  "
                   f"{f'{c.drop_name} ({c.drop_pos})'[:21]:<22}"
                   f"{c.displaces or '--'}")
    out.append("")
    for i, c in enumerate(candidates, start=1):
        if c.correction_carries_it:
            out.append(f"  {i}* ranks here only because {c.pos} projections are "
                       f"corrected UP by {c.correction:.1f},\n     measured on "
                       f"{c.correction_n} player-weeks. On ESPN's raw number he "
                       f"does not clear the bar.")
        elif c.clears_despite_correction:
            out.append(f"  {i}  clears the bar even after {c.pos} projections are "
                       f"marked DOWN\n     {abs(c.correction):.1f} for being "
                       f"systematically over-projected.")
    out.append("\nWEEK is what the whole lineup is worth afterwards, not a head "
               "to head,\nso it already accounts for who shifts where. SEASON is "
               "what the drop\ncosts or gains for the rest of the year, kept "
               "separate because a week\nis not worth a season.\n\nEach row is an "
               "alternative, not a sequence: every one is measured\nagainst the "
               "lineup you have now, which is why they can name the same drop.")
    return "\n".join(out)
