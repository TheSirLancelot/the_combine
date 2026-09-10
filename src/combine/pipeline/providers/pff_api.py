"""PFF Premium Stats API client.

Not a projections source. Verified against the live spec on 2026-09-09: 70
endpoints of grades and charted stats, no projection, ranking or ADP anywhere.
Projections and ADP still come from the hand-exported CSVs in data/pff/. What
this gives us is usage and efficiency, which is what a start/sit call actually
turns on, plus actuals and team defense.

Two endpoint families:
  /v1/facet/<area>/<report>   every player at once, no id needed. Build here.
  /v1/player/<area>/<report>  needs an explicit player_id.

Three things learned by probing that the docs do not tell you:

1. The response envelope key is NOT mechanical, and neither is its shape.
   receiving/summary comes back as a list under "receiving_summary", but
   defense/coverage_matchup comes back under "receiving_coverage_stats" as a
   dict of three lists: defenders (1003), receivers (792) and versus (14099
   receiver-against-defender rows keyed by player_id + coverage_player_id).
   So we take the first value in the payload rather than constructing the key,
   and flat and grouped reports have separate accessors instead of one that
   quietly returns nothing for half of them.

2. Calls vary from fast to very slow. passing/summary is 142 rows in 2s;
   defense/summary is 1456 rows and 1.7MB in 15s; defense/coverage_matchup is
   4MB in 22s. Disk caching is not an optimisation here, it is what makes the
   Streamlit page usable at all.

3. Season is a trap. A season that has not kicked off still returns rows, and
   they are PRESEASON rows: season=2026 gave 602 receiving rows led by camp
   bodies at 24 targets, while season=2026&week=1 gave zero. Nothing in a facet
   row says which it is. /v1/leagues is the honest answer: it reports
   default_season and default_week, where a week below 1 is preseason
   (Hall of Fame is -1, preseason week 1 is -2, and so on). See season_state().
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import DATA_DIR

BASE = "https://api.pff.com"
CACHE_DIR = DATA_DIR / "pff_api"

# Facet payloads are stable once a week is played, and expensive to fetch.
FACET_TTL = 12 * 3600
# The calendar moves on Tuesdays; a few hours is plenty.
CALENDAR_TTL = 6 * 3600

# Regular season weeks in an NFL season. Weeks past this are playoffs, which
# PFF folds into a season total along with preseason.
REGULAR_WEEKS = 18


class PffError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeasonState:
    """Where the NFL calendar actually is, per PFF.

    week < 1 means preseason: PFF numbers exist for the season but they are
    camp snaps, and asking for a regular-season week returns nothing.
    """
    season: int
    week: int

    @property
    def in_season(self) -> bool:
        return self.week >= 1

    @property
    def stats_season(self) -> int:
        """The season whose stats are worth reading right now. Before kickoff
        that is last season, because this season's rows are preseason only."""
        return self.season if self.in_season else self.season - 1

    def describe(self) -> str:
        if self.in_season:
            return f"{self.season} week {self.week}"
        return f"{self.season} preseason, using {self.stats_season} stats as the prior"


class PffApi:
    """Read-only. Every method here is a GET.

    The key is read from the environment and never logged, printed or written
    to the cache path.
    """

    def __init__(self, key: str | None = None, cache_dir: Path = CACHE_DIR,
                 ttl: int = FACET_TTL):
        self.key = key or os.environ.get("PFF_API_KEY") or ""
        self.cache_dir = cache_dir
        self.ttl = ttl

    # --- plumbing -------------------------------------------------------

    def _cache_path(self, path: str, params: dict) -> Path:
        stamp = json.dumps({"p": path, "q": sorted(params.items())}, sort_keys=True)
        digest = hashlib.sha1(stamp.encode()).hexdigest()[:16]
        slug = path.strip("/").replace("/", "_")
        return self.cache_dir / f"{slug}_{digest}.json"

    def get(self, path: str, ttl: int | None = None, **params: Any) -> dict:
        """One GET, cached on disk. Returns the decoded payload."""
        if not self.key:
            raise PffError("PFF_API_KEY is unset")
        params = {"league": "nfl", **{k: v for k, v in params.items() if v is not None}}
        cache = self._cache_path(path, params)
        window = self.ttl if ttl is None else ttl
        if window and cache.exists() and (time.time() - cache.stat().st_mtime) < window:
            return json.loads(cache.read_text())

        import requests

        r = requests.get(BASE + path, headers={"Authorization": f"Bearer {self.key}"},
                         params=params, timeout=90)
        if r.status_code == 401:
            raise PffError("PFF rejected the key (401). Check PFF_API_KEY in .env")
        if not r.ok:
            raise PffError(f"PFF {path} returned {r.status_code}: {r.text[:200]}")
        payload = r.json()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
        return payload

    @staticmethod
    def _body(payload: dict):
        """The payload's single content value, whatever shape it is.

        Deliberately not payload[f"{area}_{report}"]: coverage_matchup answers
        under "receiving_coverage_stats", so constructing the key is wrong.
        """
        for value in payload.values():
            if isinstance(value, (list, dict)):
                return value
        return []

    # --- calendar -------------------------------------------------------

    def regular_weeks(self, season: int) -> str:
        """The `week` argument that gets REGULAR SEASON numbers for a season.

        PFF's season-level totals silently include preseason and playoff snaps.
        Drake Maye's 2025 season row is 23 games and 770 dropbacks; his regular
        season is 17 and 601, and his passing grade moves 75.2 -> 87.8 once the
        camp snaps come out. So no caller should ever ask for a bare season
        total.

        A comma-separated week list is aggregated server-side, which matters:
        summing weekly rows here would mean re-deriving rates and grades, and a
        PFF grade is not something that can be recombined from its parts.
        """
        state = self.season_state()
        last = REGULAR_WEEKS
        if season >= state.season and state.in_season:
            last = min(state.week, REGULAR_WEEKS)   # the season so far
        elif season >= state.season:
            return ""                               # preseason: nothing to ask for
        return ",".join(str(w) for w in range(1, last + 1))

    def season_state(self) -> SeasonState:
        """PFF's own view of where the calendar is. One cheap call."""
        payload = self.get("/v1/leagues", ttl=CALENDAR_TTL)
        for lg in payload.get("leagues", []):
            if str(lg.get("abbreviation", "")).lower() == "nfl" or lg.get("slug") == "nfl":
                return SeasonState(season=int(lg.get("default_season") or 0),
                                   week=int(lg.get("default_week") or 0))
        raise PffError("no nfl league in /v1/leagues")

    # --- data -----------------------------------------------------------

    def facet(self, area: str, report: str = "summary", season: int | None = None,
              week: int | None = None, ttl: int | None = None) -> list[dict]:
        """Every player's rows for one report.

        season defaults to the season whose stats are worth reading now, which
        before kickoff is LAST season. Pass season explicitly to override.
        """
        body = self._body(self._facet_payload(area, report, season, week, ttl))
        if isinstance(body, dict):
            raise PffError(
                f"{area}/{report} is a grouped report, not a flat one. "
                f"use facet_groups(); groups: {', '.join(body)}")
        return [x for x in body if isinstance(x, dict)]

    def facet_groups(self, area: str, report: str, season: int | None = None,
                     week: int | None = None, ttl: int | None = None
                     ) -> dict[str, list[dict]]:
        """Grouped reports, where the payload is several lists rather than one.

        defense/coverage_matchup is the one that matters: {defenders,
        receivers, versus}, where versus is the receiver-against-defender
        table. It is 4MB and takes ~20s uncached, so treat it as a weekly pull.
        """
        body = self._body(self._facet_payload(area, report, season, week, ttl))
        if not isinstance(body, dict):
            return {"rows": [x for x in body if isinstance(x, dict)]}
        return {k: [x for x in v if isinstance(x, dict)]
                for k, v in body.items() if isinstance(v, list)}

    def _facet_payload(self, area: str, report: str, season: int | None,
                       week: int | None, ttl: int | None) -> dict:
        if season is None:
            season = self.season_state().stats_season
        return self.get(f"/v1/facet/{area}/{report}", ttl=ttl,
                        season=season, week=week)

    def players(self, name: str) -> list[dict]:
        """Name lookup, which is where PFF's stable player_id comes from.

        Substring matching: "Josh Allen" also returns Josh Hines-Allen, so
        callers must filter rather than taking the first hit.
        """
        return self.get("/v1/players", ttl=7 * 24 * 3600, name=name).get("players", [])

    def whoami(self) -> dict:
        return self.get("/v1/auth/whoami", ttl=CALENDAR_TTL)


# Reports that carry fantasy-relevant usage and efficiency, cheap enough to
# pull together. defense/summary is 1456 rows and slow, so it is not in here;
# it gets pulled on its own when the IDP and team-defense work needs it.
SKILL_REPORTS = (("passing", "summary"), ("rushing", "summary"), ("receiving", "summary"))
