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

from dataclasses import dataclass

from ..config import get_league
from ..config import leagues as configured
from ..platforms import Matchup, client_for


@dataclass(frozen=True)
class Side:
    team: str
    score: float
    projected: float        # ESPN's projected final, which moves during games
    yet_to_play: int        # starters whose game has not finished
    mine: bool


@dataclass(frozen=True)
class Game:
    league: str
    league_name: str
    week: int
    home: Side
    away: Side

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
    )


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
                games.append(Game(
                    league=slug, league_name=cfg.name, week=m.week,
                    home=_side(m, "home", my_team),
                    away=_side(m, "away", my_team),
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
            out.append(line)

    for item in missing:
        out.append(f"\n{item.league_name}: not on the scoreboard. {item.reason}")
    return "\n".join(out).lstrip("\n")
