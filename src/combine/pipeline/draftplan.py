"""Snake draft mechanics: which of these players will still be here next time.

VAL tells you who is underpriced. It does NOT tell you to take him now, and
often means the opposite: a big positive VAL says the room lets him fall, which
is the same as saying he will probably still be there at your next pick.

The number that decides WHEN is ADP compared to your NEXT pick. So:
  take now      -> ADP says he is gone before your next turn
  can wait      -> ADP says he lasts
Among the players who will be gone, take the best VAL. That is the pick where
you paid less than the player is worth AND genuinely could not have waited.

ADP is an average, so treat it as a distribution, not a deadline. The window
below is deliberately wide.
"""

from __future__ import annotations

WINDOW = 8  # picks of slop around ADP before we call it either way


def snake_picks(slot: int, teams: int, rounds: int) -> list[int]:
    """Overall pick numbers for one drafter in a standard snake."""
    picks = []
    for rnd in range(rounds):
        offset = slot - 1 if rnd % 2 == 0 else teams - slot
        picks.append(rnd * teams + offset + 1)
    return picks


def next_pick(current: int, picks: list[int]) -> int | None:
    return next((p for p in picks if p > current), None)


def partition(rows, nxt: int | None):
    """Split available players into gone / contested / safe by ADP vs next pick.
    Players with no ADP go to 'unknown', which for RCL is every defender."""
    gone, contested, safe, unknown = [], [], [], []
    for r in rows:
        if r.adp is None:
            unknown.append(r)
        elif nxt is None:
            safe.append(r)
        elif r.adp < nxt - WINDOW:
            gone.append(r)
        elif r.adp <= nxt + WINDOW:
            contested.append(r)
        else:
            safe.append(r)
    return gone, contested, safe, unknown
