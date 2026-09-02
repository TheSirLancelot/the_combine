"""ESPN adapter. Wraps espn-api, instantiated per league id.

Phase 1.2 fills these in. Phase 0 only needs ping() so doctor can prove the
cookies work before any tool logic exists.
"""

from __future__ import annotations

import os

from ..config import SEASON, LeagueConfig
from . import Matchup, PlayerState


class EspnClient:
    def __init__(self, cfg: LeagueConfig):
        self.slug = cfg.slug
        self.cfg = cfg
        self._league = None

    @property
    def league(self):
        if self._league is None:
            from espn_api.football import League

            self._league = League(
                league_id=int(self.cfg.league_id),
                year=SEASON,
                espn_s2=os.environ["ESPN_S2"],
                swid=os.environ["ESPN_SWID"],
            )
        return self._league

    def ping(self) -> str:
        lg = self.league
        return f"{lg.settings.name} ({len(lg.teams)} teams, week {lg.current_week})"

    def scoring_rules(self) -> dict[str, float]:
        raise NotImplementedError("phase 1.2")

    def roster_slots(self) -> dict[str, int]:
        raise NotImplementedError("phase 1.2")

    def my_roster(self) -> list[PlayerState]:
        raise NotImplementedError("phase 1.2")

    def matchup(self, week: int | None = None) -> Matchup:
        raise NotImplementedError("phase 1.2")

    def free_agents(self, position: str | None = None, limit: int = 10) -> list[PlayerState]:
        raise NotImplementedError("phase 1.2")

    def injuries(self) -> list[PlayerState]:
        raise NotImplementedError("phase 1.2")
