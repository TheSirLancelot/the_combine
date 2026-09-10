"""Replay a season and test two ideas that need no forecasting improvement.

The counterfactual is deliberately narrow and honest:

  * Real matchups. Who played whom is stored, so a week is replayed as it
    happened rather than against an invented opponent.
  * Only my side changes. The opponent keeps the lineup they actually fielded,
    because they were not reacting to me.
  * Only legal moves, using the slot eligibility ESPN reported that week.
  * Only what was knowable before kickoff. Outcome distributions for week W are
    built from weeks before W. A rule tuned on the week it is scored on looks
    wonderful and means nothing.

Win rate is the metric because it is what is being played for. Points are
reported alongside, because a rule can raise points and lose more weeks.

Two results, one good and one not, both worth keeping:

OPTIMIZER: assigning players to slots optimally on expected points is worth
about +3.5pp of win rate and +2.5 points a week against what was actually
fielded. It ships.

POSTURE: ranking by ceiling when projected to lose and by floor when projected
to win loses at every threshold tried, and loses more the more often it fires.
See `sweep`. It does not ship.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import posture as posture_mod
from .distribution import Distribution
from .lineup import BAD_STATUS
from .optimize import best_lineup
from .usage import family

THRESHOLDS = (4, 6, 8, 12, 16, 20, 25)


def _players(rows: pd.DataFrame) -> list[dict]:
    out = []
    for r in rows.itertuples():
        elig = set((r.eligible or "").split(",")) - {""}
        out.append({
            "espn_id": r.espn_id, "name": r.name, "pos": r.pos,
            "family": family(r.pos), "eligible": elig,
            "proj": float(r.projected or 0.0), "actual": float(r.actual or 0.0),
            "started": bool(r.started),
            "playable": bool(r.projected and r.projected > 0
                             and (r.status or "OK") not in BAD_STATUS),
        })
    return out


def _slots(rows: pd.DataFrame) -> list[str]:
    """The starting slots that team actually fielded, which is the league's
    lineup requirement without needing the settings endpoint."""
    return [r.slot for r in rows.itertuples() if r.started]


@dataclass
class Replay:
    """One row per team-week, with every candidate lineup already scored, so
    thresholds can be swept without re-solving anything."""
    rows: pd.DataFrame

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def se(self) -> float:
        """One standard error on a win-rate difference, in points."""
        return float(np.sqrt(0.25 / self.n) * 100) if self.n else float("nan")


def replay(conn, season: int) -> Replay:
    frame_dists: dict[int, Distribution] = {}
    from .training import build as build_frame

    frame = build_frame(conn, season)
    for wk in sorted(frame["week"].unique()):
        frame_dists[int(wk)] = Distribution(frame[frame["week"] < wk])

    df = pd.read_sql_query(
        "SELECT * FROM espn_player_week WHERE season=?", conn, params=(season,))
    out = []
    for (_league, week), wk_rows in df.groupby(["league", "week"]):
        dist = frame_dists.get(int(week))
        if dist is None or dist.empty:
            continue
        teams = list(wk_rows.groupby("fantasy_team"))
        actual = {n: g[g.started == 1]["actual"].sum() for n, g in teams}
        projected = {n: g[g.started == 1]["projected"].sum() for n, g in teams}
        versus = {n: (g["versus"].dropna().iloc[0] if g["versus"].notna().any() else "")
                  for n, g in teams}

        for name, group in teams:
            opponent = versus.get(name) or ""
            if opponent not in actual:
                continue
            slots = _slots(group)
            if not slots:
                continue
            roster = _players(group)
            bands = {p["espn_id"]: dist.for_player(p["family"], p["proj"])
                     for p in roster}

            def keyed(attr, bands=bands):
                def key(p):
                    band = bands.get(p["espn_id"])
                    return getattr(band, attr) if band else p["proj"]
                return key

            lineups = {mode: best_lineup(roster, slots, key=keyed(attr))
                       for mode, attr in (("neutral", "median"), ("chase", "ceiling"),
                                          ("protect", "floor"))}
            optimal = best_lineup(roster, slots, key=lambda p: p["proj"])

            row = {
                "week": int(week), "team": name,
                "opp_actual": actual[opponent],
                "fielded": actual[name],
                "fielded_proj": projected[name],
                "optimal": sum(p["actual"] for p in optimal),
                "optimal_proj": sum(p["proj"] for p in optimal),
                "moved": len({p["espn_id"] for p in optimal}
                             ^ {p["espn_id"] for p in roster if p["started"]}) > 0,
                "margin": sum(p["proj"] for p in optimal) - projected[opponent],
            }
            for mode, lineup in lineups.items():
                row[f"{mode}_pts"] = sum(p["actual"] for p in lineup)
            out.append(row)
    return Replay(pd.DataFrame(out))


def optimizer_result(r: Replay) -> pd.DataFrame:
    """What optimal slot assignment is worth against what was actually set."""
    d = r.rows
    moved = d[d["moved"]]
    return pd.DataFrame([
        {"lineup": "as actually set", "n": len(d),
         "win_rate": (d["fielded"] > d["opp_actual"]).mean(),
         "pts": d["fielded"].mean()},
        {"lineup": "optimizer on projections", "n": len(d),
         "win_rate": (d["optimal"] > d["opp_actual"]).mean(),
         "pts": d["optimal"].mean()},
        {"lineup": "  ...weeks it moved someone", "n": len(moved),
         "win_rate": (moved["optimal"] > moved["opp_actual"]).mean(),
         "pts": moved["optimal"].mean() - moved["fielded"].mean()},
    ])


def posture_sweep(r: Replay, thresholds=THRESHOLDS) -> pd.DataFrame:
    """Posture against the same optimizer run on expected points."""
    d = r.rows
    base_win = (d["optimal"] > d["opp_actual"]).mean()
    rows = []
    for t in thresholds:
        pts = np.where(d["margin"] >= t, d["protect_pts"],
                       np.where(d["margin"] <= -t, d["chase_pts"], d["optimal"]))
        rows.append({
            "threshold": f"+-{t}",
            "fired": ((d["margin"] >= t) | (d["margin"] <= -t)).mean(),
            "win_rate": (pts > d["opp_actual"]).mean(),
            "vs_optimizer_pp": ((pts > d["opp_actual"]).mean() - base_win) * 100,
            "pts_delta": pts.mean() - d["optimal"].mean(),
        })
    return pd.DataFrame(rows)


def posture_modes(r: Replay) -> pd.DataFrame:
    """Each posture applied unconditionally, which isolates what the ranking
    itself does before any threshold logic is involved."""
    d = r.rows
    base_win = (d["optimal"] > d["opp_actual"]).mean()
    return pd.DataFrame([
        {"always": mode,
         "win_rate_pp": ((d[f"{mode}_pts"] > d["opp_actual"]).mean() - base_win) * 100,
         "pts_delta": d[f"{mode}_pts"].mean() - d["optimal"].mean()}
        for mode in ("neutral", "chase", "protect")
    ])


# posture_mod is imported for its thresholds and for callers that want the
# labels; referenced here so the import is not mistaken for dead weight.
DEFAULT_THRESHOLD = posture_mod.PROTECT_AT
