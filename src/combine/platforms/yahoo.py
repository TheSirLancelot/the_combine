"""Yahoo adapter. Wraps yahoofantasy. OAuth token lives in data/yahoo_token.json,
written once by scripts/yahoo_login.py and refreshed by the library.
"""

from __future__ import annotations

from ..config import SEASON, LeagueConfig
from . import Matchup, PlayerState


def ensure_game(ctx, season: int = SEASON) -> str:
    """Teach yahoofantasy this season's NFL game id.

    The library ships a HARDCODED season -> game id table that ends at 2025, so
    every call for 2026 dies with "2026 is not a valid season for nfl" before it
    ever reaches Yahoo. Nothing is wrong with the token when that happens, which
    is worth knowing because the error does not look like a stale lookup table.

    Yahoo will say what the id is, and the library ships `_find_game_id` for
    exactly this. So ask, then register the answer, rather than pinning a
    number that goes stale again next September.
    """
    from yahoofantasy.api.games import _find_game_id, games

    key = str(season)
    known = games["nfl"].get(key)
    if known:
        return str(known)
    found = _find_game_id("nfl", season, ctx)
    if not found:
        raise RuntimeError(
            f"Yahoo did not return an NFL game id for {season}. If the season "
            f"has not opened on their side yet there is nothing to fetch.")
    games["nfl"][key] = found
    return str(found)


class YahooClient:
    def __init__(self, cfg: LeagueConfig):
        self.slug = cfg.slug
        self.cfg = cfg
        self._league = None

    @property
    def league(self):
        if self._league is None:
            from yahoofantasy import Context

            ctx = Context()
            ensure_game(ctx, SEASON)
            leagues = ctx.get_leagues("nfl", SEASON)
            match = [lg for lg in leagues if str(lg.league_id) == str(self.cfg.league_id)]
            if not match:
                ids = ", ".join(str(lg.league_id) for lg in leagues)
                raise RuntimeError(f"yahoo league {self.cfg.league_id} not on this account. saw: {ids}")
            self._league = match[0]
        return self._league

    def ping(self) -> str:
        lg = self.league
        return f"{lg.name} ({len(list(lg.teams()))} teams)"

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
