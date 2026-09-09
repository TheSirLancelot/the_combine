"""Ridge regression on the residual, one model per position family.

Deliberately the smallest thing that could work. With a few thousand rows per
family and around twenty features, a linear model is the right tool, and it has
the property that the coefficients can be printed and argued with. Gradient
boosting on this sample size would mostly be a more elaborate way to fit noise,
and it is only worth adding if ridge shows signal to chase.

The model predicts `actual - espn_proj`. Predicting zero everywhere reproduces
ESPN exactly, so the failure mode of a model that learns nothing is landing on
the benchmark rather than below it.

It declines to speak about players with no history. A prediction built from
median-imputed features is a guess dressed as a number, and in ARGUE mode a
confident wrong flag is worse than silence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .training import MIN_PRIOR_WEEKS

# Weeks the model may learn from, and the weeks it is judged on. The holdout is
# never touched during fitting or tuning: lambda is chosen on an inner split of
# the training weeks.
TRAIN_WEEKS = range(1, 14)
HOLDOUT_WEEKS = range(14, 19)
INNER_TRAIN = range(1, 11)
INNER_VAL = range(11, 14)

LAMBDAS = (0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0)

# Features every family shares. Family-specific usage columns are added from
# whatever the frame actually carries, so the feature set follows training.py
# rather than being restated here and drifting out of sync.
SHARED = (
    "espn_proj",            # lets the model learn ESPN's own regression to mean
    "own_mean_recent", "own_mean_season", "own_proj_recent", "own_resid_recent",
    "def_allowed_prior", "prior_weeks", "is_home",
)


def feature_columns(df: pd.DataFrame) -> list[str]:
    usage = [c for c in df.columns if c.endswith(("_r", "_s"))]
    return [c for c in (*SHARED, *usage) if c in df.columns]


@dataclass
class Ridge:
    """Closed-form ridge on standardized features. The intercept is not
    penalized, which matters here because the mean residual is not zero."""
    columns: list[str]
    mean: np.ndarray = field(default=None, repr=False)
    scale: np.ndarray = field(default=None, repr=False)
    median: np.ndarray = field(default=None, repr=False)
    weights: np.ndarray = field(default=None, repr=False)
    intercept: float = 0.0
    lam: float = 0.0

    def _matrix(self, df: pd.DataFrame) -> np.ndarray:
        x = df[self.columns].astype(float).to_numpy()
        idx = np.where(np.isnan(x))
        x[idx] = np.take(self.median, idx[1])
        return (x - self.mean) / self.scale

    def fit(self, df: pd.DataFrame, y: np.ndarray, lam: float) -> Ridge:
        raw = df[self.columns].astype(float).to_numpy()
        self.median = np.nanmedian(raw, axis=0)
        self.median = np.where(np.isnan(self.median), 0.0, self.median)
        filled = raw.copy()
        idx = np.where(np.isnan(filled))
        filled[idx] = np.take(self.median, idx[1])
        self.mean = filled.mean(axis=0)
        self.scale = filled.std(axis=0)
        self.scale[self.scale == 0] = 1.0
        x = (filled - self.mean) / self.scale
        self.intercept = float(y.mean())
        centered = y - self.intercept
        n_features = x.shape[1]
        gram = x.T @ x + lam * np.eye(n_features)
        self.weights = np.linalg.solve(gram, x.T @ centered)
        self.lam = lam
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self._matrix(df) @ self.weights + self.intercept

    def coefficients(self) -> pd.Series:
        """In standardized units, so they compare directly against each other."""
        return pd.Series(self.weights, index=self.columns).sort_values(
            key=lambda s: s.abs(), ascending=False)


def _usable(df: pd.DataFrame) -> pd.DataFrame:
    """Rows the model is allowed to speak about: a real decision, and enough
    history that the features mean something."""
    return df[(df["espn_proj"] > 0) & df["actual"].notna()
              & (df["prior_weeks"] >= MIN_PRIOR_WEEKS)]


def choose_lambda(df: pd.DataFrame, cols: list[str]) -> tuple[float, float]:
    """(best lambda, its inner-validation MAE). Chosen on weeks 11-13 so the
    real holdout stays untouched."""
    inner = _usable(df[df["week"].isin(INNER_TRAIN)])
    val = _usable(df[df["week"].isin(INNER_VAL)])
    if len(inner) < 50 or val.empty:
        return 10.0, float("nan")
    best, best_mae = LAMBDAS[0], float("inf")
    for lam in LAMBDAS:
        m = Ridge(cols).fit(inner, inner["residual"].to_numpy(), lam)
        mae = np.abs(m.predict(val) - val["residual"].to_numpy()).mean()
        if mae < best_mae:
            best, best_mae = lam, float(mae)
    return best, best_mae


def fit_family(df: pd.DataFrame) -> tuple[Ridge | None, float]:
    cols = feature_columns(df)
    train = _usable(df[df["week"].isin(TRAIN_WEEKS)])
    if len(train) < 100:
        return None, float("nan")
    lam, inner_mae = choose_lambda(df, cols)
    return Ridge(cols).fit(train, train["residual"].to_numpy(), lam), inner_mae


def apply(model: Ridge | None, df: pd.DataFrame) -> pd.Series:
    """ESPN's projection plus the predicted residual.

    Falls back to ESPN untouched where the model has no business speaking:
    no model for that family, or not enough history on that player. That is
    what makes the worst case 'no worse than ESPN' rather than 'confidently
    wrong about rookies'.
    """
    out = df["espn_proj"].astype(float).copy()
    if model is None:
        return out
    speak = df["prior_weeks"] >= MIN_PRIOR_WEEKS
    if speak.any():
        out.loc[speak] = (df.loc[speak, "espn_proj"].astype(float)
                          + model.predict(df.loc[speak]))
    return out


def train_and_score(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Ridge]]:
    """Fit per family, score on the untouched holdout weeks, return both
    ESPN's numbers and the model's on exactly the same rows."""
    from .evaluate import score

    rows, models = [], {}
    for fam, group in frame.groupby("family"):
        if fam == "other":       # kickers and D/ST have no PFF usage to learn from
            continue
        model, _inner_mae = fit_family(group)
        holdout = _usable(group[group["week"].isin(HOLDOUT_WEEKS)])
        if model is None or holdout.empty:
            continue
        scored = holdout.assign(model_pred=apply(model, holdout))
        espn = score(scored, "espn_proj", "espn")
        ours = score(scored, "model_pred", "model")
        models[fam] = model
        rows.append({
            "family": fam, "n_train": len(_usable(group[group["week"].isin(TRAIN_WEEKS)])),
            "n_holdout": len(holdout), "lambda": model.lam,
            "espn_mae": round(espn.mae, 3), "model_mae": round(ours.mae, 3),
            "mae_delta": round(espn.mae - ours.mae, 3),
            "espn_close": round(espn.pair_acc_close, 3),
            "model_close": round(ours.pair_acc_close, 3),
            "close_delta": round(ours.pair_acc_close - espn.pair_acc_close, 3),
            "n_close": espn.n_close,
        })
    return pd.DataFrame(rows), models
