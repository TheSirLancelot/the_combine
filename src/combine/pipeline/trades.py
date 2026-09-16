"""Deals where both rosters go up, priced by the same assignment the lineup
code uses, on the axis where a trade can actually create value.

**The weekly axis is zero sum, and that was measured here before anything was
built on it.** Across 999 priced one-for-ones in DMWD the two sides' weekly
lineup gains summed to a median of -0.6 points and a best of +2.1, and exactly
two pairs had both sides positive at all, by four tenths of a point. That is not
a shortage of imagination, it is conservation: the points a trade moves into my
starting lineup come out of theirs. A waiver claim is positive sum because the
pool is free; a trade is not.

**Where a trade does create value is unused season projection.** My fourth back
carries real points I will never score, because he cannot crack the lineup
ahead of three better men. Their fourth receiver is in the same position. Swap
them and both rosters convert dead weight into live weight. That is a roster
question, not a Sunday one, which is why `depth.py` exists alongside
`waivers.py` and why this ranks the way `depth.py` thinks rather than the way
`waivers.py` does.

So the ranking axis is `best_lineup` keyed on ESPN's FULL SEASON projection:
what a roster is worth if it always starts its best eligible men. The same
exact assignment, the same slot eligibility, a different key. Nothing is
forecast. ESPN publishes no weekly projection past the current week, and none
is invented here; the season number is ESPN's own and the cascade around it is
arithmetic.

Three things to read carefully.

**Season totals include games already played.** So the totals are not a
rest-of-season value and only the DIFFERENCE between two of them means
anything, which is the same rule the comparison view prints.

**The season assignment assumes you always start your best man.** No byes, no
injuries, no week a starter is out and the surplus player you traded away would
have covered. Giving up depth costs insurance that none of these numbers can
price, and that cost is real.

**Both sides gaining is not the same as them saying yes.** Nothing here models
what a manager wants. League-wide activity this season is 24 events in RCL and
7 in DMWD, none of them trades, which is not a sample to learn preferences
from. What IS observable is the shape of their roster, so each partner comes
with where they are thin and where they carry someone who never starts.

Read-only, like everything else: this hands you a deal to go and propose.

The search. Pricing every pair exactly is around 4,400 assignments a league,
which is minutes. Two properties of the assignment problem cut that to seconds,
and neither is an approximation:

  * `V` is monotone, so `V(M - P + X) <= V(M + X)`. What I gain by receiving X
    can never beat what X adds to my roster untouched. That bound does not
    depend on who I give up, so an X that fails it fails against everybody and
    never reaches a pair.
  * `V` is submodular, so `V(M - P + X) - V(M) >= add(X) - drop(P)`. That lower
    bound orders the survivors, and it is the WORSE of the two sides that does
    the ordering: rank on my own and the best player in the league sits at the
    top paired with every man I own, which is forty spellings of a deal nobody
    accepts and buries the one pair that works.

Everything that survives is then priced exactly, both sides, both axes. The
bounds choose what to look at. They never become the answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..platforms import WeeklyPlayer
from .depth import noise_band
from .lineup import as_candidate
from .optimize import best_lineup

SHORTLIST = 60         # pairs per partner priced exactly, after the bounds cut
LIMIT = 8


def _season(c: dict) -> float:
    return c["season"]


def _week(c: dict) -> float:
    return c["proj"]


@dataclass(frozen=True)
class Deal:
    """One player for one player, priced from both ends and on both axes."""

    give: WeeklyPlayer
    get: WeeklyPlayer
    partner: str
    my_season: float              # my whole roster, best-eligible, after minus before
    their_season: float           # theirs, same way
    my_week: float                # my starting lineup this week, after minus before
    their_week: float
    partner_thin: str = ""        # weakest position they still start
    partner_deep: str = ""        # where they carry someone who never starts
    odds_now: float = 0.0         # P(I win this week) as things stand
    odds_after: float = 0.0

    @property
    def odds_gain(self) -> float:
        return self.odds_after - self.odds_now

    def describe(self) -> str:
        return (f"give {self.give.name} ({self.give.pos}), "
                f"get {self.get.name} ({self.get.pos}) from {self.partner}")


def rosters(client, week: int) -> tuple[list[WeeklyPlayer],
                                        dict[str, list[WeeklyPlayer]]]:
    """(my players, {rival team: their players}).

    Box scores, because that is the only place a weekly projection lives, and
    they cover every team rather than only mine.
    """
    mine_ids = {p.player_id for p in client.matchup(week).my_lineup}
    mine: list[WeeklyPlayer] = []
    others: dict[str, list[WeeklyPlayer]] = {}
    for team, _versus, player in client.player_weeks(week):
        if player.player_id in mine_ids:
            mine.append(player)
        else:
            others.setdefault(team, []).append(player)
    return mine, others


def season_projections(client) -> dict[str, float]:
    """{espn id: full-season projection} for every rostered player in the
    league. `waivers.season_values` covers only my own team, and a trade needs
    the other man's number too."""
    out: dict[str, float] = {}
    for team in getattr(client.league, "teams", []) or []:
        for player in getattr(team, "roster", []) or []:
            out[str(getattr(player, "playerId", ""))] = float(
                getattr(player, "projected_total_points", 0.0) or 0.0)
    return out


def _cand(player: WeeklyPlayer, cal, season: dict[str, float],
          incoming: bool = False) -> dict:
    """A player in the shape the optimizer wants, carrying both values.

    An arriving player's slot and started flag belong to the roster he is
    leaving, so they are cleared. One whose game has kicked off is not playable
    this week, which is the honest weekly value of acquiring him today.
    """
    c = as_candidate(player, cal)
    c["season"] = season.get(player.player_id, 0.0)
    if incoming:
        c["started"] = False
        c["playable"] = not player.locked
    return c


def _anyone(_c: dict) -> bool:
    return True


def _value(cands: list[dict], slot_list: list[str], key) -> float:
    """The best legal assignment, summed.

    The season axis passes `_anyone` on purpose. `as_candidate` marks a player
    unplayable once his game has kicked off, which is right for a start/sit
    question and wrong for a roster one. Read on a Tuesday, when every game has
    been played, the default would drop every bench player out of the season
    valuation and make whole rosters look as if they had no surplus at all.
    """
    playable = _anyone if key is _season else None
    return sum(key(c) for c in best_lineup(cands, slot_list, key=key,
                                           playable=playable))


def _drop_costs(cands: list[dict], slot_list: list[str], base: float,
                key, chosen: set[str]) -> dict[str, float]:
    """What removing each player costs his own roster. Zero means he is carried
    rather than used, which is the man a trade can move for free.

    Only the men in the optimal assignment need solving. Removing somebody the
    optimum did not use cannot change the optimum, so his cost is exactly zero
    and there is nothing to compute. That is not an approximation, and it is
    most of the roster.
    """
    out = {}
    for i, c in enumerate(cands):
        out[c["espn_id"]] = (
            base - _value(cands[:i] + cands[i + 1:], slot_list, key)
            if c["espn_id"] in chosen else 0.0)
    return out


def _add_gains(cands: list[dict], slot_list: list[str], base: float,
               incoming: list[dict], key, floor: float | None = None
               ) -> dict[str, float]:
    """What adding each outside player would be worth, roster untouched. The
    monotone upper bound on any deal that brings him in.

    `floor` is the weakest man already in the assignment. With every slot full,
    an arrival who is worth less than that cannot displace anybody, so there is
    nothing to solve for him. Skipping those is what keeps this in seconds.
    """
    out = {}
    for c in incoming:
        if floor is not None and key(c) <= floor:
            out[c["espn_id"]] = 0.0
            continue
        out[c["espn_id"]] = _value([*cands, c], slot_list, key) - base
    return out


def _assignment(cands: list[dict], slot_list: list[str]) -> list[dict]:
    """The best-eligible season lineup, which several things need and none
    should solve twice."""
    return best_lineup(cands, slot_list, key=_season, playable=_anyone)


def _floor(chosen: list[dict], slot_list: list[str]) -> float | None:
    """The weakest man in the assignment, or None when a slot is still empty
    and anybody at all would be an improvement."""
    if len(chosen) < len(slot_list):
        return None
    return min((_season(c) for c in chosen), default=None)


def _shape(cands: list[dict], slot_list: list[str],
           drop_costs: dict[str, float]) -> tuple[str, str]:
    """(thinnest position, deepest position) for one roster.

    Observed, not inferred, and this is the whole of the opponent model. Thin is
    the weakest man who still makes their best-eligible season lineup; deep is
    where they carry somebody whose absence would cost them nothing. What a
    manager would want is visible in what he is forced to start.
    """
    chosen = _assignment(cands, slot_list)
    thin = min(chosen, key=_season)["pos"] if chosen else ""
    spare: dict[str, int] = {}
    for c in cands:
        if drop_costs.get(c["espn_id"], 1.0) <= 0.01:
            spare[c["pos"]] = spare.get(c["pos"], 0) + 1
    deep = max(spare, key=lambda pos: spare[pos]) if spare else ""
    return thin, deep


def find(client, week: int | None = None, cal=None, limit: int = LIMIT,
         shortlist: int = SHORTLIST, band: float | None = None,
         dist=None) -> list[Deal]:
    """One-for-ones where both rosters gain more than the noise band over a
    season, best first by my own gain."""
    wk = int(week or client.week)
    bar = noise_band() if band is None else band
    slots = client.roster_slots()
    slot_list = [slot for slot, count in slots.items() for _ in range(count)]
    mine, others = rosters(client, wk)
    if not mine or not others:
        return []
    season = season_projections(client)

    # A player ESPN has no season number for cannot be priced on this axis, and
    # a zero would read as "worth nothing" rather than "not known". He is left
    # out of the search entirely rather than traded away for free.
    mine = [p for p in mine if season.get(p.player_id, 0.0) > 0]
    my_cands = [_cand(p, cal, season) for p in mine]
    my_chosen = _assignment(my_cands, slot_list)
    my_base = sum(_season(c) for c in my_chosen)
    my_floor = _floor(my_chosen, slot_list)
    my_drop = _drop_costs(my_cands, slot_list, my_base, _season,
                          {c["espn_id"] for c in my_chosen})
    by_id = {p.player_id: p for p in mine}

    deals: list[Deal] = []
    for team, roster in others.items():
        players = [p for p in roster if season.get(p.player_id, 0.0) > 0]
        if not players:
            continue
        their_cands = [_cand(p, cal, season) for p in players]
        their_chosen = _assignment(their_cands, slot_list)
        their_base = sum(_season(c) for c in their_chosen)
        their_drop = _drop_costs(their_cands, slot_list, their_base, _season,
                                 {c["espn_id"] for c in their_chosen})
        thin, deep = _shape(their_cands, slot_list, their_drop)
        theirs_by_id = {p.player_id: p for p in players}

        my_add = _add_gains(my_cands, slot_list, my_base,
                            [_cand(p, cal, season, incoming=True)
                             for p in players], _season, my_floor)
        their_add = _add_gains(their_cands, slot_list, their_base,
                               [_cand(p, cal, season, incoming=True)
                                for p in mine], _season,
                               _floor(their_chosen, slot_list))
        wanted = [pid for pid, gain in my_add.items() if gain > bar]
        offered = [pid for pid, gain in their_add.items() if gain > bar]
        if not wanted or not offered:
            continue

        pairs = sorted(
            ((min(my_add[got] - my_drop[gave],
                  their_add[gave] - their_drop[got]), gave, got)
             for gave in offered for got in wanted),
            key=lambda row: -row[0])[:shortlist]

        for _bound, gave, got in pairs:
            give, get = by_id[gave], theirs_by_id[got]
            my_season = _value(
                [c for c in my_cands if c["espn_id"] != gave]
                + [_cand(get, cal, season, incoming=True)],
                slot_list, _season) - my_base
            if my_season <= bar:
                continue
            their_season = _value(
                [c for c in their_cands if c["espn_id"] != got]
                + [_cand(give, cal, season, incoming=True)],
                slot_list, _season) - their_base
            if their_season <= bar:
                continue
            deals.append(Deal(
                give=give, get=get, partner=team,
                my_season=my_season, their_season=their_season,
                my_week=0.0, their_week=0.0,
                partner_thin=thin, partner_deep=deep))

    deals.sort(key=lambda d: -d.my_season)
    best = _weekly(mine, others, slot_list, cal, season, _distinct(deals, limit))
    return _with_odds(client, wk, mine, slot_list, cal, season, best, dist) \
        if dist else best


def _distinct(deals: list[Deal], limit: int) -> list[Deal]:
    """Best first, never the same player twice on either side.

    Without this the table is eight spellings of one trade: the one rival worth
    raiding, paired with each of the eight men I could send back. The second
    row tells you nothing the first did not, and the deal on some other roster
    that would have been seventh never appears.
    """
    out: list[Deal] = []
    used: set[str] = set()
    for deal in deals:
        if deal.give.player_id in used or deal.get.player_id in used:
            continue
        used.add(deal.give.player_id)
        used.add(deal.get.player_id)
        out.append(deal)
        if len(out) >= limit:
            break
    return out


def _weekly(mine, others, slot_list, cal, season, deals: list[Deal]) -> list[Deal]:
    """This Sunday's cost or gain, for the deals actually shown.

    Late on purpose. It is not the ranking axis, it is four assignments a pair,
    and pricing it for every pair the bounds let through was most of the runtime
    for a column that decides nothing.
    """
    from dataclasses import replace

    if not deals:
        return deals
    my_cands = [_cand(p, cal, season) for p in mine]
    my_base = _value(my_cands, slot_list, _week)
    their_cands = {team: [_cand(p, cal, season) for p in roster]
                   for team, roster in others.items()}
    their_base = {team: _value(cands, slot_list, _week)
                  for team, cands in their_cands.items()}

    out = []
    for d in deals:
        mine_after = ([c for c in my_cands
                       if c["espn_id"] != d.give.player_id]
                      + [_cand(d.get, cal, season, incoming=True)])
        theirs_after = ([c for c in their_cands[d.partner]
                         if c["espn_id"] != d.get.player_id]
                        + [_cand(d.give, cal, season, incoming=True)])
        out.append(replace(
            d,
            my_week=_value(mine_after, slot_list, _week) - my_base,
            their_week=(_value(theirs_after, slot_list, _week)
                        - their_base[d.partner])))
    return out


def _with_odds(client, week: int, mine: list[WeeklyPlayer],
               slot_list: list[str], cal, season, deals: list[Deal],
               dist) -> list[Deal]:
    """P(I win this week) before and after, for the deals actually shown.

    My side is the lineup I would field having been told to optimise it; their
    side is the lineup their manager has actually set, which is observed rather
    than assumed. Only the shortlist gets this: a simulation per pair considered
    would cost more than the search.
    """
    from dataclasses import replace

    from .odds import as_lineup, win_probability

    # Their STARTERS. `their_lineup` is the whole roster, bench included, and
    # simulating all twenty of them against my twelve put my win odds at 13%
    # in a matchup ESPN had me favoured in.
    opponent = as_lineup([p for p in client.matchup(week).their_lineup
                          if p.starting])

    def field(players: list[WeeklyPlayer]) -> list[tuple[str, float]]:
        chosen = best_lineup([_cand(p, cal, season) for p in players],
                             slot_list, key=_week)
        return as_lineup([c["player"] for c in chosen])

    now = win_probability(field(mine), opponent, dist)
    out = []
    for deal in deals:
        after = [p for p in mine if p.player_id != deal.give.player_id]
        after.append(deal.get)
        out.append(replace(deal, odds_now=now,
                           odds_after=win_probability(field(after), opponent,
                                                      dist)))
    return out


def for_league(league: str, limit: int = LIMIT) -> list[Deal]:
    """The whole question for one league, so three callers cannot drift."""
    from .. import config, db
    from ..platforms import client_for
    from .calibration import load as load_cal
    from .distribution import load as load_dist

    dist = None
    try:
        with db.connect(readonly=True) as conn:
            candidate = load_dist(conn, config.SEASON - 1)
        dist = None if candidate.empty else candidate
    except Exception:
        dist = None      # no history yet: the deals still price, the odds do not
    return find(client_for(league), None, load_cal(league), limit=limit,
                dist=dist)


FOOTER = (
    "\nSEASON is each whole roster started best-eligible, before against after, "
    "so a\nfourth back who never cracks a lineup is correctly worth nothing to "
    "the side\nholding him and can be worth real points to the side that would "
    "start him.\nThat is the only axis where a trade creates value for both "
    "teams.\n\nWEEK is this Sunday's starting lineup, and it is close to zero "
    "sum: measured\nover 999 priced pairs the two sides' weekly gains summed to "
    "a median of -0.6.\nExpect one side to be negative here. It is shown so a "
    "deal that quietly costs\nyou the week is visible, not because it is the "
    "thing to optimise.\n\nSeason totals include games already played, so read "
    "the gain and not the\ntotals. The assignment assumes you always start your "
    "best man: no byes, no\ninjuries, and no price on the depth you give up, "
    "which is a real cost none of\nthese numbers carry.\n\nBoth sides gaining "
    "is not the same as them saying yes. Nothing here models\nwhat a manager "
    "wants, and nobody in either league has made a trade this season.")


def render(deals: list[Deal], league_name: str) -> str:
    if not deals:
        return (f"{league_name} — trades\n"
                f"No one-for-one improves both rosters by more than the noise "
                f"band.\nThat is a normal answer: it needs two rosters whose "
                f"surpluses fit each\nother's holes, and most pairs of rosters "
                f"do not.")
    out = [f"{league_name} — trades", ""]
    out.append(f"  {'GIVE':<20}{'GET':<20}{'FROM':<16}"
               f"{'ME/SZN':>8}{'THEM/SZN':>10}{'ME/WK':>7}{'WIN%':>9}")
    for d in deals:
        odds = (f"{d.odds_now * 100:.0f}→{d.odds_after * 100:.0f}"
                if d.odds_after else "--")
        out.append(f"  {d.give.name[:19]:<20}{d.get.name[:19]:<20}"
                   f"{d.partner[:15]:<16}{d.my_season:>+8.0f}"
                   f"{d.their_season:>+10.0f}{d.my_week:>+7.1f}{odds:>9}")
    shapes: dict[str, str] = {}
    for d in deals:
        note = []
        if d.partner_thin:
            note.append(f"thinnest at {d.partner_thin}")
        if d.partner_deep:
            note.append(f"carrying spare {d.partner_deep}s")
        if note:
            shapes.setdefault(d.partner, ", ".join(note))
    if shapes:
        out.append("")
        for team, note in shapes.items():
            out.append(f"  {team}: {note}")
    out.append(FOOTER)
    return "\n".join(out)


# --- grading an offer somebody actually sent -------------------------------
#
# The finder asks "what deal exists". This asks "is the one in my inbox any
# good", which is the same two assignments without the search, and it takes any
# number of players a side. The extra thing it has to handle is roster size: a
# two for one frees a spot, a one for two forces a cut, and the cut is a real
# player whose value has to come out of the total rather than being waved at.


@dataclass(frozen=True)
class Move:
    """One change the deal makes to a lineup."""

    name: str
    pos: str
    value: float
    joining: bool
    dp: int = 0            # season points are whole, a week's are not

    def describe(self) -> str:
        arrow = "in " if self.joining else "out"
        return f"{arrow} {self.name} ({self.pos}) {self.value:.{self.dp}f}"


@dataclass(frozen=True)
class Verdict:
    """What an offer is worth, from both ends."""

    give: list[WeeklyPlayer]
    get: list[WeeklyPlayer]
    partner: str
    my_season: float
    their_season: float
    my_week: float
    their_week: float
    season_moves: list[Move]           # what changes in my season assignment
    week_moves: list[Move]             # what changes in my lineup this Sunday
    spots: int = 0                     # roster spots freed (+) or needed (-)
    room: int = 0                      # spots I have open right now
    my_cuts: list[str] = ()            # who I would have to cut to fit them in
    their_cuts: list[str] = ()
    odds_now: float = 0.0
    odds_after: float = 0.0

    @property
    def good(self) -> bool:
        """Whether the numbers favour me. Not whether to accept: depth,
        injuries and what happens to this roster in November are not in it."""
        return self.my_season > 0

    @property
    def mutual(self) -> bool:
        return self.my_season > 0 and self.their_season > 0

    @property
    def odds_gain(self) -> float:
        return self.odds_after - self.odds_now


def _pick(index: dict[str, WeeklyPlayer], want: str) -> tuple:
    """(player, complaint). Exact name beats a substring, so asking for a man
    whose name is contained in somebody else's is not ambiguous."""
    key = want.strip().lower()
    if key in index:
        return index[key], ""
    hits = [name for name in index if key and key in name]
    if not hits:
        return None, f"`{want}` is not on either roster"
    if len(hits) > 1:
        shown = ", ".join(sorted(index[h].name for h in hits)[:6])
        return None, f"`{want}` matches {len(hits)}: {shown}"
    return index[hits[0]], ""


def _cheapest(cands: list[dict], drop_costs: dict[str, float], how_many: int,
              spare: set[str]) -> list[dict]:
    """The men a roster would cut to make room, least painful first.

    Cheapest by what losing them costs the assignment, then by season
    projection, so two players who both cost nothing are separated by the
    smaller loss of depth rather than by dictionary order.

    A player on IR is never offered. He is not occupying an active spot, so
    cutting him frees nothing, and he is on IR because somebody decided to keep
    him. The first version offered Myles Garrett, stashed at 118 points, as the
    cheapest man on the roster, which was true and useless.
    """
    if how_many <= 0:
        return []
    pool = [c for c in cands
            if c["espn_id"] not in spare
            and getattr(c.get("player"), "slot", "") != "IR"]
    pool.sort(key=lambda c: (drop_costs.get(c["espn_id"], 0.0), _season(c)))
    return pool[:how_many]


def _moves(before: list[dict], after: list[dict], key, dp: int = 0) -> list[Move]:
    was = {c["espn_id"]: c for c in before}
    now = {c["espn_id"]: c for c in after}
    out = [Move(c["name"], c["pos"], key(c), True, dp)
           for pid, c in now.items() if pid not in was]
    out += [Move(c["name"], c["pos"], key(c), False, dp)
            for pid, c in was.items() if pid not in now]
    return sorted(out, key=lambda m: (not m.joining, -m.value))


def grade(client, give: list[str], get: list[str], week: int | None = None,
          cal=None, dist=None) -> tuple[Verdict | None, str]:
    """Price an offer that already exists. (verdict, complaint).

    Any number a side. Everything the finder says about the two axes applies
    unchanged: the season number is where a trade can create value and the
    weekly one is close to zero sum.
    """
    from .waivers import roster_room

    wk = int(week or client.week)
    slots = client.roster_slots()
    slot_list = [slot for slot, count in slots.items() for _ in range(count)]
    mine, others = rosters(client, wk)
    season = season_projections(client)

    mine_index = {p.name.lower(): p for p in mine}
    theirs_index = {p.name.lower(): (team, p)
                    for team, roster in others.items() for p in roster}

    giving, problems = [], []
    for want in give:
        player, err = _pick(mine_index, want)
        if err:
            problems.append(err.replace("either roster", "your roster"))
        else:
            giving.append(player)

    getting, partners = [], set()
    for want in get:
        owned = {name: player for name, (_team, player) in theirs_index.items()}
        player, err = _pick(owned, want)
        if err:
            problems.append(err.replace("either roster",
                                        "any other roster in the league"))
            continue
        getting.append(player)
        partners.add(theirs_index[player.name.lower()][0])

    if problems:
        return None, " ".join(problems)
    if not giving or not getting:
        return None, "a trade needs at least one player each way"
    if len(partners) > 1:
        return None, ("that is a three way trade: "
                      f"{', '.join(sorted(partners))}. One partner at a time.")
    partner = next(iter(partners))
    theirs = others[partner]

    # Both sides, on both axes, with the forced cuts actually removed rather
    # than assumed free.
    my_cands = [_cand(p, cal, season) for p in mine]
    their_cands = [_cand(p, cal, season) for p in theirs]
    my_chosen = _assignment(my_cands, slot_list)

    gave = {p.player_id for p in giving}
    got = {p.player_id for p in getting}
    spots = len(giving) - len(getting)
    room = roster_room(client, mine)

    def traded(cands, out_ids, incoming):
        return ([c for c in cands if c["espn_id"] not in out_ids]
                + [_cand(p, cal, season, incoming=True) for p in incoming])

    def make_room(cands, how_many, keep):
        """Who gets cut, chosen on the roster AS IT WOULD BE after the trade.

        Choosing beforehand picks the wrong man. Take Lamar Jackson in and
        Brock Purdy becomes the surplus quarterback and the obvious cut; ask
        before the trade lands and Purdy is still a starter, so the cut falls on
        somebody who was doing useful work.
        """
        if how_many <= 0:
            return [], cands
        chosen = _assignment(cands, slot_list)
        costs = _drop_costs(cands, slot_list,
                            sum(_season(c) for c in chosen), _season,
                            {c["espn_id"] for c in chosen})
        cuts = _cheapest(cands, costs, how_many, keep)
        dropped = {c["espn_id"] for c in cuts}
        return cuts, [c for c in cands if c["espn_id"] not in dropped]

    my_cuts, mine_after = make_room(traded(my_cands, gave, getting),
                                    -spots - room, got)
    their_cuts, theirs_after = make_room(traded(their_cands, got, giving),
                                         spots, gave)

    return Verdict(
        give=giving, get=getting, partner=partner,
        my_season=(_value(mine_after, slot_list, _season)
                   - _value(my_cands, slot_list, _season)),
        their_season=(_value(theirs_after, slot_list, _season)
                      - _value(their_cands, slot_list, _season)),
        my_week=(_value(mine_after, slot_list, _week)
                 - _value(my_cands, slot_list, _week)),
        their_week=(_value(theirs_after, slot_list, _week)
                    - _value(their_cands, slot_list, _week)),
        season_moves=_moves(my_chosen, _assignment(mine_after, slot_list),
                            _season),
        week_moves=_moves(
            best_lineup(my_cands, slot_list, key=_week),
            best_lineup(mine_after, slot_list, key=_week), _week, dp=1),
        spots=spots, room=room,
        my_cuts=[c["name"] for c in my_cuts],
        their_cuts=[c["name"] for c in their_cuts],
        **_grade_odds(client, wk, my_cands, mine_after, slot_list, dist),
    ), ""


def _grade_odds(client, week: int, before: list[dict], after: list[dict],
                slot_list: list[str], dist) -> dict[str, float]:
    """P(I win this week) either side of the deal, when there is history to
    resample. An empty dict when there is not, so the render can say nothing
    rather than print a made-up fifty."""
    if dist is None:
        return {}
    from .odds import as_lineup, win_probability

    opponent = as_lineup([p for p in client.matchup(week).their_lineup
                          if p.starting])

    def field(cands):
        chosen = best_lineup(cands, slot_list, key=_week)
        return as_lineup([c["player"] for c in chosen])

    return {"odds_now": win_probability(field(before), opponent, dist),
            "odds_after": win_probability(field(after), opponent, dist)}


VERDICT_FOOTER = (
    "\nSEASON is your whole roster started best-eligible, before against after, "
    "so a\nplayer who never cracks the lineup is correctly worth nothing and a "
    "man who\ndisplaces a starter is worth the difference, not his own "
    "projection. It counts\ngames already played, so read the change and not "
    "the totals.\n\nWEEK is this Sunday only, and it is close to zero sum "
    "across the two sides.\nIt is here so a deal that quietly costs you Sunday "
    "is visible.\n\nThis grades the offer. It does not tell you to accept it. "
    "What it cannot see:\nthe depth you give up, an injury in November, whether "
    "ESPN's season numbers are\nright about either man, and whether this "
    "manager comes back with something\nbetter if you say no.")


def render_verdict(v: Verdict, league_name: str) -> str:
    give = ", ".join(f"{p.name} ({p.pos})" for p in v.give)
    get = ", ".join(f"{p.name} ({p.pos})" for p in v.get)
    out = [f"{league_name} — offer from {v.partner}", "",
           f"  you give   {give}",
           f"  you get    {get}", ""]

    verdict = ("the numbers favour you" if v.my_season > 0
               else "the numbers are against you" if v.my_season < 0
               else "the numbers are a wash")
    out.append(f"  {verdict.upper()}")
    out.append(f"  {'your roster, season':<24}{v.my_season:>+8.0f}")
    out.append(f"  {'their roster, season':<24}{v.their_season:>+8.0f}")
    out.append(f"  {'your lineup this week':<24}{v.my_week:>+8.1f}")
    if v.odds_after:
        out.append(f"  {'your odds this week':<24}"
                   f"{v.odds_now * 100:>7.0f}% → {v.odds_after * 100:.0f}%")
    out.append("")

    if v.season_moves:
        out.append("WHAT CHANGES in your season lineup")
        for move in v.season_moves:
            out.append(f"  {move.describe()}")
        out.append("")
    else:
        out.append("Nothing changes in your season lineup: the men coming in "
                   "do not crack it\nand the men going out were not in it.\n")

    if v.week_moves:
        out.append("WHAT CHANGES this Sunday")
        for move in v.week_moves:
            out.append(f"  {move.describe()}")
        out.append("")

    if v.spots < 0:
        short = -v.spots - v.room
        if short > 0:
            out.append(f"ROSTER: you take on {-v.spots} more than you send "
                       f"with {v.room} spot(s) open, so {short} would\nhave to "
                       f"go, cheapest first: {', '.join(v.my_cuts)}\nWhat "
                       f"cutting them costs is already inside the season "
                       f"number above.")
        else:
            out.append(f"ROSTER: you take on {-v.spots} more than you send and "
                       f"have {v.room} spot(s) open, so\nnobody has to be cut.")
        out.append("")
    elif v.spots > 0:
        out.append(f"ROSTER: you free {v.spots} spot(s). Worth close to nothing "
                   f"in points -- the best\nfree agent does not crack this "
                   f"lineup -- and worth something as insurance,\nwhich none of "
                   f"these numbers price.")
        out.append("")

    out.append(VERDICT_FOOTER.lstrip("\n"))
    return "\n".join(out)
