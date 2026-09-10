"""Per-position correction for ESPN's projections.

ESPN's projections are well calibrated on offense and badly calibrated on
individual defense. Measured on RCL's 2025 season, actual minus projected:

    S    -2.05 +-0.41 (n=204)      DE   +0.60 +-0.42 (n=216)
    CB   -1.71 +-0.51 (n=112)      DT   +2.49 +-0.99 (n=40)
    LB   -1.26 +-0.24 (n=676)
    QB, RB, WR, TE all between -0.6 and +0.5

So a cornerback projected for 9.5 is worth about 7.8, and a defensive end
projected 7.4 is worth about 8.0. Comparing those two on raw projections says
the cornerback is better by two points when he is actually worse by a fifth of
one, and that is not a rounding error, it is the wrong answer.

This mattered immediately. A first pass at the waiver work found 18 free agents
who beat an RCL starter, every one of them a cornerback or linebacker replacing a
defensive end. Corrected, 17 of the 18 vanished and the survivor was a
same-position comparison, which is the one comparison that never needed
correcting.

WHERE IT APPLIES: anywhere projections from different positions are compared,
which is the lineup optimizer and the bench-versus-starter check. Not to a
displayed projection, because the number on screen should be the number ESPN
published; a silently adjusted projection is worse than an uncorrected one.

WHERE IT DOES NOT: a correction is only used where it is statistically real,
meaning at least MIN_SAMPLE observations and a mean at least SIGNIFICANT standard
errors from zero. In DMWD that leaves every position uncorrected, which is
correct: ESPN is fine there and inventing a correction would be noise.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_SAMPLE = 40        # below this a position's bias is not measurable
SIGNIFICANT = 2.0      # standard errors from zero before a correction is used


@dataclass(frozen=True)
class Bias:
    pos: str
    n: int
    mean: float         # actual minus projected, so negative = ESPN too generous
    se: float

    @property
    def real(self) -> bool:
        return self.n >= MIN_SAMPLE and abs(self.mean) >= SIGNIFICANT * self.se

    def describe(self) -> str:
        verdict = "applied" if self.real else "not significant, ignored"
        return (f"{self.pos:<4} n={self.n:<5} {self.mean:+6.2f} +-{self.se:.2f}  "
                f"{verdict}")


@dataclass(frozen=True)
class Calibration:
    """Per-league, per-position. Empty means no correction anywhere, which is a
    valid and common answer."""
    league: str
    season: int
    biases: dict[str, Bias]

    def offset(self, pos: str) -> float:
        bias = self.biases.get((pos or "").upper())
        return bias.mean if bias and bias.real else 0.0

    def adjust(self, pos: str, points: float) -> float:
        """A projection made comparable across positions."""
        return points + self.offset(pos)

    @property
    def active(self) -> dict[str, float]:
        return {p: b.mean for p, b in self.biases.items() if b.real}

    def describe(self) -> str:
        if not self.biases:
            return f"{self.league}: no history to measure against"
        lines = [f"{self.league} (from {self.season}, actual minus projected)"]
        lines += ["  " + b.describe()
                  for b in sorted(self.biases.values(), key=lambda b: b.mean)]
        if not self.active:
            lines.append("  nothing significant: ESPN is well calibrated here")
        return "\n".join(lines)


EMPTY = Calibration(league="", season=0, biases={})


def measure(conn, league: str, season: int, before_week: int | None = None
            ) -> Calibration:
    """Bias per position from stored player-weeks.

    Only rows ESPN projected above zero and where the player's game finished: a
    zero projection means ESPN was not making a claim, and an unplayed game is
    not evidence about a projection.

    `before_week` restricts to earlier weeks, which is what a backtest must use.
    """
    sql = ("SELECT pos, COUNT(*) n, AVG(actual - projected) mean,"
           "       AVG((actual - projected) * (actual - projected)) mean_sq"
           " FROM espn_player_week"
           " WHERE league=? AND season=? AND projected > 0 AND played=1")
    params: list = [league, season]
    if before_week is not None:
        # Leakage rule, same as everywhere else: a correction used for week W is
        # measured only on weeks before W. Calibrating on the week you are
        # scoring makes any correction look good.
        sql += " AND week < ?"
        params.append(before_week)
    rows = conn.execute(sql + " GROUP BY pos", params).fetchall()
    biases: dict[str, Bias] = {}
    for row in rows:
        n = int(row["n"] or 0)
        if n < 2:
            continue
        mean = float(row["mean"] or 0.0)
        variance = max(float(row["mean_sq"] or 0.0) - mean * mean, 0.0)
        se = (variance / n) ** 0.5 if n else 0.0
        pos = (row["pos"] or "").upper()
        biases[pos] = Bias(pos=pos, n=n, mean=mean, se=se)
    return Calibration(league=league, season=season, biases=biases)


def load(league: str, season: int | None = None) -> Calibration:
    """Best-effort. No database means no correction, not an error."""
    from .. import db
    from ..config import SEASON

    try:
        with db.connect(readonly=True) as conn:
            return measure(conn, league, season or SEASON - 1)
    except Exception:
        return EMPTY
