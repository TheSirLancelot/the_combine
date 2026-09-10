"""Pull past seasons into SQLite, so there is something to train and measure on.

The point of this module is the pairing. ESPN's past weeks carry both
`projected_points` and `points` for every rostered player, already scored under
each league's own rules, which means one row gives us the label and the
benchmark together. That is what makes "does our model beat ESPN" a measurable
question on William's actual scoring rather than an argument.

Only rostered players are pulled. Someone had to decide whether to start these
people; nobody was deciding about the free agent pool, so including it would
train the model on a population it will never be asked about.

Resumable by design. This is around a hundred requests, some of them slow, so
every step skips what is already stored and can be run again after an
interruption without duplicating or re-fetching.
"""

from __future__ import annotations

import json

from .. import db
from ..platforms import client_for

# 18 regular season weeks. Playoff weeks exist in the data but the fantasy
# population changes shape (byes, consolation brackets), so they stay out.
REGULAR_SEASON = tuple(range(1, 19))
PFF_AREAS = ("passing", "rushing", "receiving", "defense")

from ..platforms import BENCH_SLOTS


def starting(slot: str) -> bool:
    return slot not in BENCH_SLOTS


def stored_espn_weeks(conn, league: str, season: int) -> set[int]:
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT week FROM espn_player_week WHERE league=? AND season=?",
        (league, season))}


def stored_pff_weeks(conn, season: int, area: str) -> set[int]:
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT week FROM pff_player_week WHERE season=? AND area=?",
        (season, area))}


def pull_espn(conn, league: str, season: int, weeks=REGULAR_SEASON,
              log=print) -> int:
    """Every rostered player's projection and actual, week by week."""
    have = stored_espn_weeks(conn, league, season)
    todo = [w for w in weeks if w not in have]
    if not todo:
        log(f"  {league} {season}: all {len(weeks)} weeks already stored")
        return 0
    c = client_for(league, season=season)
    written = 0
    for wk in todo:
        rows = []
        for fantasy_team, versus, p in c.player_weeks(wk):
            rows.append((
                league, season, wk, p.player_id, fantasy_team, versus, p.name, p.pos,
                p.slot, ",".join(sorted(p.eligible_slots)),
                int(starting(p.slot)), p.team, p.opponent or None,
                None if p.game is None else int(p.game.home),
                p.projected, p.actual, int(p.played), p.status, db.now(),
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO espn_player_week (league, season, week, espn_id,"
            " fantasy_team, versus, name, pos, slot, eligible, started, team,"
            " opponent, is_home, projected, actual, played, status, pulled_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        written += len(rows)
        log(f"  {league} {season} wk{wk}: {len(rows)} player-weeks")
    return written


def pull_pff(conn, api, season: int, weeks=REGULAR_SEASON, areas=PFF_AREAS,
             log=print) -> int:
    """PFF's charted stat line per player per week, stored verbatim.

    The row is kept as json rather than exploded into columns: the four areas
    have different and overlapping fields, and which of the sixty-odd matter is
    exactly what the modelling is going to keep changing its mind about.
    """
    written = 0
    for area in areas:
        have = stored_pff_weeks(conn, season, area)
        todo = [w for w in weeks if w not in have]
        if not todo:
            log(f"  pff {area} {season}: all {len(weeks)} weeks already stored")
            continue
        for wk in todo:
            rows = []
            for row in api.facet(area, "summary", season=season, week=wk):
                pid = row.get("player_id")
                if not pid:
                    continue
                rows.append((season, wk, int(pid), area, row.get("player"),
                             row.get("team_name"), row.get("position"),
                             json.dumps(row), db.now()))
            conn.executemany(
                "INSERT OR REPLACE INTO pff_player_week (season, week, pff_id, area,"
                " player, team, position, stats, pulled_at) VALUES (?,?,?,?,?,?,?,?,?)",
                rows)
            conn.commit()
            written += len(rows)
            log(f"  pff {area} {season} wk{wk}: {len(rows)} rows")
    return written


def espn_players(conn, season: int) -> list:
    """Distinct ESPN players seen that season, shaped for the crosswalk.

    Rosters churn between seasons, so the current crosswalk (built from 2026
    rosters and the live pool) does not cover everyone who played in 2025.
    """
    from ..platforms import PlayerState

    seen: dict[str, PlayerState] = {}
    for r in conn.execute(
            "SELECT espn_id, name, pos, team FROM espn_player_week"
            " WHERE season=? GROUP BY espn_id", (season,)):
        seen[r["espn_id"]] = PlayerState(player_id=r["espn_id"], name=r["name"],
                                        team=r["team"], pos=r["pos"])
    return list(seen.values())


def coverage(conn, season: int) -> dict:
    """What is actually in the database, for the doctor and for sanity."""
    out: dict[str, object] = {}
    row = conn.execute(
        "SELECT COUNT(*) n, COUNT(DISTINCT week) wks, COUNT(DISTINCT espn_id) players,"
        " SUM(started) starts, SUM(actual IS NULL) no_actual"
        " FROM espn_player_week WHERE season=?", (season,)).fetchone()
    out["espn"] = dict(row) if row else {}
    out["pff"] = {
        r["area"]: {"rows": r["n"], "weeks": r["wks"]}
        for r in conn.execute(
            "SELECT area, COUNT(*) n, COUNT(DISTINCT week) wks FROM pff_player_week"
            " WHERE season=? GROUP BY area", (season,))
    }
    return out
