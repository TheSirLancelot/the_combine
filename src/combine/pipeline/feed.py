"""What just happened, built by subtraction.

ESPN's fantasy API publishes totals and never events. There is no endpoint that
says "Gibbs scored at 1:42" — only that he is on 18.4 now, and was on 12.4 the
last time anybody looked. So an event here is manufactured, and manufactured
honestly: two reads, subtracted, with the difference worded out of the same
vocabulary the stat line uses. The feed says "+6.0 · 1 car, 12 yd, 1 TD" because
those counts genuinely changed between one read and the next, not because
anything here knows what a touchdown is.

Two consequences worth being straight about, both of them stated on the page
rather than buried here:

  * The clock is when this app looked, not when the play happened. A read every
    thirty seconds puts an event within thirty seconds of the truth, which is
    plenty for the question being asked and is not a play clock.
  * Nothing accumulates while nobody is looking. The first read of the day is a
    baseline and produces no events, because a total is not a change. Open the
    app at four o'clock having not looked since ten and the feed starts at four.
    Auto-refresh on the scores page is what keeps it fed.

Only starters, on both sides of the matchups involving him. A bench player's
points are real and change nothing about who is winning, and a feed that mixes
them in is a feed nobody can skim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from .. import statline

# ESPN rounds to a tenth. Anything under half of that is float dust from the
# json, not a play.
EPSILON = 0.05
KEEP = 60          # events held per week; a Sunday does not produce more


@dataclass(frozen=True)
class Event:
    """One player's total moving between two reads."""

    league: str
    week: int
    espn_id: str
    name: str
    short: str
    pos: str
    team: str
    slot: str
    side: str           # the fantasy team he plays for
    mine: bool
    points: float       # what this read added
    total: float        # his total after it
    what: str           # '1 car, 12 yd, 1 TD', or '' when nothing nameable moved
    at: str             # iso, utc

    @property
    def clock(self) -> str:
        """'1:42 PM', local. When we looked, not when it happened."""
        try:
            when = datetime.fromisoformat(self.at).astimezone()
        except ValueError:
            return ""
        hour = when.hour % 12 or 12
        return f"{hour}:{when:%M} {'AM' if when.hour < 12 else 'PM'}"

    @property
    def scored(self) -> bool:
        return self.points > 0


def _row(conn, league: str, season: int, week: int, espn_id: str):
    return conn.execute(
        "SELECT points, stats FROM score_snapshot"
        " WHERE league=? AND season=? AND week=? AND espn_id=?",
        (league, season, week, espn_id)).fetchone()


def _keep(conn, league: str, season: int, week: int, espn_id: str,
          points: float, counts: tuple, at: str) -> None:
    conn.execute(
        "INSERT INTO score_snapshot (league, season, week, espn_id, points,"
        " stats, seen_at) VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(league, season, week, espn_id) DO UPDATE SET"
        " points=excluded.points, stats=excluded.stats, seen_at=excluded.seen_at",
        (league, season, week, espn_id, float(points),
         json.dumps(dict(counts)), at))


def observe(conn, games, season: int, at: str) -> list[Event]:
    """Record this read and return whatever moved since the last one.

    Nothing is emitted for a player seen for the first time. A baseline is not
    an event, and pretending it is would open every session with a burst of
    fictional scoring.
    """
    found: list[Event] = []
    for game in games:
        if not game.involves_me:
            continue
        for row in game.starters:
            for cell, mine in ((row.mine, True), (row.theirs, False)):
                if cell is None:
                    continue
                side = (game.me.team if mine else game.them.team)
                was = _row(conn, game.league, season, game.week, cell.espn_id)
                _keep(conn, game.league, season, game.week, cell.espn_id,
                      cell.points, cell.counts, at)
                if was is None:
                    continue
                moved = cell.points - float(was["points"])
                if abs(moved) < EPSILON:
                    continue
                before = tuple(sorted(json.loads(was["stats"]).items()))
                found.append(Event(
                    league=game.league, week=game.week, espn_id=cell.espn_id,
                    name=cell.name, short=cell.short, pos=cell.pos,
                    team=cell.team, slot=row.slot, side=side, mine=mine,
                    points=round(moved, 1), total=round(cell.points, 1),
                    what=statline.line(statline.delta(before, cell.counts)),
                    at=at))
    for e in found:
        conn.execute(
            "INSERT INTO score_event (league, season, week, espn_id, name,"
            " short, pos, team, slot, side, mine, points, total, what, at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e.league, season, e.week, e.espn_id, e.name, e.short, e.pos,
             e.team, e.slot, e.side, int(e.mine), e.points, e.total, e.what,
             e.at))
    conn.commit()
    return found


def recent(conn, season: int, week: int, limit: int = KEEP) -> list[Event]:
    """Newest first. Ordered by id rather than by time, because a burst of
    events from one read all share a timestamp to the second and the insertion
    order is the only thing that distinguishes them."""
    rows = conn.execute(
        "SELECT * FROM score_event WHERE season=? AND week=?"
        " ORDER BY id DESC LIMIT ?", (season, week, limit)).fetchall()
    return [Event(league=r["league"], week=r["week"], espn_id=r["espn_id"],
                  name=r["name"], short=r["short"], pos=r["pos"],
                  team=r["team"], slot=r["slot"], side=r["side"],
                  mine=bool(r["mine"]), points=r["points"], total=r["total"],
                  what=r["what"], at=r["at"]) for r in rows]


def render(events: list[Event]) -> str:
    """Terminal shape, for the CLI and the bot."""
    if not events:
        return ("nothing has moved since this started watching. the feed is the "
                "difference between reads, so it fills as the games go.")
    out = []
    for e in events:
        who = "*" if e.mine else " "
        head = (f"{who}{e.clock:>8}  {e.short[:18]:<18} {e.points:>+5.1f} "
                f"-> {e.total:>5.1f}")
        out.append(head + (f"   {e.what}" if e.what else ""))
    out.append("\n* your player. times are when this looked, not when the play "
               "happened.")
    return "\n".join(out)
