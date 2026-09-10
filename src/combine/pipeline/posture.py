"""Whether you should be chasing points or protecting a lead.

Start/sit is not one question. Asking "who scores more" is only right when the
matchup is close. If you are projected up by ten, the player who wins you the
week is the one least likely to bust, and if you are projected down by fifteen
the mean is nearly irrelevant because you need an outcome, not an expectation.

ESPN gives a mean and no spread, so its ordering answers the close case and
quietly answers the other two wrong. Combined with distribution.py this is a
real recommendation that requires no forecasting improvement at all: same
projections, different question.

The thresholds are picked, then checked. backtest.py replays 2025 and asks
whether a posture-aware lineup would actually have won more weeks, which is the
only thing that settles it.
"""

from __future__ import annotations

from dataclasses import dataclass

# Projected margin, in points, at which the question changes. Both picked, then
# calibrated by the backtest sweep.
PROTECT_AT = 8.0
CHASE_AT = -8.0

CHASE, NEUTRAL, PROTECT = "chase", "neutral", "protect"


@dataclass(frozen=True)
class Posture:
    mode: str
    margin: float

    @property
    def rank_by(self) -> str:
        """Which column of a Band orders the lineup."""
        return {CHASE: "ceiling", PROTECT: "floor"}.get(self.mode, "median")

    def describe(self) -> str:
        if self.mode == CHASE:
            return (f"projected down {abs(self.margin):.1f}. You need an outcome, "
                    f"not an expectation, so start for ceiling.")
        if self.mode == PROTECT:
            return (f"projected up {self.margin:.1f}. The way you lose this is a "
                    f"bust, so start for floor.")
        return (f"projected within {abs(self.margin):.1f}. Close enough that the "
                f"expected points are the right question.")


def of(my_proj: float, their_proj: float, protect_at: float = PROTECT_AT,
       chase_at: float = CHASE_AT) -> Posture:
    margin = my_proj - their_proj
    if margin >= protect_at:
        return Posture(PROTECT, margin)
    if margin <= chase_at:
        return Posture(CHASE, margin)
    return Posture(NEUTRAL, margin)
