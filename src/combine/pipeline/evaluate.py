"""Baselines, and the metric the model has to beat.

Written before any model exists, on purpose. A model built first and measured
afterwards gets graded against whatever metric happens to flatter it, and
weekly fantasy points are noisy enough that almost anything can be made to look
good for one slice of one season.

Two metrics, because they answer different questions.

MAE is how close a number is. It is the honest accuracy measure, and ESPN's is
the bar.

Pairwise decision accuracy is the one that matters for start/sit. Of the pairs
of players you could actually have chosen between, how often did the one you
were told to prefer outscore the other. A projection can carry a worse MAE and
still order players better, and ordering is the entire job here. The subset
that counts is CLOSE calls: everyone gets Ja'Marr Chase over a backup right, so
overall pairwise accuracy mostly measures how many easy pairs are in the sample.

The population is rostered players who played and whom ESPN projected above
zero. A zero projection is ESPN saying "not playing, or not relevant", and
scoring ourselves on those is scoring on rows where there was no decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import pandas as pd

# Where the real start/sit decisions live. Beyond this the projection gap is
# doing the work and no model is needed.
CLOSE = 3.0


@dataclass(frozen=True)
class Score:
    name: str
    mae: float
    rmse: float
    n: int
    pair_acc: float          # all comparable pairs
    pair_acc_close: float    # pairs inside CLOSE points
    n_close: int

    def line(self) -> str:
        return (f"{self.name:<22} MAE {self.mae:5.2f}  RMSE {self.rmse:5.2f}  "
                f"pairs {self.pair_acc * 100:4.1f}%  close {self.pair_acc_close * 100:4.1f}% "
                f"(n={self.n}, close={self.n_close})")


def population(df: pd.DataFrame) -> pd.DataFrame:
    """Rows where a decision actually existed."""
    return df[(df["espn_proj"] > 0) & df["actual"].notna()].copy()


def pairwise(df: pd.DataFrame, pred_col: str) -> tuple[float, float, int]:
    """(accuracy over all pairs, accuracy over close pairs, close pair count).

    Pairs are formed inside one league, one week and one position family, which
    is the set of players who could plausibly have filled the same slot. Ties in
    the prediction are dropped rather than counted as half right.
    """
    wins = close_wins = total = close_total = 0
    for _, group in df.groupby(["league", "week", "family"], sort=False):
        rows = group[[pred_col, "actual"]].to_numpy()
        if len(rows) < 2 or len(rows) > 60:   # guard a pathological group
            rows = rows[:60]
        for (pa, aa), (pb, ab) in combinations(rows, 2):
            if pa == pb or aa == ab:
                continue
            right = (pa > pb) == (aa > ab)
            total += 1
            wins += right
            if abs(pa - pb) <= CLOSE:
                close_total += 1
                close_wins += right
    return (wins / total if total else 0.0,
            close_wins / close_total if close_total else 0.0,
            close_total)


def score(df: pd.DataFrame, pred_col: str, name: str) -> Score:
    d = df[df[pred_col].notna()]
    err = d[pred_col] - d["actual"]
    acc, acc_close, n_close = pairwise(d, pred_col)
    return Score(name=name, mae=err.abs().mean(),
                 rmse=(err ** 2).mean() ** 0.5, n=len(d),
                 pair_acc=acc, pair_acc_close=acc_close, n_close=n_close)


def baselines(df: pd.DataFrame) -> list[Score]:
    """Everything a model has to beat before it is worth shipping."""
    d = population(df)
    d = d.assign(
        # A player's own recent scoring, the obvious no-model predictor.
        trailing=d["own_mean_recent"],
        # ESPN's projection nudged by how wrong it has recently been on him.
        # If a model cannot beat this, it has not learned anything ESPN's own
        # number plus one lag does not already contain.
        espn_plus_bias=d["espn_proj"] + d["own_resid_recent"].fillna(0.0),
    )
    return [
        score(d, "espn_proj", "ESPN projection"),
        score(d, "trailing", f"own last {3} weeks"),
        score(d, "espn_plus_bias", "ESPN + recent bias"),
    ]


def by_family(df: pd.DataFrame, pred_col: str = "espn_proj") -> pd.DataFrame:
    d = population(df)
    rows = []
    for fam, group in d.groupby("family"):
        s = score(group, pred_col, fam)
        rows.append({"family": fam, "n": s.n, "mae": round(s.mae, 2),
                     "pair_acc": round(s.pair_acc, 3),
                     "close_acc": round(s.pair_acc_close, 3), "n_close": s.n_close})
    return pd.DataFrame(rows).sort_values("n", ascending=False)
