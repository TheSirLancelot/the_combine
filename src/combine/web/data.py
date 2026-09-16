"""Pipeline answers, shaped for a template.

Every function here returns plain dicts and lists. Templates get no objects to
poke at and no pandas, because a template that knows how a dataclass is spelled
is a template that breaks when the dataclass moves.

Nothing is computed here either. These are calls into `combine.pipeline` with a
cache in front and the results flattened.
"""

from __future__ import annotations

from datetime import datetime

from .. import config
from ..pipeline.crosswalk import load_ids
from ..pipeline.distribution import Distribution
from ..pipeline.lineup import (
    near_misses,
    optimal_moves,
    order_starters,
    problems,
    split,
)
from ..pipeline.providers.pff_api import PffApi
from ..pipeline.startsit import review
from ..pipeline.usage import family, for_espn
from ..pipeline.usage import load as load_usage
from ..platforms import client_for
from .cache import memo

PRIOR = 1          # the outcome history is last season's


def now() -> str:
    return datetime.now().astimezone().strftime("%H:%M %Z")


def leagues() -> list[dict]:
    return [{"slug": slug, "name": cfg.name, "platform": cfg.platform}
            for slug, cfg in config.leagues().items()]


def espn_leagues() -> list[dict]:
    return [x for x in leagues() if x["platform"] == "espn"]


def outcomes() -> Distribution | None:
    """Last season's outcome spread, or None. Optional by design: without it
    you lose the floor and ceiling columns, not the answer."""
    def build():
        from .. import db
        from ..pipeline.training import build as frame

        try:
            with db.connect(readonly=True) as conn:
                rows = frame(conn, config.SEASON - PRIOR)
        except Exception:
            return None
        if rows is None or rows.empty:
            return None
        dist = Distribution(rows)
        return None if dist.empty else dist

    return memo(("outcomes",), 3600, build)


def pff():
    """(usage, ids, in_season, error). Optional: an expired key costs the role
    column and nothing else."""
    def build():
        try:
            api = PffApi()
            return load_usage(api), load_ids(), api.season_state().in_season, ""
        except Exception as exc:
            return {}, {}, True, f"{type(exc).__name__}: {exc}"

    return memo(("pff",), 3600, build)


def calibration(league: str):
    from ..pipeline.calibration import load as load_cal

    return memo(("cal", league), 3600, lambda: load_cal(league))


# --- the week ---------------------------------------------------------------

def _row(p, usage, ids, dist) -> dict:
    role = for_espn(usage, ids, p.player_id) if usage and ids else None
    band = dist.for_player(family(p.pos), p.projected) if dist else None
    return {
        "id": p.player_id, "name": p.name, "pos": p.pos, "slot": p.slot,
        "team": p.team or "", "opp": p.opponent,
        "proj": round(p.projected, 1), "actual": round(p.actual, 1),
        "role": role.line(p.pos) if role else "",
        "floor": round(band.floor, 1) if band else None,
        "ceiling": round(band.ceiling, 1) if band else None,
        "boom": round(band.boom * 100) if band else None,
        "bust": round(band.bust * 100) if band else None,
        "status": p.status if p.status != "OK" else "",
        "locked": bool(p.locked and not p.played),
        "bye": p.on_bye,
    }


def week(league: str, wk: int | None = None) -> dict:
    def build():
        client = client_for(league)
        match = client.matchup(wk or None)
        usage, ids, in_season, pff_err = pff()
        dist = outcomes()
        starters, bench = split(match.my_lineup)
        slots = client.roster_slots()
        try:
            calls, _hurt = review(match, usage, ids, dist=dist)
        except Exception:
            calls = []
        add, drop, gain = optimal_moves(match.my_lineup, slots)
        return {
            "week": match.week,
            "me": match.my_team, "them": match.their_team,
            "my_proj": round(match.my_proj, 1),
            "their_proj": round(match.their_proj, 1),
            "my_score": round(match.my_score, 1),
            "their_score": round(match.their_score, 1),
            "starters": [_row(p, usage, ids, dist)
                         for p in order_starters(starters, slots)],
            "bench": [_row(p, usage, ids, dist) for p in bench],
            "problems": [{"name": p.name, "pos": p.pos, "slot": p.slot,
                          "status": p.status, "bye": p.on_bye}
                         for p in problems(starters)],
            "calls": [{"bench": c.bench.name, "starter": c.starter.name,
                       "edge": round(c.proj_edge, 1),
                       "needed": round(c.needed, 1)} for c in calls],
            "optimal": {"in": [p.name for p in add],
                        "out": [p.name for p in drop],
                        "gain": round(gain, 1)},
            "near": [{"bench": n.bench.name, "starter": n.starter.name,
                      "short": round(n.short_by, 1), "why": n.explain()}
                     for n in near_misses(starters, bench, dist=dist)],
            "pff_err": pff_err, "in_season": in_season,
            "at": now(),
        }

    return memo(("week", league, wk or 0), 60, build)


# --- the wire ---------------------------------------------------------------

def waivers(league: str, wk: int | None = None) -> dict:
    def build():
        from ..pipeline.depth import for_league as depth_for
        from ..pipeline.waivers import find, notes, season_values, stash_note

        client = client_for(league)
        cands = find(client, wk or None, cal=calibration(league),
                     season_value=season_values(client), dist=outcomes())
        lineup = client.matchup(wk or None).my_lineup
        return {
            "candidates": [{
                "name": c.name, "pos": c.pos, "team": c.team or "",
                "proj": round(c.week_proj, 1),
                "week": round(c.week_gain, 1),
                "season": round(c.season_cost, 0),
                "drop": c.drop_name, "note": c.describe(),
                "free": c.is_free, "stash": c.is_stash,
                "corrected": c.clears_despite_correction,
            } for c in cands],
            "notes": notes(client, lineup),
            "stash": stash_note(client, lineup),
            "depth": [{"name": g.name, "pos": g.pos, "season": round(g.season),
                       "best": g.best_name, "theirs": round(g.best_season),
                       "gain": round(g.gain), "note": g.describe()}
                      for g in depth_for(league)],
            "at": now(),
        }

    return memo(("waivers", league, wk or 0), 120, build)


# --- the scoreboard ---------------------------------------------------------

def scores(wk: int | None = None) -> dict:
    def build():
        from ..pipeline.scoreboard import build as board

        games, missing = board(wk or None)
        return {
            "games": [{
                "league": g.league_name, "week": g.week,
                "me": g.me.team, "them": g.them.team,
                "my_score": round(g.me.score, 1),
                "their_score": round(g.them.score, 1),
                "my_proj": round(g.me.projected, 1),
                "their_proj": round(g.them.projected, 1),
                "yet_to_play": g.me.yet_to_play,
                "their_yet_to_play": g.them.yet_to_play,
                "margin": round(g.margin, 1),
                "projected_margin": round(g.projected_margin, 1),
                "final": g.final, "started": g.started,
            } for g in games if g.involves_me],
            "missing": [{"league": m.league_name, "why": m.reason}
                        for m in missing],
            "at": now(),
        }

    return memo(("scores", wk or 0), 30, build)


# --- how the advice has done ------------------------------------------------

def scorecard(wk: int | None = None) -> dict:
    def build():
        from .. import db
        from ..pipeline.scorecard import frame, summary

        try:
            with db.connect(readonly=True) as conn:
                rows = frame(conn, config.SEASON)
        except Exception as exc:
            return {"rows": [], "summary": [], "at": now(),
                    "error": f"{type(exc).__name__}: {exc}"}
        if rows is None or rows.empty:
            return {"rows": [], "summary": [], "at": now(), "error": ""}
        if wk:
            rows = rows[rows["week"] == wk]
        return {"rows": rows.to_dict("records"), "summary": summary(rows),
                "at": now(), "error": ""}

    return memo(("scorecard", wk or 0), 300, build)


# --- trades -----------------------------------------------------------------

def pickers(league: str, wk: int | None = None) -> dict:
    """Everybody you could name in a trade, grouped for the selects."""
    def build():
        from ..pipeline.trades import pool, rosters, season_projections

        client = client_for(league)
        week_no = wk or client.week
        mine, others = rosters(client, week_no)
        season = season_projections(client)
        free = pool(client, week_no, season)
        return {
            "mine": [{"name": p.name, "label": f"{p.name} · {p.pos}"}
                     for p in sorted(mine, key=lambda p: p.name)],
            "theirs": [{"name": p.name,
                        "label": f"{p.name} · {p.pos} · {team}"}
                       for team, roster in sorted(others.items())
                       for p in sorted(roster, key=lambda p: p.name)],
            "wire": [{"name": p.name, "label": f"{p.name} · {p.pos}"}
                     for p in sorted(free.values(), key=lambda p: p.name)],
            "teams": sorted(others),
        }

    return memo(("pickers", league, wk or 0), 300, build)


def grade(league: str, give: list[str], get: list[str],
          fill: list[str] | None = None):
    from ..pipeline.trades import grade as price

    return price(client_for(league), give, get, cal=calibration(league),
                 dist=outcomes(), fill=fill or [])


def target(league: str, player: str):
    from ..pipeline.trades import packages

    return packages(client_for(league), player, cal=calibration(league),
                    dist=outcomes())


def raid(league: str, team: str, reach: float):
    from ..pipeline.trades import for_league

    def build():
        return for_league(league, limit=10, only=team, reach=reach,
                          shortlist=250)

    return memo(("raid", league, team, reach), 600, build)


def find_trades(league: str):
    from ..pipeline.trades import for_league

    return memo(("find", league), 600, lambda: for_league(league))
