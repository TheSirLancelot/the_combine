"""Live scores across every league at once.

One place to see all three matchups on a Sunday, rather than three apps. The
numbers come from the same box scores the week view reads, so nothing new is
fetched per league.

Two derived figures earn their place beside the score. How many starters have
not finished, because a 20 point lead with nine players left is not a lead. And
ESPN's projected final, which moves during the games as its own model updates,
so it is labelled as ESPN's rather than presented as ours.

The hand-entered Yahoo league cannot appear: a scoreboard needs an opponent's
lineup, and entering one every week by hand is more upkeep than a score line is
worth. It shows as unavailable with the reason, rather than silently going
missing, so nobody has to wonder whether it broke.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from ..config import get_league
from ..config import leagues as configured
from ..platforms import Matchup, WeeklyPlayer, client_for


@dataclass(frozen=True)
class Cell:
    """One player as a scoreboard shows him, which is not how a roster does.

    A roster answers "who do I have". This answers "what is he doing right now",
    so the kickoff and the opponent sit next to the score and matter as much:
    twelve points with the game over is a different number from twelve points at
    halftime, and no view that prints them the same way is being honest.
    """
    name: str
    short: str              # 'B. Purdy'; a phone has room for one of the two
    pos: str
    team: str
    opponent: str           # '@ DEN', 'vs KC', 'BYE', or '' when unknown
    kickoff: str            # 'Sun 1:25 PM', '' on a bye or an unknown schedule
    status: str             # 'Q', 'O', 'D'... only when it is not plain OK
    line: str               # '15/25, 155 yd, 1 INT', or '' before he plays
    points: float
    projected: float
    played: bool            # his game has finished or is under way
    locked: bool            # kickoff has passed, so the lineup call is spent


@dataclass(frozen=True)
class Row:
    """One line of the head to head: a slot, and the man in it on each side."""
    slot: str
    mine: Cell | None       # None when one side has fewer players than the other
    theirs: Cell | None

    @property
    def lead(self) -> float:
        """My points minus his, which is the only number this row is for."""
        return (self.mine.points if self.mine else 0.0) - \
               (self.theirs.points if self.theirs else 0.0)


@dataclass(frozen=True)
class Side:
    team: str
    score: float
    projected: float        # ESPN's projected final, which moves during games
    yet_to_play: int        # starters whose game has not finished
    mine: bool
    # ESPN's own chance-to-win, 0..1. None on any week it does not publish one,
    # which is every week but the one in play.
    win_prob: float | None = None


@dataclass(frozen=True)
class Game:
    league: str
    league_name: str
    week: int
    home: Side
    away: Side
    # The head to head, already oriented: `mine` is my side when I am playing,
    # and the home side when I am not. Tuples because Game is frozen and a
    # frozen dataclass holding a list is a hash waiting to fail.
    starters: tuple[Row, ...] = ()
    bench: tuple[Row, ...] = ()

    @property
    def involves_me(self) -> bool:
        return self.home.mine or self.away.mine

    @property
    def final(self) -> bool:
        return self.home.yet_to_play == 0 and self.away.yet_to_play == 0

    @property
    def started(self) -> bool:
        return (self.home.score + self.away.score) > 0 or self.final

    @property
    def me(self) -> Side | None:
        return self.home if self.home.mine else (self.away if self.away.mine else None)

    @property
    def them(self) -> Side | None:
        return self.away if self.home.mine else (self.home if self.away.mine else None)

    @property
    def margin(self) -> float:
        """My score minus theirs, or home minus away when I am not playing."""
        mine, theirs = (self.me, self.them) if self.involves_me else (self.home, self.away)
        return mine.score - theirs.score

    @property
    def projected_margin(self) -> float:
        mine, theirs = (self.me, self.them) if self.involves_me else (self.home, self.away)
        return mine.projected - theirs.projected


@dataclass(frozen=True)
class Unavailable:
    league: str
    league_name: str
    reason: str


def _side(m: Matchup, side: str, my_team: str) -> Side:
    lineup = m.home_lineup if side == "home" else m.away_lineup
    team = m.home_team if side == "home" else m.away_team
    return Side(
        team=team,
        score=m.home_score if side == "home" else m.away_score,
        projected=m.home_proj if side == "home" else m.away_proj,
        yet_to_play=sum(1 for p in lineup if p.starting and not p.played),
        mine=bool(my_team) and team == my_team,
        win_prob=m.home_win_prob if side == "home" else m.away_win_prob,
    )


def _clock(ms: int) -> str:
    """'Sun 1:25 PM'. Built by hand rather than with strftime because the
    zero-padding flag for the hour is spelled differently on every platform and
    this runs on two of them."""
    # Local on purpose: the server runs where he does, and a kickoff shown in
    # UTC is a kickoff nobody can use.
    at = datetime.fromtimestamp(ms / 1000)  # noqa: DTZ006
    hour = at.hour % 12 or 12
    return f"{at:%a} {hour}:{at:%M} {'AM' if at.hour < 12 else 'PM'}"


def _short(name: str) -> str:
    """'Jahmyr Gibbs' -> 'J. Gibbs'. What ESPN does, for the reason ESPN does
    it: two names have to fit either side of a slot on a 390 point screen, and a
    surname carries almost all of the identification.

    Eleven characters is where it starts, measured rather than guessed: that is
    what fits the name column on the narrowest phone, so anything at or under it
    is left whole and only the ones that would be clipped are cut. Left alone
    too when there is nothing to cut — a one word name, or a defense."""
    parts = name.split()
    if len(parts) < 2 or len(name) <= 11 or len(parts[0]) < 2:
        return name
    return f"{parts[0][0]}. " + " ".join(parts[1:])


def _cell(p: WeeklyPlayer) -> Cell:
    return Cell(
        name=p.name,
        short=_short(p.name),
        pos=p.pos,
        team=p.team or "",
        opponent=p.opponent,
        kickoff="" if p.on_bye or not p.game else _clock(p.game.kickoff_ms),
        status="" if p.status in ("OK", "ACTIVE", "") else p.status,
        line=p.stat_line,
        points=p.actual,
        projected=p.projected,
        played=p.played,
        locked=p.locked,
    )


def _pair(mine: list[WeeklyPlayer], theirs: list[WeeklyPlayer]) -> list[Row]:
    """Line the two lineups up slot against slot, the way a cast reads.

    The slot order comes from my lineup rather than from a table of our own,
    because ESPN already returns it in the order the site prints it and a second
    opinion about where the flex goes would only ever disagree. Where his lineup
    holds a slot mine does not, the extra is appended rather than dropped: a row
    with one side empty is information, and a missing row is a lie.
    """
    order = [p.slot for p in mine]
    short = Counter(p.slot for p in theirs) - Counter(order)
    for slot, extra in short.items():
        order += [slot] * extra

    left: dict[str, list[WeeklyPlayer]] = {}
    right: dict[str, list[WeeklyPlayer]] = {}
    for p in mine:
        left.setdefault(p.slot, []).append(p)
    for p in theirs:
        right.setdefault(p.slot, []).append(p)

    rows = []
    for slot in order:
        a = left.get(slot) or []
        b = right.get(slot) or []
        rows.append(Row(slot=slot,
                        mine=_cell(a.pop(0)) if a else None,
                        theirs=_cell(b.pop(0)) if b else None))
    return rows


def _cast(m: Matchup, flip: bool) -> tuple[tuple[Row, ...], tuple[Row, ...]]:
    """(starters, bench). `flip` when the away side is the one to show on the
    left, which is the case exactly when the away team is mine."""
    a, b = (m.away_lineup, m.home_lineup) if flip else (m.home_lineup, m.away_lineup)
    starters = _pair([p for p in a if p.starting], [p for p in b if p.starting])
    # The bench pairs by position in the list, not by slot: every bench player
    # sits in the same slot, so pairing by slot would be pairing by nothing.
    sit_a = [p for p in a if not p.starting]
    sit_b = [p for p in b if not p.starting]
    bench = [Row(slot=(sit_a[i].slot if i < len(sit_a) else sit_b[i].slot),
                 mine=_cell(sit_a[i]) if i < len(sit_a) else None,
                 theirs=_cell(sit_b[i]) if i < len(sit_b) else None)
             for i in range(max(len(sit_a), len(sit_b)))]
    return tuple(starters), tuple(bench)


def build(week: int | None = None, slugs: list[str] | None = None
          ) -> tuple[list[Game], list[Unavailable]]:
    """(games, leagues that cannot be shown and why)."""
    games: list[Game] = []
    missing: list[Unavailable] = []

    for slug in (slugs or list(configured())):
        cfg = get_league(slug)
        try:
            client = client_for(slug)
            if not hasattr(client, "all_matchups"):
                missing.append(Unavailable(
                    slug, cfg.name,
                    "hand-entered, so there is no opponent data. a scoreboard needs "
                    "the other lineup, which the Yahoo API will provide once "
                    "approved."))
                continue
            my_team = client.my_team_name()
            for m in client.all_matchups(week):
                home = _side(m, "home", my_team)
                away = _side(m, "away", my_team)
                starters, bench = _cast(m, flip=away.mine)
                games.append(Game(
                    league=slug, league_name=cfg.name, week=m.week,
                    home=home, away=away, starters=starters, bench=bench,
                ))
        except Exception as exc:
            missing.append(Unavailable(slug, cfg.name,
                                       f"{type(exc).__name__}: {exc}"))
    # My games first, then the rest in league order: this is a scoreboard for one
    # person, and his own matchup is the reason he opened it.
    games.sort(key=lambda g: (not g.involves_me, g.league))
    return games, missing


def _mark(g: Game) -> str:
    """Two columns: whose game it is, and what state it is in."""
    return ("*" if g.involves_me else " ") + ("F" if g.final else
                                              ">" if g.started else "-")


def _score_line(g: Game, width: int = 22) -> str:
    return (f"{_mark(g)} {g.home.team[:width]:<{width}} {g.home.score:>6.1f} "
            f"{g.home.projected:>6.1f} {g.home.yet_to_play:>3}   "
            f"{g.away.team[:width]:<{width}} {g.away.score:>6.1f} "
            f"{g.away.projected:>6.1f} {g.away.yet_to_play:>3}")


def render(games: list[Game], missing: list[Unavailable], width: int = 22) -> str:
    """Terminal shape. The legend is printed because unexplained single-character
    markers in a column are worse than no markers."""
    if not games and not missing:
        return "no leagues configured"
    out: list[str] = []
    header = (f"   {'TEAM':<{width}} {'SCORE':>6} {'PROJ':>6} {'LFT':>3}   "
              f"{'OPPONENT':<{width}} {'SCORE':>6} {'PROJ':>6} {'LFT':>3}")

    for slug in dict.fromkeys(g.league for g in games):
        rows = [g for g in games if g.league == slug]
        out.append(f"\n{rows[0].league_name} — week {rows[0].week}")
        out.append(header)
        out += [_score_line(g, width) for g in rows]

    if games:
        out.append("\n* your matchup   F final   > in progress   - not started"
                   "\nPROJ is ESPN's projected final, which moves during games."
                   "\nLFT is starters whose game has not ended.")

    mine = [g for g in games if g.involves_me]
    if mine:
        out.append("\nYOU")
        for g in mine:
            me, them = g.me, g.them
            verb = ("won by" if g.final and g.margin > 0 else
                    "lost by" if g.final and g.margin < 0 else
                    "tied" if g.final else
                    "up" if g.margin > 0 else "down" if g.margin < 0 else "level")
            line = (f"  {g.league_name}: {me.score:.1f} to {them.score:.1f}, "
                    f"{verb}{'' if verb in ('level', 'tied') else f' {abs(g.margin):.1f}'}")
            if not g.final:
                line += (f"\n    ESPN projects {me.projected:.1f} to "
                         f"{them.projected:.1f} ({g.projected_margin:+.1f}), "
                         f"{me.yet_to_play} of yours left against "
                         f"{them.yet_to_play} of theirs")
                if me.win_prob is not None:
                    line += (f"\n    ESPN gives you {me.win_prob * 100:.0f}% "
                             f"against their {them.win_prob * 100:.0f}%")
            out.append(line)

    for item in missing:
        out.append(f"\n{item.league_name}: not on the scoreboard. {item.reason}")
    return "\n".join(out).lstrip("\n")
