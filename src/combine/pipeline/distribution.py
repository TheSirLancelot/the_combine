"""The spread around a projection, which is the thing ESPN does not give you.

A projection is one number and it is a MEAN. Across 2025 the median outcome sat
1.4 points BELOW the projection while the mean sat 0.4 below, because the
distribution is right skewed: most weeks come in under, and the average is held
up by touchdown outcomes. So "projected 12" does not mean "expect 12", and two
players projected 12 are frequently not the same bet.

Measured on 2025, players projected 8 to 16 points:

    rb            15.4% chance of a 20+ game,  24.8% chance of under half
    pass-catcher  11.8%                        26.0%
    idp            9.8%                        21.7%

A back projected for 12 booms half again as often as a receiver projected for
12, and a defender projected for 12 is the safest floor on the board. None of
that requires out-projecting ESPN, which is exactly why it survived when the
residual model did not: it uses ESPN's number and describes what happens around
it.

Everything here is empirical. No distribution is assumed and nothing is fitted;
these are quantiles of what actually happened in comparable rows.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Projection bands. Wide enough to hold a few hundred rows per position, narrow
# enough that spread genuinely differs across them: a back projected 4 and one
# projected 22 have very different shapes.
BANDS = ((0, 6), (6, 10), (10, 14), (14, 20), (20, 999))

# A 20 point week wins you a matchup on its own in most formats. Under half the
# projection is the outcome that loses you one.
BOOM = 20.0
BUST_FRACTION = 0.5

# Below this a cell is too thin to quote and we fall back to the family, then to
# everything. Picked, not derived.
MIN_CELL = 60


def band_of(proj: float) -> str:
    """A label, not a tuple: it becomes a DataFrame column, and a column of
    tuples compares elementwise instead of as a value."""
    for lo, hi in BANDS:
        if lo <= proj < hi:
            return f"{lo}-{hi}"
    lo, hi = BANDS[-1]
    return f"{lo}-{hi}"


@dataclass(frozen=True)
class Band:
    """What happened to comparable players, in points."""
    floor: float             # 10th percentile outcome
    median: float
    ceiling: float           # 90th percentile outcome
    boom: float              # P(20+ points)
    bust: float              # P(under half the projection)
    n: int
    basis: str               # which cell this came from, so thin cells are visible

    def describe(self) -> str:
        return (f"floor {self.floor:.1f}  median {self.median:.1f}  "
                f"ceiling {self.ceiling:.1f}  boom {self.boom * 100:.0f}%  "
                f"bust {self.bust * 100:.0f}%")


def _cell(rows: pd.DataFrame, proj: float, basis: str) -> Band:
    """Quantiles of the RESIDUAL, re-centred on this player's projection.

    Residuals rather than raw points, so a player projected 15 is described
    against players projected 14-20 rather than against the band's average.
    """
    resid = rows["residual"]
    return Band(
        floor=proj + float(resid.quantile(0.10)),
        median=proj + float(resid.median()),
        ceiling=proj + float(resid.quantile(0.90)),
        boom=float((rows["actual"] >= BOOM).mean()),
        bust=float((rows["actual"] < rows["espn_proj"] * BUST_FRACTION).mean()),
        n=len(rows),
        basis=basis,
    )


class Distribution:
    """Built once from history, queried per player."""

    def __init__(self, frame: pd.DataFrame):
        d = frame[(frame["espn_proj"] > 0) & frame["actual"].notna()].copy()
        d["band"] = d["espn_proj"].map(band_of) if len(d) else []
        self._d = d

    @property
    def empty(self) -> bool:
        return self._d.empty

    def for_player(self, family: str, proj: float) -> Band | None:
        if proj <= 0:
            return None
        band = band_of(proj)
        cell = self._d[(self._d["family"] == family) & (self._d["band"] == band)]
        if len(cell) >= MIN_CELL:
            return _cell(cell, proj, f"{family} {band}")
        fam = self._d[self._d["family"] == family]
        if len(fam) >= MIN_CELL:
            return _cell(fam, proj, f"{family}, all projections (thin cell)")
        if len(self._d) >= MIN_CELL:
            return _cell(self._d, proj, "all players (very thin)")
        return None

    def table(self) -> pd.DataFrame:
        """The whole grid, for eyeballing and for the README."""
        rows = []
        for (fam, band), cell in self._d.groupby(["family", "band"], observed=True):
            if len(cell) < MIN_CELL:
                continue
            b = _cell(cell, cell["espn_proj"].mean(), "")
            rows.append({"family": fam, "band": band, "n": b.n,
                         "floor": round(b.floor, 1), "median": round(b.median, 1),
                         "ceiling": round(b.ceiling, 1),
                         "boom": round(b.boom, 3), "bust": round(b.bust, 3)})
        return pd.DataFrame(rows)


def load(conn, season: int) -> Distribution:
    from .training import build

    return Distribution(build(conn, season))
