"""Fill the starting slots optimally, given a value for each player.

The swap check in lineup.py is greedy and one for one, which cannot see a move
like "put the receiver in the flex so the back can take the RB slot, and the
tight end comes off the bench". In a lineup with a flex and RCL's DP slot that
is worth real points, and it is arithmetic rather than forecasting: no
prediction improves, the same players are valued the same way, they are just
assigned better.

Exact, not heuristic. This is maximum-weight bipartite matching between players
and slots, solved by DP over subsets of slots. Lineups have at most about
fifteen starting slots, so 2^15 states times a few dozen players is nothing,
and an exact answer removes a whole class of "why did it not suggest X"
questions that a greedy version would generate forever.

The value function is a parameter on purpose. Order by expected points and you
get the standard lineup; order by ceiling or floor and you get the same
machinery serving posture.py.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

NEG = float("-inf")


def eligible_for(player: dict, slot: str) -> bool:
    """A player can fill a slot if ESPN said so. Falls back to an exact
    position match when eligibility was not captured."""
    elig = player.get("eligible")
    if elig:
        return slot in elig
    return player.get("pos") == slot


def best_lineup(players: Sequence[dict], slots: Sequence[str],
                key: Callable[[dict], float],
                playable: Callable[[dict], bool] | None = None) -> list[dict]:
    """The highest-value legal assignment of players to slots.

    Returns the chosen players. Slots that nothing can fill are left empty
    rather than filled with someone ineligible, which is what ESPN does too.
    """
    pool = [p for p in players
            if (playable(p) if playable else p.get("playable", True))]
    n_slots = len(slots)
    if not pool or not n_slots:
        return []

    # value[i][s] = what player i is worth in slot s, or NEG when ineligible.
    values = [[key(p) if eligible_for(p, slot) else NEG for slot in slots]
              for p in pool]

    full = 1 << n_slots
    # best[mask] = (total value, chosen list) using the first k players, where
    # k is the popcount of mask. Standard assignment DP: each player is either
    # skipped or placed in one still-empty slot.
    best: list[tuple[float, list[int]] | None] = [None] * full
    best[0] = (0.0, [])
    for mask in range(full):
        cur = best[mask]
        if cur is None:
            continue
        used = mask.bit_count()
        if used >= len(pool):
            continue
        total, chosen = cur
        # Skipping a player is free: the next player is considered against the
        # same mask on the following pass, which the ordering below handles by
        # letting any player index fill the next slot.
        for i, row in enumerate(values):
            if i in chosen:
                continue
            for s in range(n_slots):
                if mask & (1 << s) or row[s] == NEG:
                    continue
                nxt = mask | (1 << s)
                cand = (total + row[s], [*chosen, i])
                if best[nxt] is None or cand[0] > best[nxt][0]:
                    best[nxt] = cand

    filled = max((b for b in best if b is not None), key=lambda b: b[0])
    return [pool[i] for i in filled[1]]


def gain(players: Sequence[dict], slots: Sequence[str],
         key: Callable[[dict], float]) -> float:
    """How much the optimal lineup beats the one currently set, in whatever
    units `key` returns. Zero means the lineup is already right."""
    current = [p for p in players if p.get("started")]
    return sum(key(p) for p in best_lineup(players, slots, key)) \
        - sum(key(p) for p in current)
