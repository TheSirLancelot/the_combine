"""The chance of winning a matchup, from the spreads we already measured.

A projected total is a mean, and two teams four points apart are not the same
bet as two teams four points apart with a boom-or-bust roster on one side. The
distribution work already knows what happens around a projection, per position
family and projection band, so turning two lineups into a win probability is
resampling rather than modelling.

**Nothing is fitted.** Each starter draws a residual from the real weeks of
comparable players, and the draws are added up. No normal assumption, no
variance parameter, no curve. A player with no comparable history draws zero,
which reads as "no spread known" and not as "no spread".

**The one honest bias: draws are independent, and real weeks are not.** A
quarterback and his receiver boom together, and two players in the same game
share a script. Independent draws therefore understate how much a team total
moves, which pushes every probability further from 50% than it should be. So
read these as directional, and read a 78% as "clear favourite" rather than as
78. Correcting it properly needs a correlation estimate measured from the same
history, which is a real piece of work and not a constant to guess at.
"""

from __future__ import annotations

import numpy as np

TRIALS = 20_000


def simulate(lineup: list[tuple[str, float]], dist, trials: int,
             rng: np.random.Generator) -> np.ndarray:
    """`trials` team totals, one per simulated week.

    `lineup` is (family, projection) per starter. Anyone projected at zero is
    left out rather than resampled: he is on a bye or ruled out, and comparable
    players did not have that problem.
    """
    total = np.zeros(trials)
    for family, proj in lineup:
        if proj <= 0:
            continue
        sample = dist.residuals(family, proj) if dist is not None else []
        if len(sample) == 0:
            total += proj
            continue
        total += proj + rng.choice(sample, size=trials, replace=True)
    return total


def win_probability(mine: list[tuple[str, float]],
                    theirs: list[tuple[str, float]], dist,
                    trials: int = TRIALS, seed: int = 20260916) -> float:
    """P(my total beats theirs), as a fraction.

    A fixed seed by default, because the same question asked twice should give
    the same answer. The noise at 20,000 trials is about a third of a point of
    probability, which is well inside the bias named in the module docstring.
    """
    if not mine and not theirs:
        return 0.5
    rng = np.random.default_rng(seed)
    a = simulate(mine, dist, trials, rng)
    b = simulate(theirs, dist, trials, rng)
    return float((a > b).mean() + 0.5 * (a == b).mean())


def as_lineup(players) -> list[tuple[str, float]]:
    """WeeklyPlayers in the shape `simulate` wants, using the same effective
    value the lineup code uses so a ruled-out starter counts as the zero he is.
    """
    from .lineup import effective
    from .usage import family

    return [(family(p.pos), effective(p)) for p in players]
