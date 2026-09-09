"""A league entered by hand, for when there is no API yet.

The Yahoo league's API access is still in review, so its roster and settings
live in config/<slug>_league.toml and config/<slug>_roster.csv. This adapter
makes that look enough like a real league that the week view, the optimizer and
the comparison threshold all work against it unchanged.

Projections are built rather than fetched. ESPN publishes a raw projected stat
line per player per week, independent of any league's scoring, so we take that
line and score it under this league's own rules. The numbers will therefore not
match what Yahoo displays: Yahoo has its own projections, and this is ESPN's
view of the player priced by Yahoo's rules. That is the more useful property,
because it is the same methodology as the other two leagues.

Read-only like everything else. Nothing here writes anywhere.
"""

from __future__ import annotations

import csv
import os
from functools import cached_property

from ..config import CONFIG_DIR, SEASON
from ..pipeline.scoring import ScoringTable, load_league_file, load_table
from . import Matchup, PlayerState, ProGame, WeeklyPlayer

BENCH = ("BN", "IR")


class ManualClient:
    """Enough of LeagueClient to run the in-season tools."""

    platform = "manual"

    def __init__(self, slug: str, season: int = SEASON):
        self.slug = slug
        self.season = int(season)
        self.spec = load_league_file(slug)
        self.table: ScoringTable = load_table(CONFIG_DIR / f"{slug}_league.toml")

    # --- settings -------------------------------------------------------

    @property
    def name(self) -> str:
        return self.spec.get("name", self.slug)

    def roster_slots(self) -> dict[str, int]:
        return dict(self.spec.get("slots", {}))

    def team_count(self) -> int:
        return int(self.spec.get("teams", 12))

    def roster_size(self) -> int:
        bench = self.spec.get("bench", {})
        return sum(self.roster_slots().values()) + int(bench.get("BN", 0))

    def scoring_rules(self) -> dict[int, float]:
        return dict(self.table.rules)

    def scoring_labels(self) -> dict[int, str]:
        return {i: str(i) for i in self.table.rules}

    def eligible_slots(self, pos: str) -> frozenset[str]:
        """Which lineup slots a position may fill, from the league file.

        Yahoo names its flexes W/R and W/R/T, and the file says which positions
        each accepts, so this needs no hardcoded knowledge of either platform.
        """
        pos = pos.upper()
        slots = {s for s in self.roster_slots() if s.upper() == pos}
        for slot, accepts in (self.spec.get("eligibility") or {}).items():
            if pos in {a.upper() for a in accepts}:
                slots.add(slot)
        return frozenset(slots | {"BN"})

    # --- the stat-line source -------------------------------------------

    @cached_property
    def _source(self):
        """An ESPN league, used purely as a projection source.

        Any league works: the stat lines are league independent, and only the
        already-scored point totals are league specific, which is exactly what
        we are replacing.
        """
        from espn_api.football import League

        league_id = os.environ.get("ESPN_RCL_ID") or os.environ.get("ESPN_DMWD_ID")
        if not league_id:
            raise RuntimeError("no ESPN league configured to read stat lines from")
        return League(league_id=int(league_id), year=self.season,
                      espn_s2=os.environ["ESPN_S2"], swid=os.environ["ESPN_SWID"])

    @cached_property
    def week(self) -> int:
        return max(int(self._source.current_week or 0), 1)

    @cached_property
    def _stat_lines(self) -> dict[str, object]:
        """{lowercased player name: espn player object with weekly stats}.

        Box scores across the configured ESPN leagues cover every rostered
        player; the free agent pool covers the rest. Both carry a weekly
        projected stat line, which the season-level player endpoint does not.
        """
        from espn_api.football import League

        found: dict[str, object] = {}
        ids = [os.environ.get(k) for k in ("ESPN_RCL_ID", "ESPN_DMWD_ID")]
        for league_id in [i for i in ids if i]:
            lg = League(league_id=int(league_id), year=self.season,
                        espn_s2=os.environ["ESPN_S2"], swid=os.environ["ESPN_SWID"])
            for box in lg.box_scores(self.week):
                for p in (box.home_lineup or []) + (box.away_lineup or []):
                    found.setdefault(p.name.lower(), p)
        for p in self._source.free_agents(size=500):
            found.setdefault(p.name.lower(), p)
        return found

    def _lookup(self, name: str):
        want = name.strip().lower()
        hit = self._stat_lines.get(want)
        if hit is not None:
            return hit
        # "Vikings" for a team defense, where ESPN says "Vikings D/ST".
        for key, player in self._stat_lines.items():
            if want in key:
                return player
        return None

    # --- roster ---------------------------------------------------------

    def _rows(self) -> list[dict]:
        path = CONFIG_DIR / f"{self.slug}_roster.csv"
        if not path.exists():
            raise FileNotFoundError(f"no hand-entered roster at {path}")
        with path.open(newline="") as fh:
            return [r for r in csv.DictReader(fh) if r.get("player")]

    def weekly_lineup(self, week: int | None = None) -> list[WeeklyPlayer]:
        wk = int(week or self.week)
        schedule = self._pro_schedule(wk)
        out = []
        for row in self._rows():
            pos = (row.get("pos") or "").upper()
            slot = (row.get("slot") or "BN").upper()
            source = self._lookup(row["player"])
            stats = getattr(source, "stats", {}).get(wk, {}) if source else {}
            breakdown = stats.get("projected_breakdown") or {}
            points = self.table.score(breakdown, position=pos) if breakdown else 0.0
            team = (row.get("team") or (getattr(source, "proTeam", "") or "")).upper()
            game = schedule.get(team)
            out.append(WeeklyPlayer(
                player_id=str(getattr(source, "playerId", "")) or row["player"],
                name=row["player"],
                team=team or None,
                pos=pos,
                slot=slot,
                eligible_slots=self.eligible_slots(pos) | {slot},
                status=(row.get("status") or "OK").strip().upper() or "OK",
                game=game,
                projected=round(points, 2),
                on_bye=game is None,
                played=False,
            ))
        return out

    def _pro_schedule(self, week: int) -> dict[str, ProGame]:
        """Reuses the ESPN adapter's schedule, which is league independent."""
        from ..config import get_league
        from .espn import EspnClient

        for slug in ("rcl", "dmwd"):
            try:
                return EspnClient(get_league(slug), season=self.season).pro_schedule(week)
            except Exception:
                continue
        return {}

    def my_roster(self) -> list[PlayerState]:
        return [PlayerState(player_id=p.player_id, name=p.name, team=p.team,
                            pos=p.pos, slot=p.slot, status=p.status,
                            platform_proj=p.projected)
                for p in self.weekly_lineup()]

    def matchup(self, week: int | None = None) -> Matchup:
        """No opponent data by hand, by choice: entering an opponent's whole
        lineup every week is more upkeep than the matchup line is worth. The
        week view and the optimizer do not need it."""
        wk = int(week or self.week)
        lineup = self.weekly_lineup(wk)
        mine = sum(p.projected for p in lineup if p.starting)
        return Matchup(week=wk, home_team=self.name, away_team="(no opponent entered)",
                       home_proj=mine, away_proj=0.0,
                       home_lineup=lineup, away_lineup=[], mine="home")

    def injuries(self) -> list[PlayerState]:
        return [s for s in self.my_roster() if s.status != "OK"]

    def free_agents(self, position: str | None = None, limit: int = 10) -> list[PlayerState]:
        raise NotImplementedError("no free agent pool without the Yahoo API")

    def ping(self) -> str:
        rows = self._rows()
        starters = sum(1 for r in rows if (r.get("slot") or "").upper() not in BENCH)
        return (f"{self.name} (hand-entered, {len(rows)} players, {starters} starters, "
                f"{self.team_count()} teams, {len(self.table.rules)} scoring rules)")
