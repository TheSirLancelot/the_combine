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
from textwrap import fill as fill_text

from ..platforms import WeeklyPlayer
from .depth import noise_band
from .lineup import as_candidate
from .optimize import best_lineup

SHORTLIST = 60   # pairs per partner priced exactly, after the bounds cut
LEAST = 3        # target rungs to show even when the tests bless fewer
LIMIT = 8
MINE_TEAM = "yours"    # my own squad's key in the league-wide pool


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
    bar: float = 0.0              # the noise band this was judged against
    their_moves: tuple = ()       # what changes in THEIR season lineup
    odds_now: float = 0.0         # P(I win this week) as things stand
    odds_after: float = 0.0

    @property
    def odds_gain(self) -> float:
        return self.odds_after - self.odds_now

    @property
    def ask(self) -> str:
        """How hard a sell this is, in bands of noise.

        `solid`, the numbers say he gains. `stretch`, his side is inside the
        band where a gain and a loss look the same. `longshot`, the numbers do
        say he loses, and only a manager who likes trading is going to listen.
        Three words rather than a probability, because nothing here knows how
        any of them will answer.
        """
        if self.their_season > self.bar:
            return "solid"
        return "stretch" if self.their_season > -self.bar else "longshot"

    @property
    def stretch(self) -> bool:
        """Whether the partner's side is inside the band rather than above it.

        The band is the width below which these numbers cannot tell two
        outcomes apart. So a partner at +8 and a partner at -8 are the same
        answer -- "no idea" -- and requiring him to clear +17 before a deal is
        worth mentioning throws away the ones worth asking about. It hid the
        best deal in RCL: Davis for Malik Nabers at +36 to me and +8 to him.

        It is a real distinction and it is labelled rather than blurred. Above
        the band the numbers say he gains. Inside it they say nothing, and
        whether he says yes comes down to what he thinks of his own roster.
        """
        return self.their_season <= self.bar

    def describe(self) -> str:
        return (f"give {self.give.name} ({self.give.pos}), "
                f"get {self.get.name} ({self.get.pos}) from {self.partner}")

    def why_they_might(self) -> str:
        """What the deal does to THEIR lineup, which is the only honest answer
        to "how does losing their starter gain them 56 points".

        It usually does not cost them what it looks like. Super Lamario 64
        carries Lamar Jackson at 343 and Jayden Daniels at 339 in a one
        quarterback league, so sending Jackson costs him four points, not 343,
        and the linebacker coming back replaces a 117 point starter.
        """
        if not self.their_moves:
            return ""
        ins = ", ".join(f"{m.name} {m.value:.0f}"
                        for m in self.their_moves if m.joining)
        outs = ", ".join(f"{m.name} {m.value:.0f}"
                         for m in self.their_moves if not m.joining)
        return f"they start {ins or 'nobody new'}; out comes {outs or 'nobody'}"


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


def _profile(cands: list[dict], slot_list: list[str],
             chosen: list[dict]) -> tuple[dict[str, float], dict[str, float]]:
    """({position: their weakest starter}, {position: their best non-starter}).

    Both keyed on the best-eligible season assignment, so "starter" means the
    man the roster would actually field over a year rather than whoever is in
    the slot this Tuesday.
    """
    picked = {c["espn_id"] for c in chosen}
    worst: dict[str, float] = {}
    best: dict[str, float] = {}
    for c in chosen:
        pos = c["pos"]
        worst[pos] = min(worst.get(pos, _season(c)), _season(c))
    for c in cands:
        if c["espn_id"] in picked:
            continue
        pos = c["pos"]
        best[pos] = max(best.get(pos, _season(c)), _season(c))
    return worst, best


def league_shape(profiles: dict[str, tuple[dict, dict]], band: float
                 ) -> dict[str, tuple[str, str]]:
    """{team: (thinnest position, deepest position)}, measured AGAINST THE
    LEAGUE rather than against the rest of their own roster.

    The first version compared a roster only with itself, and it produced a
    contradiction William spotted on screen: "thinnest at WR, carrying spare
    WRs". Both halves were really measuring how many receivers a roster holds.
    Thin was the lowest-scoring man in the lineup, which lands on WR because
    receivers fill the most starting slots and score less than quarterbacks and
    backs. Deep was a count of players who could be dropped for free, which
    lands on WR because everybody benches receivers. Two different questions,
    one answer, and that answer was "rosters have a lot of receivers".

    Thin now means their weakest starter at a position is worse than the
    league's typical weakest starter at that position. Deep means their best
    BENCHED player at a position is better than the league's typical benched
    player there. A position can no longer be both: a benched man who is better
    than the league's starters would be starting.

    Nothing is named unless the gap clears the noise band, because a position
    two points off the median is not a hole.
    """
    from statistics import median

    def gaps(side: int, sign: float) -> dict[str, dict[str, float]]:
        pool: dict[str, list[float]] = {}
        for prof in profiles.values():
            for pos, value in prof[side].items():
                pool.setdefault(pos, []).append(value)
        mids = {pos: median(values) for pos, values in pool.items()}
        return {team: {pos: sign * (value - mids[pos])
                       for pos, value in prof[side].items()}
                for team, prof in profiles.items()}

    short = gaps(0, -1.0)      # how far BELOW the league their starters are
    spare = gaps(1, +1.0)      # how far ABOVE the league their bench is
    out = {}
    for team in profiles:
        thin = max(short[team], key=short[team].get, default=None)
        deep = max(spare[team], key=spare[team].get, default=None)
        out[team] = (
            thin if thin and short[team][thin] > band else "",
            deep if deep and spare[team][deep] > band else "")
    return out


def find(client, week: int | None = None, cal=None, limit: int = LIMIT,
         shortlist: int = SHORTLIST, band: float | None = None,
         dist=None, progress=None, only: str | None = None,
         reach: float = 1.0) -> list[Deal]:
    """One-for-ones where both rosters gain more than the noise band over a
    season, best first by my own gain.

    `only` narrows the search to one partner, which is the question you ask
    when you know somebody trades. `reach` is how far below zero his side is
    allowed to land, in noise bands: 1.0 refuses anything the numbers call a
    loss for him, higher asks for things he has a reason to turn down. Nothing
    here knows who is willing, so that judgement stays with the caller.

    `progress(done, total, team)` is called for a caller that wants to show how
    far along this is: once with `team=None` before anything is read, once per
    partner as it is priced, and once with `team=""` at the end. It runs for the
    best part of a minute in a twelve team IDP league, and a spinner that says
    nothing for fifty seconds is indistinguishable from a hang.
    """
    if progress:
        progress(0, 1, None)
    wk = int(week or client.week)
    bar = noise_band() if band is None else band
    slots = client.roster_slots()
    slot_list = [slot for slot, count in slots.items() for _ in range(count)]
    mine, others = rosters(client, wk)
    if only:
        others = {team: roster for team, roster in others.items()
                  if team.lower() == only.lower()
                  or only.lower() in team.lower()}
        if not others:
            return []
    if not mine or not others:
        return []
    season = season_projections(client)

    # A player ESPN has no season number for cannot be priced on this axis, and
    # a zero would read as "worth nothing" rather than "not known". He is left
    # out of the search entirely rather than traded away for free.
    mine = [p for p in mine if season.get(p.player_id, 0.0) > 0]
    my_cands = [_cand(p, cal, season) for p in mine]
    my_chosen = _assignment(my_cands, slot_list)

    # Every roster, solved once, so the shape of one can be judged against the
    # league instead of against itself. My own team is in the pool: it is part
    # of the league and leaving it out would shift every median.
    squads = {MINE_TEAM: (my_cands, my_chosen)}
    for team, roster in others.items():
        cands = [_cand(p, cal, season) for p in roster
                 if season.get(p.player_id, 0.0) > 0]
        if cands:
            squads[team] = (cands, _assignment(cands, slot_list))
    shapes = league_shape(
        {team: _profile(cands, slot_list, chosen)
         for team, (cands, chosen) in squads.items()}, bar)
    my_base = sum(_season(c) for c in my_chosen)
    my_floor = _floor(my_chosen, slot_list)
    my_drop = _drop_costs(my_cands, slot_list, my_base, _season,
                          {c["espn_id"] for c in my_chosen})
    by_id = {p.player_id: p for p in mine}

    deals: list[Deal] = []
    total = len(others)
    for done, (team, roster) in enumerate(others.items()):
        if progress:
            progress(done, total, team)
        players = [p for p in roster if season.get(p.player_id, 0.0) > 0]
        if not players:
            continue
        their_cands, their_chosen = squads[team]
        their_base = sum(_season(c) for c in their_chosen)
        their_drop = _drop_costs(their_cands, slot_list, their_base, _season,
                                 {c["espn_id"] for c in their_chosen})
        thin, deep = shapes[team]
        theirs_by_id = {p.player_id: p for p in players}

        my_add = _add_gains(my_cands, slot_list, my_base,
                            [_cand(p, cal, season, incoming=True)
                             for p in players], _season, my_floor)
        their_add = _add_gains(their_cands, slot_list, their_base,
                               [_cand(p, cal, season, incoming=True)
                                for p in mine], _season,
                               _floor(their_chosen, slot_list))
        # My side still prunes: `my_season > bar` needs `my_add > bar`, which is
        # the monotone bound and sound.
        wanted = [pid for pid, gain in my_add.items() if gain > bar]
        if not wanted:
            continue

        # His side no longer does, and cannot. The test is now that he is not
        # clearly worse off, and the only sound bound on his gain is `their_add
        # >= 0`, which every player satisfies. A filter on `their_add > 0`
        # looks reasonable and is wrong: a man who cannot crack their lineup
        # today may well start once the player they send me is gone, which is
        # the same submodularity that made the lower bound a lower bound. So
        # every one of my players stays a candidate, and the shortlist cap is
        # what keeps this bounded.
        offered = list(their_add)

        # Ranked by how much slack each side has against ITS OWN bar, which is
        # +bar for me and -bar for him. Ranking on the raw pair of bounds sent
        # the shortlist after deals he loves and skipped the ones he merely
        # does not mind, which are most of what this change is for.
        pairs = sorted(
            ((min(my_add[got] - my_drop[gave] - bar,
                  their_add[gave] - their_drop[got] + bar * reach), gave, got)
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
            if their_season <= -bar * reach:
                continue
            deals.append(Deal(
                give=give, get=get, partner=team,
                my_season=my_season, their_season=their_season,
                my_week=0.0, their_week=0.0,
                partner_thin=thin, partner_deep=deep, bar=bar))

    if progress:
        progress(total, total, "")

    # My gain first, then theirs. Among deals worth the same to me, the one
    # worth more to the partner is the one likelier to be accepted, and leaving
    # that to dictionary order threw away a strictly better deal: Davis for
    # Daniels and Pitre for Daniels are both +28 to me, and +60 against +47 to
    # him.
    deals.sort(key=lambda d: (-d.my_season, -d.their_season))
    best = _finish(mine, others, slot_list, cal, season, _distinct(deals, limit))
    return _with_odds(client, wk, mine, slot_list, cal, season, best, dist) \
        if dist else best


def _distinct(deals: list[Deal], limit: int) -> list[Deal]:
    """One row per man you would acquire, at his best price.

    Two rules were tried here. Refusing to repeat a player on EITHER side gives
    a set of deals you could do all at once, which reads well and is wrong: it
    showed Pitre for Jayden Daniels at +28/+47 while hiding Davis for Daniels at
    +28/+60, purely because Davis had already been spent on the row above.
    Identical for me and thirteen points better for the man who has to say yes.

    So the key is the incoming player alone. The question a row answers is "what
    is the cheapest thing that gets me this man", and that has one answer.
    Repeating one of my own players across rows is honest, because the rows are
    alternatives rather than a package -- which `render` says out loud, since
    each row is priced against the roster as it stands today and two of them do
    not add up.
    """
    out: list[Deal] = []
    taken: set[str] = set()
    for deal in deals:
        if deal.get.player_id in taken:
            continue
        taken.add(deal.get.player_id)
        out.append(deal)
        if len(out) >= limit:
            break
    return out


def _finish(mine, others, slot_list, cal, season, deals: list[Deal]) -> list[Deal]:
    """This Sunday's cost or gain, and their side of the cascade, for the deals
    actually shown.

    Late on purpose. Neither is the ranking axis, both are assignments per pair,
    and computing them for every pair the bounds let through was most of the
    runtime for columns that decide nothing.
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
                        - their_base[d.partner]),
            their_moves=tuple(_moves(
                _assignment(their_cands[d.partner], slot_list),
                _assignment(theirs_after, slot_list), _season))))
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


def for_league(league: str, limit: int = LIMIT, progress=None,
               only: str | None = None, reach: float = 1.0,
               shortlist: int = SHORTLIST) -> list[Deal]:
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
                dist=dist, progress=progress, only=only, reach=reach,
                shortlist=shortlist)


ASK_NOTE = (
    "\nASK says how hard a sell this is. `solid`, the numbers say he gains too. "
    "`stretch`,\nhis side lands inside the noise band, where a gain and a loss "
    "look the same, so\nit is worth asking and not worth expecting. "
    "`longshot`, the numbers do say he\nloses, and only somebody who likes "
    "trading is going to listen.")


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


def alternatives_note(deals: list[Deal]) -> str:
    """Said whenever one of my players is on offer in more than one row, which
    the `get`-keyed dedupe allows on purpose. Each row is priced against the
    roster as it stands, so two rows sharing a player are alternatives and two
    rows that do not share one still do not add up."""
    seen: dict[str, int] = {}
    for d in deals:
        seen[d.give.name] = seen.get(d.give.name, 0) + 1
    repeated = [name for name, n in seen.items() if n > 1]
    if not repeated:
        return ("Each row is priced against your roster as it stands today, so "
                "doing two of them\nis not worth the sum of the two.")
    return (f"{', '.join(repeated)} appears in more than one row: those are "
            f"alternatives, not a\npackage. Every row is priced against your "
            f"roster as it stands today, so doing\ntwo of them is not worth the "
            f"sum of the two.")


def render(deals: list[Deal], league_name: str, focus: str = "") -> str:
    head = f"{league_name} — trades" + (f" with {focus}" if focus else "")
    if not deals:
        if focus:
            return (f"{head}\nNothing with {focus} gains you more than the "
                    f"noise band at this much\npushing. Try asking for more, "
                    f"or their roster simply does not fit yours.")
        return (f"{league_name} — trades\n"
                f"No one-for-one gains you more than the noise band without "
                f"clearly costing\nthe other side. That is a normal answer: it "
                f"needs two rosters whose\nsurpluses fit each other's holes, "
                f"and most pairs of rosters do not.")
    out = [head, ""]
    out.append(f"  {'GIVE':<20}{'GET':<20}{'FROM':<14}"
               f"{'ME/SZN':>8}{'THEM/SZN':>10}{'ME/WK':>7}{'WIN%':>9}  ASK")
    for d in deals:
        odds = (f"{d.odds_now * 100:.0f}→{d.odds_after * 100:.0f}"
                if d.odds_after else "--")
        out.append(f"  {d.give.name[:19]:<20}{d.get.name[:19]:<20}"
                   f"{d.partner[:13]:<14}{d.my_season:>+8.0f}"
                   f"{d.their_season:>+10.0f}{d.my_week:>+7.1f}{odds:>9}  "
                   f"{d.ask}")
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

    why = [(d, d.why_they_might()) for d in deals]
    if any(text for _d, text in why):
        out.append("")
        out.append("WHY IT WORKS FOR THEM")
        for d, text in why:
            if text:
                out.append(f"  {d.get.name} → {d.partner}: {text}")
        out.append("")
        out.append("Losing a starter usually costs them far less than his "
                   "projection, because the\nman behind him steps up. That is "
                   "the same cascade your own side is priced\non, read from "
                   "their end.")
    out.append(ASK_NOTE.lstrip("\n"))
    out.append("")
    out.append(alternatives_note(deals))
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
    their_moves: list[Move] = ()       # and what changes in theirs
    spots: int = 0                     # roster spots freed (+) or needed (-)
    room: int = 0                      # spots I have open right now
    my_cuts: list[str] = ()            # who I would have to cut to fit them in
    their_cuts: list[str] = ()
    claimed: list[str] = ()            # men I would have to add off the wire first
    claim_cuts: list[str] = ()         # and who that claim would cost me
    claim_known: tuple = ()            # (player, drop) for claims already in
    picked_up: list[str] = ()          # men penciled into the freed spots
    picked_idle: tuple = ()            # of those, the ones who never start
    bar: float = 0.0                   # the noise band it was judged against
    week_benched: tuple = ()           # (incoming, his week, blocker, his week)
    odds_now: float = 0.0
    odds_after: float = 0.0

    @property
    def reading(self) -> str:
        """`gain`, `wash` or `loss`, judged against the noise band.

        Zero was the wrong line and it made this contradict the rest of the
        tool. Everywhere else a season gap has to clear the band before it
        means anything, and the finder refuses to list a deal that does not.
        The grader was declaring a +2 a win, which is two points on a roster
        worth 2,500 and well inside the width where these numbers cannot tell
        one outcome from another.
        """
        if self.my_season > self.bar:
            return "gain"
        return "wash" if self.my_season >= -self.bar else "loss"

    @property
    def good(self) -> bool:
        """Whether the numbers clearly favour me. Not whether to accept: depth,
        injuries and what happens to this roster in November are not in it."""
        return self.reading == "gain"

    @property
    def mutual(self) -> bool:
        return self.my_season > 0 and self.their_season > 0

    @property
    def odds_gain(self) -> float:
        return self.odds_after - self.odds_now


def pool(client, week: int, season: dict[str, float],
         size: int = 350) -> dict[str, WeeklyPlayer]:
    """{lowercased name: player} for everybody unrostered, and their season
    projections folded into `season`.

    Deliberately NOT `waivers._synthetic`, which refuses a kicker, a man ruled
    out this week and anyone with no projection today. Those are all correct
    for "would he improve my lineup on Sunday" and all wrong here: a player you
    intend to claim and trade on is currency, and this week's projection is not
    what he is being valued for.
    """
    out: dict[str, WeeklyPlayer] = {}
    try:
        raws = client.league.free_agents(size=size)
    except Exception:
        return out
    for raw in raws:
        name = getattr(raw, "name", "") or ""
        slots = frozenset(getattr(raw, "eligibleSlots", ()) or ())
        pid = str(getattr(raw, "playerId", ""))
        if not name or not slots or not pid:
            continue
        stats = (getattr(raw, "stats", {}) or {}).get(week, {}) or {}
        out[name.lower()] = WeeklyPlayer(
            player_id=pid, name=name,
            team=getattr(raw, "proTeam", None),
            pos=(getattr(raw, "position", "") or "?").upper(),
            slot="FA", eligible_slots=slots,
            status=(getattr(raw, "injuryStatus", "") or "OK").upper(),
            projected=float(stats.get("projected_points") or 0.0))
        season[pid] = float(getattr(raw, "projected_total_points", 0.0) or 0.0)
    return out


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


def _benched_this_week(cands: list[dict], slot_list: list[str],
                       incoming: list[WeeklyPlayer]) -> tuple:
    """Men arriving who do not crack THIS Sunday's lineup, and who blocks them.

    Without this, a trade whose whole point is a quarterback upgrade shows no
    change in the Sunday column and reads as if Sunday were not computed at
    all. It usually is computed and the answer is usually a near tie: Jayden
    Daniels at 19.6 does not displace Brock Purdy at 19.9, even though over a
    season it is 339 against 311.
    """
    starting = {c["espn_id"] for c in best_lineup(cands, slot_list, key=_week)}
    # Only the slots that actually start. Every player is eligible for BE, so
    # intersecting raw eligibility made a quarterback compete with a defensive
    # end and named the wrong blocker.
    real = set(slot_list)
    out = []
    for man in incoming:
        if man.player_id in starting:
            continue
        mine_slots = set(man.eligible_slots) & real
        rivals = [c for c in cands
                  if c["espn_id"] in starting
                  and (mine_slots & (c["eligible"] & real))]
        blocker = min(rivals, key=_week) if rivals else None
        here = next((c for c in cands if c["espn_id"] == man.player_id), None)
        out.append((man.name, _week(here) if here else 0.0,
                    blocker["name"] if blocker else "",
                    _week(blocker) if blocker else 0.0))
    return tuple(out)


def grade(client, give: list[str], get: list[str], week: int | None = None,
          cal=None, dist=None, fill: list[str] | None = None
          ) -> tuple[Verdict | None, str]:
    """Price an offer that already exists. (verdict, complaint).

    Any number a side. Everything the finder says about the two axes applies
    unchanged: the season number is where a trade can create value and the
    weekly one is close to zero sum.

    `fill` is who you would pencil into the spots the deal frees, off the wire.
    Sending two for one leaves a hole, and what goes in it is a real part of
    whether the deal is good. Nothing here guesses at that: name him or the
    spot stays empty and the numbers say so.
    """
    from .waivers import pending_adds, roster_room

    wk = int(week or client.week)
    slots = client.roster_slots()
    slot_list = [slot for slot, count in slots.items() for _ in range(count)]
    mine, others = rosters(client, wk)
    season = season_projections(client)

    mine_index = {p.name.lower(): p for p in mine}
    theirs_index = {p.name.lower(): (team, p)
                    for team, roster in others.items() for p in roster}
    # The pool, so a man you could claim can be part of what you send. Read
    # lazily: it is a 350 player call and most offers do not need it.
    free_index: dict[str, WeeklyPlayer] | None = None

    giving, claiming, problems = [], [], []
    for want in give:
        player, err = _pick(mine_index, want)
        if not err:
            giving.append(player)
            continue
        if free_index is None:
            free_index = pool(client, wk, season)
        found_free, free_err = _pick(free_index, want)
        if found_free is not None:
            giving.append(found_free)
            claiming.append(found_free)
            continue
        owner = theirs_index.get(want.strip().lower())
        if owner:
            problems.append(f"{owner[1].name} is on {owner[0]}'s roster, so "
                            f"he is not yours to send")
        else:
            problems.append(free_err.replace(
                "either roster", "your roster or the wire"))

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

    picking = []
    for want in fill or []:
        if free_index is None:
            free_index = pool(client, wk, season)
        found_free, free_err = _pick(free_index, want)
        if found_free is None:
            problems.append(free_err.replace("either roster", "the wire"))
        else:
            picking.append(found_free)

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

    # A claim I have already put in has a drop attached to it, decided by me.
    # Guessing a different one would be answering a question I already
    # answered, and the guess is cheapest-first, which is rarely what somebody
    # picks in the ESPN app.
    pending = pending_adds(client) if claiming else {}
    claim_ids = {p.player_id for p in claiming}
    # Claims for players NOT in this deal have still spoken for their spots.
    room = roster_room(client, mine,
                       {pid: c for pid, c in pending.items()
                        if pid not in claim_ids})

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

    # A man you intend to claim and trade on has to land on the roster first,
    # and that costs a spot before the trade gives it back. Priced as the two
    # steps it really is: the claim, then the deal, with the BEFORE still being
    # the roster exactly as it stands today.
    settled, known = [], []
    for man in claiming:
        claim = pending.get(man.player_id)
        if claim is None:
            continue
        known.append((man.name, claim.drop_name))
        if not claim.drop_name:
            continue
        already = next((c for c in my_cands
                        if c["name"] == claim.drop_name), None)
        if already is not None:
            settled.append(already)

    decided = {c["espn_id"] for c in settled}
    with_claims = ([c for c in my_cands if c["espn_id"] not in decided]
                   + [_cand(p, cal, season, incoming=True) for p in claiming])
    guessed, with_claims = make_room(
        with_claims, len(claiming) - len(settled) - room, claim_ids)
    claim_cuts = settled + guessed
    # What the claim leaves behind, which is what the trade then works with.
    left = room - len(claiming) + len(claim_cuts)

    my_cuts, mine_after = make_room(
        traded(with_claims, gave, getting),
        len(getting) - len(giving) - left, got)

    # Whoever gets penciled into the freed spots, last, because they only exist
    # once the deal has happened.
    if picking:
        spare = left + len(giving) - len(getting) + len(my_cuts)
        mine_after = mine_after + [_cand(p, cal, season, incoming=True)
                                   for p in picking]
        more, mine_after = make_room(
            mine_after, len(picking) - spare,
            got | {p.player_id for p in picking})
        my_cuts = my_cuts + more
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
        their_moves=_moves(_assignment(their_cands, slot_list),
                           _assignment(theirs_after, slot_list), _season),
        week_moves=_moves(
            best_lineup(my_cands, slot_list, key=_week),
            best_lineup(mine_after, slot_list, key=_week), _week, dp=1),
        bar=noise_band(), spots=spots, room=room,
        my_cuts=[c["name"] for c in my_cuts],
        their_cuts=[c["name"] for c in their_cuts],
        claimed=[p.name for p in claiming],
        claim_cuts=[c["name"] for c in claim_cuts],
        claim_known=tuple(known),
        picked_up=[p.name for p in picking],
        picked_idle=tuple(
            p.name for p in picking
            if p.player_id not in {c["espn_id"]
                                   for c in _assignment(mine_after, slot_list)}),
        week_benched=_benched_this_week(
            mine_after, slot_list, [*getting, *picking]),
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


def headline(v: Verdict) -> str:
    """The one-line answer, said about MY roster and nobody else's.

    The old wording was "the numbers favour you against <partner>", which reads
    as a comparison and is not one. Nothing here scores a trade as won or lost
    between two managers; it says what happens to one roster. Both sides can
    gain and usually the interesting deals do.
    """
    if v.reading == "gain":
        return (f"Your roster gains {v.my_season:.0f} points of season "
                f"projection.")
    if v.reading == "loss":
        return (f"Your roster loses {abs(v.my_season):.0f} points of season "
                f"projection.")
    return (f"Too close to call. {v.my_season:+.0f} is inside the noise band, "
            f"where these numbers cannot tell a gain from a loss.")


def claim_note(v: Verdict) -> list[str]:
    """What landing the wire players would take, as sentences.

    One place, because three views say it and the interesting part is which
    drop is a fact and which is a guess. Getting that backwards in one surface
    and right in another is exactly the drift this project keeps avoiding.
    """
    if not v.claimed:
        return []
    lead = (f"{', '.join(v.claimed)} is not on your roster yet. This assumes "
            f"you land the claim first and then make the trade, so it is two "
            f"moves and the first one can fail.")
    out = [lead]
    for man, drop in v.claim_known:
        out.append(
            f"You already have a claim in for {man}"
            + (f", dropping {drop}, so that is the drop this uses rather than "
               f"a guess." if drop else
               ", with no drop named, so it needs a spare roster spot."))
    named = {drop for _man, drop in v.claim_known}
    guessed = [c for c in v.claim_cuts if c not in named]
    if guessed:
        out.append(f"Fitting the rest in would cost you "
                   f"{', '.join(guessed)}, cheapest first, and that is already "
                   f"inside the numbers. If you would drop somebody else, put "
                   f"the claim in first and ask again.")
    elif not v.claim_cuts:
        out.append("You have the roster spot for it, so the claim itself "
                   "costs nothing.")
    return out


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

    notes = claim_note(v)
    if notes:
        out.append(fill_text(notes[0]))
        for line in notes[1:]:
            out.append(fill_text(line, initial_indent="  ", subsequent_indent="  "))
        out.append("")

    out.append(fill_text(headline(v), initial_indent="  ",
                         subsequent_indent="  "))
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

    if v.week_moves or v.week_benched:
        out.append("WHAT CHANGES this Sunday")
        for move in v.week_moves:
            out.append(f"  {move.describe()}")
        if not v.week_moves:
            out.append("  nothing")
        for name, his, blocker, theirs in v.week_benched:
            out.append(fill_text(
                f"{name} does not crack this Sunday's lineup: {his:.1f} "
                + (f"against {blocker}'s {theirs:.1f}." if blocker
                   else "and no slot he is eligible for."),
                initial_indent="  ", subsequent_indent="  "))
        out.append("")

    if v.their_moves:
        out.append(f"WHAT CHANGES for {v.partner}, over the season")
        for move in v.their_moves:
            out.append(f"  {move.describe()}")
        out.append("  Losing a starter usually costs them far less than his\n"
                   "  projection, because the man behind him steps up.")
        out.append("")

    if v.picked_up:
        said = (f"Penciled into the freed spots: {', '.join(v.picked_up)}. "
                f"Their value is inside the numbers above, so read the deal "
                f"and the pickup as one move or neither.")
        if len(v.picked_idle) == len(v.picked_up):
            said += (" None of them crack your best-eligible lineup, so the "
                     "spot is filled and the numbers do not move. That is the "
                     "usual answer: the best man on the wire rarely beats a "
                     "starter, and what you are buying is cover.")
        elif v.picked_idle:
            said += (f" {', '.join(v.picked_idle)} does not crack it, so he is "
                     f"cover rather than points.")
        out.append(fill_text(said))
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


# --- going after one man in particular -------------------------------------
#
# The finder asks what deal exists; the grader prices one somebody sent. This
# is the third question and the one an actual manager asks first: I want HIM,
# what do I have to send?
#
# The search is a lattice with a sound bound, not a sample. For any set S of my
# players, `M - S + X` is a subset of `M - p + X` for every p in S, and the
# assignment value is monotone, so:
#
#     my_season(S) <= min over p in S of my_season({p})
#
# Price every single exactly, and any package containing a man who does not
# clear my bar on his own cannot clear it either. Packages are then built only
# from the singles that survived, and a triple is bounded by its own pairs.
# Nothing is sampled and nothing is guessed at.
#
# Their side goes the other way and needs no bound: adding more players to a
# roster never makes it worse, so `their_season` only rises as a package grows.
# That is the ladder this returns -- cheapest ask first, then the sweeteners.

MAX_OUT = 3            # most players in a package
BUDGET = 600           # packages priced exactly, after the bound has cut


@dataclass(frozen=True)
class Package:
    """One way to get the man you are after."""

    give: tuple
    get: WeeklyPlayer
    partner: str
    my_season: float
    their_season: float
    my_week: float = 0.0
    their_week: float = 0.0
    their_moves: tuple = ()
    bar: float = 0.0
    bench: bool = False           # he does not crack my best-eligible lineup
    cover_name: str = ""          # the starter his absence would hurt least
    cover_points: float = 0.0     # how much of that absence he absorbs
    depth_rank: int = 0           # where he would sit at his own position

    @property
    def stretch(self) -> bool:
        return self.their_season <= self.bar

    @property
    def ask(self) -> str:
        """How hard a sell this rung is, in the finder's own words, so a ladder
        that now shows options the tests did not bless says which is which."""
        if self.their_season > self.bar:
            return "solid"
        return "stretch" if self.their_season > -self.bar else "longshot"

    @property
    def breakeven(self) -> float:
        """What he has to beat his own projection by, over the season, for this
        to pay. Zero when the deal already gains you points on its own.

        This is the honest way to price a buy-low, and the only one available.
        ESPN's number is the only view of him this system has, so it cannot
        tell you he is undervalued. It can tell you exactly how undervalued he
        would have to be, and let you decide whether you believe it.
        """
        return max(0.0, -self.my_season)

    @property
    def names(self) -> str:
        return ", ".join(p.name for p in self.give)

    def describe(self) -> str:
        return f"give {self.names} for {self.get.name}"


def cover_value(cands: list[dict], slot_list: list[str],
                incoming_id: str) -> tuple[str, float]:
    """(the starter he covers best, how much of that absence he absorbs).

    The handcuff question, and it is arithmetic rather than a forecast. It does
    NOT say how likely an injury is, because nothing here knows that. It says
    what having him is worth IF the man ahead of him misses time, which is the
    part that can be computed honestly and the part a depth chart cannot show
    you.
    """
    inc = next((c for c in cands if c["espn_id"] == incoming_id), None)
    if inc is None:
        return "", 0.0
    without = [c for c in cands if c["espn_id"] != incoming_id]
    base_with = _value(cands, slot_list, _season)
    base_without = _value(without, slot_list, _season)

    best_name, best_saved = "", 0.0
    for s in _assignment(cands, slot_list):
        if s["espn_id"] == incoming_id or not (inc["eligible"] & s["eligible"]):
            continue
        gone = s["espn_id"]
        hurt_with = base_with - _value(
            [c for c in cands if c["espn_id"] != gone], slot_list, _season)
        hurt_without = base_without - _value(
            [c for c in without if c["espn_id"] != gone], slot_list, _season)
        saved = hurt_without - hurt_with
        if saved > best_saved:
            best_name, best_saved = s["name"], saved
    return best_name, best_saved


def packages(client, target: str, week: int | None = None, cal=None,
             dist=None, limit: int = 6, max_out: int = MAX_OUT,
             band: float | None = None, least: int = LEAST,
             budget: int = BUDGET) -> tuple[list[Package], str]:
    """Ways to land one named player. (ladder, complaint).

    Cheapest for you first, then the packages that sweeten it. A rung earns its
    place by clearing the same two tests the finder uses — gains you more than
    the noise band, does not leave the other side clearly worse off — and by
    being meaningfully better for him than every rung above it.

    Those tests are right as a default and wrong as a wall. Naming a player is
    already a decision; what is wanted after that is the shape of the market,
    and one surviving rung does not show it. So `least` rungs come back
    whatever the tests say, the ones below the line labelled by `ask` rather
    than quietly mixed in with the ones above it.
    """
    from itertools import combinations

    wk = int(week or client.week)
    bar = noise_band() if band is None else band
    slots = client.roster_slots()
    slot_list = [slot for slot, count in slots.items() for _ in range(count)]
    mine, others = rosters(client, wk)
    season = season_projections(client)

    owners = {p.name.lower(): (team, p)
              for team, roster in others.items() for p in roster}
    found, err = _pick({name: player for name, (_t, player) in owners.items()},
                       target)
    if err:
        return [], err.replace("either roster",
                               "any other roster in the league")
    partner = owners[found.name.lower()][0]

    mine = [p for p in mine if season.get(p.player_id, 0.0) > 0]
    my_cands = [_cand(p, cal, season) for p in mine]
    my_base = _value(my_cands, slot_list, _season)
    theirs = [p for p in others[partner] if season.get(p.player_id, 0.0) > 0]
    their_cands = [_cand(p, cal, season) for p in theirs]
    their_chosen = _assignment(their_cands, slot_list)
    their_base = sum(_season(c) for c in their_chosen)
    incoming = _cand(found, cal, season, incoming=True)
    by_id = {p.player_id: p for p in mine}

    def price(ids: tuple[str, ...]) -> tuple[float, float, list, list]:
        out = set(ids)
        mine_after = ([c for c in my_cands if c["espn_id"] not in out]
                      + [incoming])
        theirs_after = ([c for c in their_cands
                         if c["espn_id"] != found.player_id]
                        + [_cand(by_id[i], cal, season, incoming=True)
                           for i in ids])
        return (_value(mine_after, slot_list, _season) - my_base,
                _value(theirs_after, slot_list, _season) - their_base,
                mine_after, theirs_after)

    priced: dict[tuple[str, ...], tuple[float, float]] = {}
    for c in my_cands:
        key = (c["espn_id"],)
        mine_gain, theirs_gain, _a, _b = price(key)
        priced[key] = (mine_gain, theirs_gain)

    # The floor is anchored to the cheapest way of getting him rather than to
    # zero, and that is the whole point of this being a TARGET search.
    #
    # Requiring a gain answers the wrong question. A man who does not crack my
    # starting lineup gains me nothing by definition, so demanding `> bar`
    # returns "no package exists" for every buy-low and every handcuff, which
    # is exactly the kind of move somebody goes looking for a named player to
    # make. The user has already decided he wants him. The job here is to find
    # what he costs, not to argue.
    #
    # So when nothing gains, the floor drops to within a band of the best
    # single: keep the packages that are competitive with the cheapest ask and
    # throw away the ones that are far worse. Self-anchored, and it still
    # prunes the lattice hard.
    best_single = max((gain for gain, _t in priced.values()), default=0.0)
    floor = bar if best_single > bar else best_single - bar
    survivors = [ids[0] for ids, (gain, _t) in priced.items() if gain > floor]
    for size in range(2, max_out + 1):
        sets = []
        for combo in combinations(sorted(survivors), size):
            bound = min(priced[sub][0]
                        for sub in combinations(combo, size - 1)
                        if sub in priced)
            if bound > floor:
                sets.append((bound, combo))
        sets.sort(key=lambda row: -row[0])
        for _bound, combo in sets[:max(0, budget - len(priced))]:
            mine_gain, theirs_gain, _a, _b = price(combo)
            priced[combo] = (mine_gain, theirs_gain)

    # Every package priced, in ladder order, tests or no tests. `good` is the
    # subset that passes both; `ranked` is what the top-up draws on.
    ranked = sorted(((ids, gain, theirs_gain)
                     for ids, (gain, theirs_gain) in priced.items()),
                    key=lambda row: (-row[1], len(row[0]), row[2]))
    good = [row for row in ranked if row[1] > floor and row[2] > -bar]
    if not ranked:
        return [], ""

    # The ladder: best for me first, then FEWEST players, then least generous.
    #
    # The middle term is the one that matters and it was missing at first. Rico
    # Dowdle costs me nothing on this axis, so Davis plus Dowdle scores the same
    # +32 as Davis alone and reads as better because it gives the other man
    # more. It is not better: handing over a player for nothing costs depth,
    # which none of these numbers price, so among packages worth the same to me
    # the smaller one is the one to ask for. The extra man is a sweetener and
    # belongs on a lower rung, not the top one.
    # A rung earns its place only by being meaningfully better for HIM than
    # every rung above it. Otherwise this is ten near-identical ways to pay the
    # same price.
    rungs, sill = [], None
    for ids, gain, theirs_gain in good:
        if sill is not None and theirs_gain <= sill + bar:
            continue
        sill = theirs_gain if sill is None else max(theirs_gain, sill)
        rungs.append((ids, gain, theirs_gain))
        if len(rungs) >= limit:
            break

    # Top up to `least`. Both filters above can leave one rung standing, or
    # none, and "here is the single thing that passes" is not an answer to
    # "what would it take". These are the next best for me in the same order,
    # and they carry the same `ask` label as everything else, so a rung he
    # would refuse is visibly a rung he would refuse.
    held = {ids for ids, _g, _t in rungs}
    for row in ranked:
        if len(rungs) >= min(least, limit):
            break
        if row[0] in held:
            continue
        rungs.append(row)
        held.add(row[0])
    rungs.sort(key=lambda row: (-row[1], len(row[0]), row[2]))

    out = []
    for ids, gain, theirs_gain in rungs:
        _m, _t, mine_after, theirs_after = price(ids)
        starts = any(c["espn_id"] == found.player_id
                     for c in _assignment(mine_after, slot_list))
        cover_name, cover_points = cover_value(mine_after, slot_list,
                                               found.player_id)
        at_his_spot = sorted(
            (_season(c) for c in mine_after if c["pos"] == found.pos),
            reverse=True)
        rank = (at_his_spot.index(season.get(found.player_id, 0.0)) + 1
                if season.get(found.player_id, 0.0) in at_his_spot else 0)
        out.append(Package(
            give=tuple(by_id[i] for i in ids), get=found, partner=partner,
            my_season=gain, their_season=theirs_gain,
            my_week=_value(mine_after, slot_list, _week)
            - _value(my_cands, slot_list, _week),
            their_week=_value(theirs_after, slot_list, _week)
            - _value(their_cands, slot_list, _week),
            their_moves=tuple(_moves(their_chosen,
                                     _assignment(theirs_after, slot_list),
                                     _season)),
            bar=bar, bench=not starts, cover_name=cover_name,
            cover_points=cover_points, depth_rank=rank))
    return out, ""


def _nth(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
            6: "sixth", 7: "seventh"}.get(n, f"{n}th")


def render_packages(items: list[Package], target: str, league_name: str) -> str:
    if not items:
        return (f"{league_name} — going after {target}\n"
                f"Nothing you could send gains you more than the noise band "
                f"without clearly\ncosting his owner. Either he is not an "
                f"upgrade on what you already start, or\nthe price is more than "
                f"he is worth to you.")
    got = items[0].get
    title = (f"{league_name} — going after {got.name} ({got.pos}), "
             f"{items[0].partner}")
    out = [title, ""]
    if all(p.bench for p in items):
        where = (f"would be your {_nth(items[0].depth_rank)} {got.pos} and "
                 if items[0].depth_rank else "")
        out.append(fill_text(
            f"{got.name} {where}does not crack your starting lineup, so none "
            f"of these gain you points on their own. That is not an argument "
            f"against the move, it is the shape of a buy-low: what you are "
            f"paying for is a view of him that ESPN does not share. This "
            f"prices the bet rather than making it."))
        out.append("")
    out.append(f"  {'YOU SEND':<44}{'ME/SZN':>8}{'THEM/SZN':>10}"
               f"{'ME/WK':>7}  ASK")
    for p in items:
        out.append(f"  {p.names[:43]:<44}{p.my_season:>+8.0f}"
                   f"{p.their_season:>+10.0f}{p.my_week:>+7.1f}  "
                   f"{p.ask}")
    out.append("")
    out.append("Cheapest ask first. Every rung down costs you more and is "
               "worth more to him,\nso start at the top and work down only as "
               "far as you have to.")
    if any(p.ask == "longshot" for p in items):
        out.append(ASK_NOTE.strip())

    top = items[0]
    if top.breakeven > 0:
        out.append("")
        out.append(fill_text(
            f"The cheapest ask costs you {top.breakeven:.0f} points of season "
            f"projection. For it to pay, {got.name} has to be worth that much "
            f"more over the rest of the year than ESPN currently says. Nothing "
            f"here has a view on whether he is: ESPN's number is the only one "
            f"this system has, so it can tell you the size of the bet and not "
            f"whether to take it."))
    if top.cover_name and top.cover_points > top.bar:
        out.append("")
        out.append(fill_text(
            f"As cover he is worth something already: if {top.cover_name} "
            f"misses time, having {got.name} absorbs "
            f"{top.cover_points:.0f} of the points that absence would "
            f"otherwise cost you. How likely that is, nothing here knows."))
    moves = items[-1].their_moves
    if moves:
        ins = ", ".join(f"{m.name} {m.value:.0f}" for m in moves if m.joining)
        outs = ", ".join(f"{m.name} {m.value:.0f}"
                         for m in moves if not m.joining)
        out.append("")
        out.append(f"At the bottom rung {items[0].partner} starts "
                   f"{ins or 'nobody new'}\nand loses {outs or 'nobody'}.")
    out.append("")
    out.append(ASK_NOTE.lstrip("\n"))
    return "\n".join(out)
