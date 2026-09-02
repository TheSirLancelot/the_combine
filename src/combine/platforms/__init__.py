"""Platform adapters. One interface, satisfied by ESPN and Yahoo.

Read-only by construction. No method here writes, and none should ever be added.
PlayerState is deliberately small: every field costs tokens once per player per call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PlayerState:
    player_id: str          # platform's id, resolved to canonical later by the crosswalk
    name: str
    team: str | None
    pos: str
    slot: str | None = None       # lineup slot, None for free agents
    status: str = "OK"            # OK | Q | D | O | IR | SUSP
    opponent: str | None = None
    platform_proj: float | None = None


@dataclass(frozen=True)
class Matchup:
    week: int
    home_team: str
    away_team: str
    home_proj: float
    away_proj: float
    home_lineup: list[PlayerState]
    away_lineup: list[PlayerState]


class LeagueClient(Protocol):
    slug: str

    def scoring_rules(self) -> dict[str, float]: ...
    def roster_slots(self) -> dict[str, int]: ...
    def my_roster(self) -> list[PlayerState]: ...
    def matchup(self, week: int | None = None) -> Matchup: ...
    def free_agents(self, position: str | None = None, limit: int = 10) -> list[PlayerState]: ...
    def injuries(self) -> list[PlayerState]: ...
    def ping(self) -> str: ...


def client_for(slug: str) -> LeagueClient:
    from ..config import get_league

    cfg = get_league(slug)
    if cfg.platform == "espn":
        from .espn import EspnClient

        return EspnClient(cfg)
    if cfg.platform == "yahoo":
        from .yahoo import YahooClient

        return YahooClient(cfg)
    raise ValueError(f"unknown platform {cfg.platform}")
