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

from dataclasses import dataclass, field

MIN_SAMPLE = 40        # below this a position's bias is not measurable
SIGNIFICANT = 2.0      # standard errors from zero before a correction is used


# Projection bands, matched to distribution.BANDS so one vocabulary describes
# both spread and bias. A flat per-position correction turned out to be wrong in
# an important way: ESPN is fine on lightly-projected defenders and over-projects
# the highly-projected ones, and waiver candidates are by construction always in
# the upper bands. Measured on RCL 2025:
#
#   LB   low -0.43 +-0.43   mid -1.92 +-0.42   high -1.43 +-0.41
#   CB   low -0.17 +-0.79   mid -2.55 +-0.91   high -2.45 +-0.94
#   S    low -0.35 +-0.73   mid -2.44 +-0.61   high -3.35 +-0.75
#   DE   low +1.25 +-0.65   mid -0.01 +-0.74   high +0.55 +-0.77
#
# So a flat correction is too gentle on exactly the players this is used to judge,
# and DE runs the other way at the low end, which makes a low-projected end better
# than his number rather than worse.
BANDS = ((0, 10), (10, 14), (14, 999))


def band_of(points: float) -> str:
    for lo, hi in BANDS:
        if lo <= points < hi:
            return f"{lo}-{hi}"
    lo, hi = BANDS[-1]
    return f"{lo}-{hi}"


@dataclass(frozen=True)
class Bias:
    pos: str
    n: int
    mean: float         # actual minus projected, so negative = ESPN too generous
    se: float
    band: str = ""      # projection band, empty for a whole-position figure

    @property
    def real(self) -> bool:
        return self.n >= MIN_SAMPLE and abs(self.mean) >= SIGNIFICANT * self.se

    def describe(self) -> str:
        verdict = "applied" if self.real else "not significant, ignored"
        label = f"{self.pos} {self.band}" if self.band else self.pos
        return (f"{label:<12} n={self.n:<5} {self.mean:+6.2f} +-{self.se:.2f}  "
                f"{verdict}")


@dataclass(frozen=True)
class Calibration:
    """Per-league, per-position. Empty means no correction anywhere, which is a
    valid and common answer."""
    league: str
    season: int
    biases: dict[str, Bias]                       # keyed by position
    banded: dict[tuple[str, str], Bias] = field(default_factory=dict)

    def offset(self, pos: str, points: float | None = None) -> float:
        """The correction for this player, from his projection band where that
        band is measurable.

        Three cases, and the middle one matters. A band with enough observations
        and a significant bias gets that bias. A band with enough observations
        and NO significant bias gets zero, because that is a measurement saying
        there is no bias here, not an absence of information. Only a band too
        thin to say anything falls back to the whole-position figure.

        Without that middle case the fallback actively contradicts the evidence.
        Terrel Bernard is the example: low-projected linebackers measured +0.34,
        meaning no over-projection at all, while the position-wide figure is
        -1.26 because it is dominated by the highly-projected linebackers. Handing
        him -1.26 applies a penalty his own band argues against.
        """
        pos = (pos or "").upper()
        if points is not None:
            fine = self.banded.get((pos, band_of(points)))
            if fine and fine.n >= MIN_SAMPLE:
                return fine.mean if fine.real else 0.0
        coarse = self.biases.get(pos)
        return coarse.mean if coarse and coarse.real else 0.0

    def adjust(self, pos: str, points: float) -> float:
        """A projection made comparable across positions."""
        return points + self.offset(pos, points)

    @property
    def active(self) -> dict[str, float]:
        return {p: b.mean for p, b in self.biases.items() if b.real}

    @property
    def active_bands(self) -> dict[tuple[str, str], float]:
        return {k: b.mean for k, b in self.banded.items() if b.real}

    def describe(self) -> str:
        if not self.biases:
            return f"{self.league}: no history to measure against"
        lines = [f"{self.league} (from {self.season}, actual minus projected)"]
        lines += ["  " + b.describe()
                  for b in sorted(self.biases.values(), key=lambda b: b.mean)]
        if self.active_bands:
            lines.append("  by projection band, which is what actually gets used:")
            lines += ["    " + b.describe()
                      for k, b in sorted(self.banded.items()) if b.real]
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

    banded: dict[tuple[str, Bias], Bias] = {}
    for lo, hi in BANDS:
        band_sql = sql + " AND projected >= ? AND projected < ? GROUP BY pos"
        for row in conn.execute(band_sql, [*params, lo, hi]).fetchall():
            n = int(row["n"] or 0)
            if n < 2:
                continue
            mean = float(row["mean"] or 0.0)
            variance = max(float(row["mean_sq"] or 0.0) - mean * mean, 0.0)
            se = (variance / n) ** 0.5 if n else 0.0
            pos = (row["pos"] or "").upper()
            banded[(pos, f"{lo}-{hi}")] = Bias(pos=pos, n=n, mean=mean, se=se,
                                               band=f"{lo}-{hi}")
    return Calibration(league=league, season=season, biases=biases, banded=banded)


def load(league: str, season: int | None = None) -> Calibration:
    """Best-effort. No database means no correction, not an error."""
    from .. import db
    from ..config import SEASON

    try:
        with db.connect(readonly=True) as conn:
            return measure(conn, league, season or SEASON - 1)
    except Exception:
        return EMPTY
