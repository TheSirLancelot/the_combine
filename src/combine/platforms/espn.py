"""ESPN adapter. Wraps espn-api, instantiated per league id. Read-only.

Field names here were taken from scripts/probe_espn.py output against the two
real leagues, not from documentation. Notes that cost us time:
  * settings.scoring_format is the canonical stat vocabulary: numeric ESPN
    stat id + abbr + points. It differs per league (RCL is IDP, DMWD has K/DST).
  * A player's projected_breakdown is league-independent raw stats, while
    projected_total_points is already scored for THIS league. Same player, two
    leagues, same breakdown, different points.
  * espn-api's friendly names in the season-level breakdown are not trustworthy
    (rushingYards came back as 81.7 against 286 carries). Weekly stats[week]
    looks sane. Prefer weekly, and prefer already-scored point totals.
  * Preseason: team.roster is [] and box_scores() raises KeyError.
"""

from __future__ import annotations

import os
from functools import lru_cache

from ..config import SEASON, LeagueConfig
from . import Matchup, PlayerState

# ESPN uses these on injuryStatus; we shorten for output width.
_STATUS = {
    "ACTIVE": "OK", "NORMAL": "OK", "QUESTIONABLE": "Q", "DOUBTFUL": "D",
    "OUT": "O", "INJURY_RESERVE": "IR", "SUSPENSION": "SUSP", "BEREAVEMENT": "OUT",
}


def _status(p) -> str:
    return _STATUS.get(getattr(p, "injuryStatus", "") or "", getattr(p, "injuryStatus", "") or "OK")


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

    @property
    def week(self) -> int:
        return max(int(self.league.current_week or 0), 1)

    def ping(self) -> str:
        lg = self.league
        return f"{lg.settings.name} ({len(lg.teams)} teams, week {lg.current_week})"

    # --- settings -------------------------------------------------------

    def scoring_rules(self) -> dict[int, float]:
        """{espn stat id: points}. The canonical scoring vocabulary."""
        return {r["id"]: r["points"] for r in self.league.settings.scoring_format}

    def scoring_labels(self) -> dict[int, str]:
        return {r["id"]: r["abbr"] for r in self.league.settings.scoring_format}

    def roster_slots(self) -> dict[str, int]:
        """Startable slots only. ESPN returns every slot it knows with 0 counts."""
        counts = self.league.settings.position_slot_counts
        return {k: v for k, v in counts.items() if v and k not in ("BE", "IR", "")}

    def _my_team(self):
        team = next(
            (t for t in self.league.teams if str(t.team_id) == str(self.cfg.team_id)), None
        )
        if team is None:
            avail = ", ".join(f"{t.team_id}={t.team_name}" for t in self.league.teams)
            raise LookupError(f"team_id {self.cfg.team_id} not in league. available: {avail}")
        return team

    # --- player state ---------------------------------------------------

    def _state(self, p, slot: str | None = None) -> PlayerState:
        return PlayerState(
            player_id=str(getattr(p, "playerId", "")),
            name=getattr(p, "name", "?"),
            team=getattr(p, "proTeam", None),
            pos=getattr(p, "position", "?"),
            slot=slot if slot is not None else (getattr(p, "lineupSlot", None) or None),
            status=_status(p),
            opponent=(getattr(p, "pro_opponent", None) or None),
            platform_proj=getattr(p, "projected_total_points", None),
        )

    def my_roster(self) -> list[PlayerState]:
        return [self._state(p) for p in self._my_team().roster]

    def free_agents(self, position: str | None = None, limit: int = 10) -> list[PlayerState]:
        """Undrafted / unrostered players, best projected first.

        Preseason this is the whole draftable pool, which is what makes it the
        useful tool before a draft. Oversample then trim, because ESPN's own
        ordering is by its ranking rather than by projection."""
        pool = self.league.free_agents(size=max(limit * 5, 50), position=position)
        pool.sort(key=lambda p: getattr(p, "projected_total_points", 0) or 0, reverse=True)
        return [self._state(p, slot="FA") for p in pool[:limit]]

    def injuries(self) -> list[PlayerState]:
        return [s for s in self.my_roster() if s.status != "OK"]

    def player(self, name: str):
        return self.league.player_info(name=name)

    def matchup(self, week: int | None = None) -> Matchup:
        raise NotImplementedError("box_scores() 404s in preseason; wire up after week 1")
