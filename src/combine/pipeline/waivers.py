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

Team defenses are in. Kickers are deliberately out, and the reason is measured
rather than assumed -- see NEVER_STREAM.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..platforms import ProGame, WeeklyPlayer
from .calibration import EMPTY, Calibration
from .lineup import (
    BAD_STATUS,
    MIN_EDGE,
    as_candidate,
    effective,
    required_edge,
    split,
)
from .optimize import best_lineup

# How many of the least valuable bench players to consider dropping. The best
# drop is usually the worst one, but not always: the worst player may be the only
# body eligible for a slot the lineup needs, and dropping him costs more than he
# is worth. Three is enough to catch that without running the optimizer hundreds
# of times.
DROP_CHOICES = 3

# Kickers are excluded because ESPN cannot tell them apart, and the number is
# not close. Across 245 kicker player-weeks in 2025, projections have a standard
# deviation of 0.4 points against an outcome spread of 11.4, correlation between
# projection and result is +0.096, and picking the higher-projected of two
# kickers scored more 50.0% of the time across 39,568 pairs. A coin flip. Every
# kicker recommendation this could make would be noise wearing a number.
#
# Team defenses are a different story and are IN: correlation +0.258 and the
# higher projection wins 56.9% of pairs, which is the same signal as IDP, a
# family the tool already acts on.
#
# Both numbers come from our own stored ESPN outcomes rather than from PFF,
# which is what makes them checkable. PFF publishes no stat lines for either
# position, but that only ever blocked the wire BACKTEST, not this decision.
NEVER_STREAM = frozenset({"K"})

# Positions you replace rather than accumulate. Nobody carries two defenses, so
# the drop that goes with adding one is the defense already on the roster, not
# whichever fringe receiver happens to be cheapest. Without this the tool
# compared a defense's season projection against a wide receiver's and reported
# a 92 point season loss for a one point weekly gain, which is a true number
# answering a question nobody asked.
STREAMED = frozenset({"D/ST", "DST", "DEF"})

# Statuses ESPN accepts into the IR slot. This is no longer a guess: ESPN's own
# help says "players with either the Out (O) or Injured/Reserve (IR) status may
# be placed into the IR slot", and, explicitly, that SUSPENDED players are NOT
# eligible. The first version of this included SUSP on the reasoning that a
# suspension is an absence like any other. It is not, to ESPN.
#
# The rule is absent from the API -- rosterSettings carries lineupSlotCounts and
# position limits and nothing about IR -- so it has to be encoded here from the
# documentation rather than read at runtime.
IR_STATUS = frozenset({"O", "OUT", "IR"})

# Statuses that mean "no injury designation". A player in the IR slot who
# reaches one of these makes the roster INVALID: ESPN blocks lineup changes and
# acquisitions until he is moved out. Q and D deliberately are not here -- a
# player already in the slot may stay there when he improves to Questionable or
# Doubtful, and only a clean bill of health forces the move.
NO_DESIGNATION = frozenset({"OK", "ACTIVE", "", "NORMAL"})


def roster_room(client, lineup, claimed: dict[str, str] | None = None) -> int:
    """Open active roster spots, minus the ones already claimed.

    An empty bench spot makes an add free in exactly the way an IR stash does:
    nothing is dropped. Subtracting pending claims is the part that is easy to
    get wrong, and it is why this exists rather than being a subtraction inline.
    A claim that has not processed has ALREADY spoken for its spot, so counting
    it as open recommends a second add that will not fit.
    """
    try:
        counts = client.league.settings.position_slot_counts
    except AttributeError:
        return 0
    capacity = sum(int(v or 0) for k, v in counts.items() if k != "IR")
    active = sum(1 for p in lineup if p.slot != "IR")
    return max(0, capacity - active - len(claimed or {}))


def ir_room(client, lineup) -> int:
    """Empty IR slots. Zero when the league has none.

    Not from `roster_slots()`, which deliberately returns startable slots only
    and drops IR and BE. This reads the raw slot counts.
    """
    try:
        counts = client.league.settings.position_slot_counts
    except AttributeError:
        return 0
    total = int(counts.get("IR") or 0)
    used = sum(1 for p in lineup if p.slot == "IR")
    return max(0, total - used)


def pending_adds(client) -> dict[str, str]:
    """{espn player id: player name} for claims of mine that have not processed.

    ESPN shows a team only its OWN pending claims, which is the right privacy
    model and also the ceiling on what this can ever know: there is no way to
    see who else is bidding, so nothing here tries to estimate competition.

    What it can do is stop recommending a player already claimed, and stop
    counting a roster spot twice when a pending add has already spoken for it.
    """
    try:
        rows = client.league.transactions(types={"WAIVER", "FREEAGENT"})
    except Exception:
        return {}          # never let bookkeeping take the waiver view down
    mine = str(getattr(client.cfg, "team_id", ""))
    out: dict[str, str] = {}
    for tx in rows or []:
        if str(getattr(tx, "status", "")).upper() != "PENDING":
            continue
        if str(getattr(getattr(tx, "team", None), "team_id", "")) != mine:
            continue
        for item in getattr(tx, "items", []) or []:
            if str(getattr(item, "type", "")).upper() == "ADD":
                out[str(getattr(item, "playerId", ""))] = getattr(
                    item, "player", "?")
    return out


def pending_note(claims: dict[str, str]) -> str:
    if not claims:
        return ""
    who = ", ".join(sorted(claims.values()))
    return (f"Claim pending on {who}. That roster spot is already spoken for, "
            f"so everything below is what to do INSTEAD if the claim fails, "
            f"not as well. No way to see who else bid.")


def ir_invalid(lineup) -> list[WeeklyPlayer]:
    """Players in an IR slot who no longer carry any injury designation.

    Worth surfacing loudly and separately from everything else here, because it
    is not advice: ESPN marks the roster INVALID and blocks lineup changes and
    waiver claims until he is moved out. Every other recommendation this tool
    makes is unactionable while this is true.
    """
    return [p for p in lineup
            if p.slot == "IR" and (p.status or "").upper() in NO_DESIGNATION]


def stashable(lineup) -> list[WeeklyPlayer]:
    """Rostered players who could be moved to IR, worst injury first.

    Status only. `eligible_slots` is no help: ESPN lists IR as an eligible slot
    for every player on the roster, healthy ones included, so it says nothing
    about who can actually be stashed.
    """
    order = {"IR": 0, "OUT": 1, "O": 1}
    return sorted(
        (p for p in lineup
         if p.slot != "IR" and (p.status or "").upper() in IR_STATUS),
        key=lambda p: (order.get((p.status or "").upper(), 9), -p.projected))

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
    # The drop you would actually want, when his game has already kicked off and
    # the platform will not let you drop him until the weekly reset. None when
    # the best drop is also a legal one, which is the normal case.
    # Set when the roster spot comes from moving an injured player to IR rather
    # than from dropping anybody. Then nothing leaves the roster and the add is
    # free, which is a different decision from every other row here.
    free_via: str = ""        # why no drop is needed, empty when one is
    stash_name: str | None = None
    stash_pos: str | None = None
    stash_status: str | None = None
    blocked_name: str | None = None
    blocked_pos: str | None = None
    blocked_season_proj: float = 0.0
    drop_locked: bool = False   # nothing on the roster can be dropped right now

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
    def blocked_cost(self) -> float:
        """Extra season value surrendered because the ideal drop is locked.

        Always >= 0: the blocked player is by definition the cheapest to lose,
        so anyone else costs at least as much. This is the number that decides
        whether to make the move now or wait for the reset.
        """
        if self.blocked_name is None:
            return 0.0
        return max(0.0, self.drop_season_proj - self.blocked_season_proj)

    @property
    def is_stash(self) -> bool:
        return self.stash_name is not None

    @property
    def is_free(self) -> bool:
        """Nothing leaves the roster, so there is no trade to weigh."""
        return bool(self.free_via)

    @property
    def season_cost(self) -> float:
        """Season value given up. Positive means the add is also an upgrade for
        the rest of the year; negative means you are trading the season for a
        week.

        An IR stash gives up nothing at all: the injured player stays on the
        roster, so this is the add's own season value and there is no trade to
        weigh. Reporting his projection as a cost would invent a decision.
        """
        if self.is_free:
            return self.season_proj
        return self.season_proj - self.drop_season_proj

    @property
    def trades_down(self) -> bool:
        return self.season_cost < 0

    def rank(self, band: float = MIN_EDGE) -> tuple[float, float]:
        """Sort key: the week first, the season where the week cannot tell them
        apart.

        Ranking on the weekly gain alone was wrong and visibly so. In RCL week 2
        it put Jack Gibbens (+4.6 week, +81.8 season) above Christian Elliss
        (+4.0, +205.8), trading 124 points of season for six tenths of a Sunday.
        Both were free adds, so there was nothing being bought with it.

        The tempting fix is to convert the season number to points a week and
        add. That invents an exchange rate between a point now and a point in
        November, which is not measured and is not measurable from anything on
        hand. Worse, with seventeen weeks left the season term is three times
        the weekly one, so it stops being a tiebreak and becomes the whole
        ranking, burying the best win-now add completely.

        So: bucket the weekly gain by `band` and order by season value inside a
        bucket. `band` is MIN_EDGE, the measured floor below which a projection
        gap does not predict which player scores more. Two candidates inside it
        are genuinely indistinguishable for this week, so season value is the
        only thing left that separates them, and preferring it costs nothing
        that can be shown to exist.

        The season half is gross rather than marginal for an IR stash, since
        nothing is dropped, so it flatters a free add against a swap. Inside one
        list that is consistent, which is all sorting needs.
        """
        return (-(self.week_gain // band), -self.season_cost)

    def blocked_note(self) -> str:
        """Why the drop is not the one you would pick, in a sentence.

        No invented threshold for "is the difference big enough". It states the
        number and whether the move still gains season value overall, which is
        the pair of facts the decision actually turns on.
        """
        if self.is_free:
            return ""      # nothing is dropped, so nothing can be blocking it
        if self.drop_locked:
            return ("Nothing on this roster can be dropped right now: everyone "
                    "who could go has already played. This has to wait for the "
                    "weekly reset.")
        if not self.blocked_name:
            return ""
        tail = (f"Dropping {self.drop_name} instead gives up "
                f"{self.blocked_cost:.0f} more points of season value")
        if self.season_cost >= 0:
            tail += ", and the move still gains season value overall."
        else:
            tail += (f", and the move gives up {abs(self.season_cost):.0f} "
                     f"overall. Waiting for the reset would avoid that.")
        return (f"{self.blocked_name} ({self.blocked_pos}) is the cheaper drop, "
                f"but his game has started, so he cannot be dropped until the "
                f"weekly reset. {tail}")

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
        note = self.blocked_note()
        if note:
            line += f"\n    LOCKED: {note}"
        if self.is_free:
            return (line + f"\n    FREE: {self.free_via}, so nothing is "
                           f"dropped.\n    {self.name} is worth "
                           f"{self.season_proj:.0f} for the rest of the season "
                           f"on top.")
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
    if (getattr(player, "position", "") or "").upper() in NEVER_STREAM:
        return None
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

    def cheapest(pool):
        return sorted(pool, key=lambda p: values.get(p.player_id, p.projected))

    # A player whose game has kicked off cannot be dropped until the weekly
    # reset, so recommending him is advice you cannot take. This is what made the
    # tool say "drop Rashid Shaheed" on a Monday after he had already played.
    def locked_out(p) -> bool:
        """His game has kicked off, so the platform will not let him be dropped
        until the weekly reset."""
        return p.locked or p.played

    def free(p) -> bool:
        """Droppable at all.

        Two different reasons a player is not, and they must stay distinct. A
        locked player is TEMPORARILY undroppable and that is worth explaining,
        because it changes what you can do today. A player in the IR slot is
        deliberately being kept: he can technically be dropped, and ESPN
        depresses an injured player's season projection so he often looks like
        the cheapest drop on the roster, but stashing someone is what you do
        when you want to keep him.

        Collapsing the two made the tool say "his game has started" about a
        player whose game had not started, which is a confident wrong answer
        rather than a missing one.
        """
        return not locked_out(p) and p.slot != "IR"

    def blocked_by_lock(ideal, drop) -> bool:
        """Only a LOCK is worth explaining as a blocker.

        Not being chosen because he is stashed on IR is a deliberate choice, not
        an obstacle, so it gets no note for the same reason a streamed add does
        not get one.
        """
        return (ideal is not None and drop is not None
                and ideal.player_id != drop.player_id and locked_out(ideal))

    # An empty IR slot plus an injured player is a free roster spot: he stays on
    # the roster, so the add costs nothing at all. That outranks every drop,
    # because every drop costs something.
    claimed = pending_adds(client)

    # Two ways to add without dropping anybody, and an open spot is checked
    # first because it needs no move at all. Both are net of pending claims: a
    # claim that has not processed has already spoken for its spot.
    open_spots = roster_room(client, lineup, claimed)

    # An empty IR slot with nobody hurt enough to fill it is not a free spot,
    # and an injured player with no slot to put him in is just an injured
    # player. Both halves are required.
    candidates_to_stash = stashable(lineup) if ir_room(client, lineup) else []
    stash = candidates_to_stash[0] if candidates_to_stash else None

    if open_spots:
        free_via = "you have an open roster spot"
        stash = None            # no need to move anybody
    elif stash is not None:
        free_via = (f"moving {stash.name} ({stash.pos}, {stash.status}) to IR")
    else:
        free_via = "" 

    out_of_lineup = [p for p in lineup if p.player_id not in base_ids]
    # What you WOULD drop if the roster were unlocked, kept so the message can
    # name him and price the difference rather than quietly substituting.
    ideal = next(iter(cheapest(out_of_lineup or lineup)), None)

    ranked_drops = cheapest([p for p in out_of_lineup if free(p)])[:DROP_CHOICES]
    drop_locked = False
    if not ranked_drops:
        # Nothing on the bench is droppable. Fall back to the whole roster, and
        # if that is locked too, still answer but say the move cannot be made.
        ranked_drops = cheapest([p for p in lineup if free(p)])[:DROP_CHOICES]
    if not ranked_drops:
        drop_locked = True
        ranked_drops = cheapest(out_of_lineup or lineup)[:1]

    # The cheap filter. A candidate can only help if his calibrated projection
    # beats the weakest calibrated starter he is eligible to replace. Without
    # this the optimizer would run hundreds of times for nothing.
    # Every startable slot begins at zero, because an EMPTY slot is beaten by
    # anyone at all. Defaulting an unfilled slot to infinity silently hid every
    # candidate who could have filled it, which is the kind of miss that looks
    # like "the wire has nothing" rather than like a bug.
    weakest: dict[str, float] = {}
    for s in starters:
        weakest[s.slot] = min(weakest.get(s.slot, 1e9),
                              cal.adjust(s.pos, effective(s)))
    # An EMPTY startable slot is beaten by anyone at all, so it sits at zero
    # rather than at the infinity a missing key would give. Applied after the
    # loop, not as its default: seeding zeros first makes every min() zero and
    # turns the whole filter off, which is a quiet way to return noise.
    for slot in slot_list:
        weakest.setdefault(slot, 0.0)

    out: list[Candidate] = []
    for raw in client.league.free_agents(size=pool_size):
        candidate = _synthetic(raw, wk)
        if candidate is None:
            continue
        if candidate.player_id in claimed:
            continue       # already claimed; recommending him again is noise
        adjusted = cal.adjust(candidate.pos, candidate.projected)
        if not any(adjusted > weakest.get(slot, 1e9)
                   for slot in candidate.eligible_slots):
            continue

        # For a streamed position the drop is the incumbent, so the comparison
        # reads defense against defense: what this week and the rest of the
        # season look like with his, versus with the one you have.
        drops = ranked_drops
        if open_spots:
            # Nothing is dropped and nothing is moved, so the lineup the
            # candidate joins is simply the one that exists.
            drops = [None]
        elif stash is not None:
            # Nothing leaves the roster. The week's value is unchanged by the
            # move itself, because an OUT player already counts zero in the
            # lineup, so this only ever adds.
            drops = [stash]
        elif candidate.pos.upper() in STREAMED:
            incumbent = [p for p in lineup
                         if p.pos.upper() in STREAMED and free(p)]
            if incumbent:
                drops = cheapest(incumbent)[:1]

        best: tuple[float, WeeklyPlayer, str | None] | None = None
        for drop in drops:
            trial = [p for p in lineup
                     if drop is None or p.player_id != drop.player_id]
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
                [p for p in lineup
                 if drop is None or p.player_id != drop.player_id] + [candidate],
                slot_list, cal)[1]:
            continue      # he does not actually make the lineup
        out.append(Candidate(
            league=client.slug, week=wk, name=candidate.name, pos=candidate.pos,
            team=candidate.team, week_proj=candidate.projected,
            season_proj=float(getattr(raw, "projected_total_points", 0.0) or 0.0),
            free_via=free_via,
            drop_name=(drop.name if drop is not None else ""),
            drop_pos=(drop.pos if drop is not None else ""),
            drop_season_proj=(values.get(drop.player_id, 0.0)
                              if drop is not None else 0.0),
            # Only when the cheaper drop is LOCKED. A streamed add deliberately
            # drops the incumbent rather than the cheapest player, and saying
            # "his game has started" about a player who is simply not the right
            # drop would be a false explanation.
            stash_name=(stash.name if stash is not None else None),
            stash_pos=(stash.pos if stash is not None else None),
            stash_status=(stash.status if stash is not None else None),
            blocked_name=(None if free_via
                          else ideal.name if blocked_by_lock(ideal, drop) else None),
            blocked_pos=(None if free_via
                         else ideal.pos if blocked_by_lock(ideal, drop) else None),
            blocked_season_proj=(values.get(ideal.player_id, 0.0)
                                 if blocked_by_lock(ideal, drop) else 0.0),
            drop_locked=drop_locked,
            week_gain=gain, displaces=displaced,
            correction=cal.offset(candidate.pos),
            correction_n=(cal.biases.get(candidate.pos.upper()).n
                          if cal.biases.get(candidate.pos.upper()) else 0),
        ))

    out.sort(key=lambda c: c.rank())
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


def notes(client, lineup) -> str:
    """Everything that changes how the list below should be read, worst first.

    One string rather than several, because these are mutually exclusive in
    practice and the order is the point: a blocked roster outranks a pending
    claim, which outranks an unused IR slot. Leading with the wrong one hands
    him a move he cannot make yet.
    """
    parts = [n for n in (stash_note(client, lineup),
                         pending_note(pending_adds(client))) if n]
    return "\n\n".join(parts)


def stash_note(client, lineup) -> str:
    """The advice that stands on its own, independent of the wire.

    An empty IR slot next to an injured player is a roster spot you already own
    and are not using. That is worth saying even when nothing on the wire clears
    the bar, and it is the reason this is not simply part of the candidate loop.
    """
    stuck = ir_invalid(lineup)
    if stuck:
        who = ", ".join(p.name for p in stuck)
        return (f"⚠️ {who} is in an IR slot with no injury designation, so ESPN "
                f"has your roster marked INVALID. Lineup changes and waiver "
                f"claims are blocked until you move him out. Nothing else here "
                f"can be acted on first.")
    room = ir_room(client, lineup)
    if not room:
        return ""
    waiting = stashable(lineup)
    if not waiting:
        return ""
    who = ", ".join(f"{p.name} ({p.status})" for p in waiting[:room])
    return (f"{room} empty IR slot(s) and {len(waiting)} player(s) who could "
            f"fill them: {who}. Moving one there frees a roster spot without "
            f"dropping anybody.")


def render(candidates: list[Candidate], league_name: str, week: int,
           stash: str = "") -> str:
    if stash and not candidates:
        return (f"{league_name} — week {week}\n"
                f"{stash}\n\n"
                f"Nobody on the wire improves the lineup, but that roster spot "
                f"is free either way.")
    if not candidates:
        return (f"{league_name} — week {week}\n"
                f"Nobody on the wire improves the lineup. That is the normal "
                f"answer:\nthe pool is unrostered for a reason.")
    out = [f"{league_name} — week {week}", "WAIVER UPGRADES", ""]
    if stash:
        out.append("".join(f"  {line}\n" for line in stash.splitlines()))
    out.append(f"  {'':<3}{'ADD':<22}{'POS':<5}{'PROJ':>6}{'WEEK':>7}"
               f"{'SEASON':>8}  {'DROP / IR MOVE':<22}{'STARTS OVER'}")
    for i, c in enumerate(candidates, start=1):
        flag = "*" if c.correction_carries_it else " "
        out.append(f"  {str(i) + flag:<3}{c.name[:21]:<22}{c.pos[:4]:<5}"
                   f"{c.week_proj:>6.1f}{c.week_gain:>+7.1f}"
                   f"{c.season_cost:>+8.1f}  "
                   f"{(f'IR: {c.stash_name}' if c.is_stash else f'{c.drop_name} ({c.drop_pos})')[:21]:<22}"
                   f"{c.displaces or '--'}")
    out.append("")
    for i, c in enumerate(candidates, start=1):
        note = c.blocked_note()
        if note:
            out.append(f"  {i}  LOCKED: {note}")
        if c.correction_carries_it:
            out.append(f"  {i}* ranks here only because {c.pos} projections are "
                       f"corrected UP by {c.correction:.1f},\n     measured on "
                       f"{c.correction_n} player-weeks. On ESPN's raw number he "
                       f"does not clear the bar.")
        elif c.clears_despite_correction:
            out.append(f"  {i}  clears the bar even after {c.pos} projections are "
                       f"marked DOWN\n     {abs(c.correction):.1f} for being "
                       f"systematically over-projected.")
    out.append("\nOrdered by WEEK, with SEASON breaking ties the week cannot "
               "settle: two\ncandidates inside the measured edge are "
               "indistinguishable for Sunday,\nso season value is the only "
               "thing left that separates them."
               "\n\nWEEK is what the whole lineup is worth afterwards, not a head "
               "to head,\nso it already accounts for who shifts where. SEASON is "
               "what the drop\ncosts or gains for the rest of the year, kept "
               "separate because a week\nis not worth a season.\n\nEach row is an "
               "alternative, not a sequence: every one is measured\nagainst the "
               "lineup you have now, which is why they can name the same drop.")
    return "\n".join(out)
