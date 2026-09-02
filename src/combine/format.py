"""Compact rendering. Every field here is paid for once per player per call,
so anything that is not decision-relevant does not belong in these strings."""

from __future__ import annotations

from .platforms import PlayerState


def tiers(points: list[float], gap_mult: float = 1.6, max_tiers: int = 12) -> list[int]:
    """Gap-based tiering on a descending list. A tier breaks where the drop to
    the next player is meaningfully larger than the typical drop so far."""
    if not points:
        return []
    out = [1]
    drops = [a - b for a, b in zip(points, points[1:])]
    if not drops:
        return out
    typical = sorted(drops)[len(drops) // 2] or 0.01
    tier = 1
    for d in drops:
        if d > typical * gap_mult and tier < max_tiers:
            tier += 1
        out.append(tier)
    return out


def player_line(s: PlayerState, rank: int | None = None, tier: int | None = None,
                extra: str = "") -> str:
    parts = []
    if rank is not None:
        parts.append(f"{rank:>3}")
    parts.append(f"{s.pos:<4}")
    parts.append(f"{s.name[:22]:<22}")
    parts.append(f"{(s.team or '--'):<3}")
    if s.platform_proj is not None:
        parts.append(f"{s.platform_proj:>6.1f}")
    if tier is not None:
        parts.append(f"T{tier:<2}")
    if s.status != "OK":
        parts.append(s.status)
    if extra:
        parts.append(extra)
    return " ".join(parts)


def ranked_table(states: list[PlayerState], header: str) -> str:
    pts = [s.platform_proj or 0.0 for s in states]
    tier_of = tiers(pts)
    lines = [header, f"{'#':>3} {'POS':<4} {'PLAYER':<22} {'TM':<3} {'PROJ':>6} TIER"]
    lines += [player_line(s, rank=i + 1, tier=tier_of[i]) for i, s in enumerate(states)]
    return "\n".join(lines)


def roster_table(states: list[PlayerState], header: str) -> str:
    if not states:
        return header + "\n(empty. the draft has not happened yet)"
    return "\n".join([header] + [player_line(s) for s in states])
