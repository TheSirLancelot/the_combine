"""Platform adapters. One interface, satisfied by ESPN and Yahoo.

Read-only by construction. No method here writes, and none should ever be added.
PlayerState is deliberately small: every field costs tokens once per player per call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

BENCH_SLOTS = frozenset({"BE", "IR"})


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
class WeeklyPlayer:
    """One player in one week's lineup.

    Separate from PlayerState because the weekly numbers only exist on the box
    score. The season-level roster object hands back projected_points=None, so
    the draft path and the in-season path genuinely read different objects
    rather than the same one with more fields filled in.

    eligible_slots is the field start/sit needs: it is the set of lineup slots
    this player may legally occupy, which is what makes a bench-over-starter
    swap possible or not.
    """
    player_id: str
    name: str
    team: str | None
    pos: str
    slot: str                       # lineup slot this week: RB, FLEX, BE, IR...
    eligible_slots: frozenset[str] = field(default_factory=frozenset)
    status: str = "OK"
    opponent: str | None = None
    projected: float = 0.0
    actual: float = 0.0
    played: bool = False            # game finished or in progress
    on_bye: bool = False

    @property
    def starting(self) -> bool:
        return self.slot not in BENCH_SLOTS


@dataclass(frozen=True)
class Matchup:
    week: int
    home_team: str
    away_team: str
    home_proj: float
    away_proj: float
    home_lineup: list[WeeklyPlayer]
    away_lineup: list[WeeklyPlayer]
    home_score: float = 0.0
    away_score: float = 0.0
    mine: str = "home"              # which side is the configured team

    @property
    def my_lineup(self) -> list[WeeklyPlayer]:
        return self.home_lineup if self.mine == "home" else self.away_lineup

    @property
    def their_lineup(self) -> list[WeeklyPlayer]:
        return self.away_lineup if self.mine == "home" else self.home_lineup

    @property
    def my_team(self) -> str:
        return self.home_team if self.mine == "home" else self.away_team

    @property
    def their_team(self) -> str:
        return self.away_team if self.mine == "home" else self.home_team

    @property
    def my_proj(self) -> float:
        return self.home_proj if self.mine == "home" else self.away_proj

    @property
    def their_proj(self) -> float:
        return self.away_proj if self.mine == "home" else self.home_proj

    @property
    def my_score(self) -> float:
        return self.home_score if self.mine == "home" else self.away_score

    @property
    def their_score(self) -> float:
        return self.away_score if self.mine == "home" else self.home_score


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
