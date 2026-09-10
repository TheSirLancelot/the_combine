"""Assemble the modelling frame from what history.py stored.

One row per player-week that someone had to make a decision about. The target
is the residual, `actual - espn_projected`, not the raw score. ESPN's
projection already absorbs things we cannot see (Vegas lines, beat reporting,
depth chart churn), so the useful question is not "what will he score" but
"where is ESPN wrong". A model that learns nothing predicts a zero residual and
lands exactly on ESPN, which is the right failure mode.

THE LEAKAGE RULE, in one place so it can be checked in one place: every feature
for week W is computed from weeks strictly before W. Nothing about W itself,
not the player's own line, not his opponent's. `_prior` is the only function
that slices weeks, and everything else goes through it. A model trained on a
frame that quietly includes week W scores brilliantly and is worthless.

Features are computed on read rather than stored. The feature set will change
on every modelling pass, and stored features would need invalidating each time.
"""

from __future__ import annotations

import json
from collections import defaultdict

import pandas as pd

from ..pipeline.crosswalk import load_ids
from ..pipeline.usage import family

# Trailing window for "recent form". Three weeks is short enough to catch a
# role change and long enough not to be one good game. Picked, not derived.
RECENT = 3
# A player needs some history before trailing numbers mean anything.
MIN_PRIOR_WEEKS = 2


def _prior(rows: list[dict], week: int, window: int | None = None) -> list[dict]:
    """Rows strictly before `week`, optionally only the most recent `window`.

    The single chokepoint for every feature. If a feature does not come through
    here, it is leaking.
    """
    earlier = [r for r in rows if r["week"] < week]
    if window:
        earlier = sorted(earlier, key=lambda r: r["week"])[-window:]
    return earlier


def _mean(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return sum(vals) / len(vals) if vals else None


def load_espn(conn, season: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM espn_player_week WHERE season=? ORDER BY week", (season,))]


def load_pff(conn, season: int) -> dict[int, dict[str, list[dict]]]:
    """{pff_id: {area: [weekly rows, stats flattened]}}"""
    out: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in conn.execute(
            "SELECT week, pff_id, area, stats FROM pff_player_week WHERE season=?",
            (season,)):
        row = json.loads(r["stats"])
        row["week"] = r["week"]
        out[r["pff_id"]][r["area"]].append(row)
    return out


def defense_allowed(espn: list[dict]) -> dict[tuple[str, str, int], float]:
    """{(defending NFL team, position, week): mean fantasy points allowed}.

    Built from actuals we already have rather than from a PFF grade, because
    what a start/sit call needs is points conceded to this position, and that
    is exactly what the label column measures. One league's scoring is used
    (they differ), so this is read as a relative ranking, not an absolute.
    """
    bucket: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for r in espn:
        opp = (r.get("opponent") or "").replace("vs ", "").replace("@ ", "").strip()
        if not opp or opp == "BYE" or not r["played"]:
            continue
        bucket[(opp, r["pos"], r["week"])].append(r["actual"])
    return {k: sum(v) / len(v) for k, v in bucket.items() if v}


def _defense_prior(allowed: dict, opp: str, pos: str, week: int) -> float | None:
    vals = [v for (team, p, w), v in allowed.items()
            if team == opp and p == pos and w < week]
    return sum(vals) / len(vals) if vals else None


def _usage_features(area_rows: dict[str, list[dict]], pos: str, week: int) -> dict:
    """Trailing PFF usage for one player before one week."""
    fam = family(pos)
    out: dict[str, float | None] = {}

    def take(area: str, keys: dict[str, str], window: int | None):
        rows = _prior(area_rows.get(area, []), week, window)
        tag = "r" if window else "s"
        for out_key, src in keys.items():
            out[f"{out_key}_{tag}"] = _mean(rows, src)
        out[f"games_{tag}"] = float(len(rows))

    if fam == "pass-catcher":
        keys = {"routes": "routes", "targets": "targets", "route_rate": "route_rate",
                "yprr": "yprr", "adot": "avg_depth_of_target",
                "grade": "grades_pass_route", "yards": "yards", "recs": "receptions"}
        take("receiving", keys, RECENT)
        take("receiving", keys, None)
    elif fam == "rb":
        keys = {"carries": "attempts", "rush_yards": "yards", "yco": "yco_attempt",
                "grade": "grades_offense", "recs": "receptions"}
        take("rushing", keys, RECENT)
        take("rushing", keys, None)
        take("receiving", {"routes": "routes", "targets": "targets"}, RECENT)
    elif fam == "qb":
        keys = {"dropbacks": "dropbacks", "ypa": "ypa", "btt": "btt_rate",
                "twp": "twp_rate", "grade": "grades_pass", "pass_yards": "yards"}
        take("passing", keys, RECENT)
        take("passing", keys, None)
    elif fam == "idp":
        keys = {"snaps": "snap_counts_defense", "tackles": "tackles",
                "assists": "assists", "stops": "stops", "sacks": "sacks",
                "pressures": "total_pressures", "grade": "grades_defense"}
        take("defense", keys, RECENT)
        take("defense", keys, None)
    return out


def build(conn, season: int, leagues: list[str] | None = None,
          ids: dict[str, int] | None = None) -> pd.DataFrame:
    """The modelling frame. One row per rostered player-week with a played game.

    Rows before a player has any history are kept but flagged, since dropping
    them silently would hide how often the model has nothing to work with.
    """
    espn = load_espn(conn, season)
    if leagues:
        espn = [r for r in espn if r["league"] in leagues]
    pff = load_pff(conn, season)
    ids = load_ids() if ids is None else ids
    allowed = defense_allowed(espn)

    own: dict[str, list[dict]] = defaultdict(list)
    for r in espn:
        own[f"{r['league']}:{r['espn_id']}"].append(r)

    out = []
    for r in espn:
        if not r["played"]:
            continue
        week = r["week"]
        opp = (r.get("opponent") or "").replace("vs ", "").replace("@ ", "").strip()
        mine = _prior(own[f"{r['league']}:{r['espn_id']}"], week)
        recent = _prior(own[f"{r['league']}:{r['espn_id']}"], week, RECENT)
        pff_id = ids.get(str(r["espn_id"]))
        feats = _usage_features(pff[pff_id] if pff_id else {}, r["pos"], week) \
            if pff_id else {}

        out.append({
            "league": r["league"], "season": season, "week": week,
            "espn_id": r["espn_id"], "name": r["name"], "pos": r["pos"],
            "family": family(r["pos"]), "started": r["started"],
            "team": r["team"], "opponent": opp, "is_home": r["is_home"],
            "espn_proj": r["projected"], "actual": r["actual"],
            # The target. Positive means ESPN was low.
            "residual": r["actual"] - r["projected"],
            # Own trailing form, the baseline any model has to beat.
            "own_mean_recent": _mean(recent, "actual"),
            "own_mean_season": _mean(mine, "actual"),
            "own_proj_recent": _mean(recent, "projected"),
            "own_resid_recent": (_mean(recent, "actual") - _mean(recent, "projected"))
            if _mean(recent, "actual") is not None else None,
            "prior_weeks": len(mine),
            "thin_history": len(mine) < MIN_PRIOR_WEEKS,
            "has_pff": bool(pff_id),
            "def_allowed_prior": _defense_prior(allowed, opp, r["pos"], week),
            **feats,
        })
    return pd.DataFrame(out)
