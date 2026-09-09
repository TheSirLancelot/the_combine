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
  * Preseason: team.roster is [] and box_scores() raises KeyError. From week 1
    box_scores() works and is the only place weekly projections live.
"""

from __future__ import annotations

import os

from ..config import SEASON, LeagueConfig
from . import Matchup, PlayerState, ProGame, WeeklyPlayer

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
        self._schedule_cache: dict[int, dict[str, ProGame]] = {}

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

    def team_count(self) -> int:
        return len(self.league.teams)

    def roster_size(self) -> int:
        """Startable slots plus bench. IR does not get drafted."""
        counts = self.league.settings.position_slot_counts
        return sum(v for k, v in counts.items() if v and k != "IR")

    def draft_picks(self) -> tuple[set[str], set[str]]:
        """(all drafted player ids, the ones I drafted) during a LIVE draft.

        espn-api's league.draft returns nothing until the draft is marked
        complete, and rosters stay empty the whole time, so mid-draft both look
        like nothing has happened. The raw mDraftDetail view does have it:
        ESPN pre-creates every pick slot and fills playerId in as picks are
        made, so an unmade pick is playerId -1. Verified live 2026-09-06.

        Only lm-api-reads answers with our cookies; fantasy.espn.com 403s.
        """
        import requests

        url = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
               f"{SEASON}/segments/0/leagues/{self.cfg.league_id}")
        r = requests.get(url, params={"view": "mDraftDetail"},
                         cookies={"espn_s2": os.environ["ESPN_S2"],
                                  "SWID": os.environ["ESPN_SWID"]}, timeout=15)
        r.raise_for_status()
        picks = (r.json().get("draftDetail") or {}).get("picks") or []
        taken, mine = set(), set()
        for p in picks:
            pid = p.get("playerId", -1)
            if pid is None or pid < 0:
                continue
            taken.add(str(pid))
            if str(p.get("teamId")) == str(self.cfg.team_id):
                mine.add(str(pid))
        return taken, mine

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
        useful tool before a draft. During a draft it shrinks as players are
        rostered, so this is the live view of who is actually left.

        Oversample a little then trim, because ESPN orders by its own ranking
        rather than by projection and we re-sort. Headroom is additive, not a
        multiplier: callers now ask for 250, and a 5x multiplier turned that
        into a 1250-player request several times a minute during a draft."""
        size = min(max(limit + 100, 100), 500)
        pool = self.league.free_agents(size=size, position=position)
        pool.sort(key=lambda p: getattr(p, "projected_total_points", 0) or 0, reverse=True)
        return [self._state(p, slot="FA") for p in pool[:limit]]

    def injuries(self) -> list[PlayerState]:
        return [s for s in self.my_roster() if s.status != "OK"]

    def player(self, name: str):
        return self.league.player_info(name=name)

    # --- weekly ---------------------------------------------------------

    def pro_schedule(self, week: int | None = None) -> dict[str, ProGame]:
        """{NFL team abbrev: that team's game this week}. Missing key = bye.

        The box score does NOT carry this. It reports opponent pro-team id 0,
        which espn-api renders as the string "None", which is what made the
        first cut of the weekly view opponent-blind. The real schedule lives on
        a separate season-level view, proTeamSchedules_wl, one request for all
        32 teams and every week. Probed 2026-09-09.

        Cached per client instance: the schedule for a given week does not
        change, and the weekly view asks for it once per player otherwise.
        """
        wk = int(week or self.week)
        if wk in self._schedule_cache:
            return self._schedule_cache[wk]

        from espn_api.football.constant import PRO_TEAM_MAP

        data = self.league.espn_request.get_pro_schedule()
        out: dict[str, ProGame] = {}
        for team in data.get("settings", {}).get("proTeams", []) or []:
            tid = team.get("id")
            if not tid:  # id 0 is ESPN's placeholder team
                continue
            games = (team.get("proGamesByScoringPeriod") or {}).get(str(wk)) or []
            if not games:
                continue  # bye
            g = games[0]
            home = g.get("homeProTeamId") == tid
            opp_id = g.get("awayProTeamId") if home else g.get("homeProTeamId")
            abbrev = PRO_TEAM_MAP.get(tid)
            opp = PRO_TEAM_MAP.get(opp_id)
            if not abbrev or not opp:
                continue
            out[abbrev] = ProGame(opponent=opp, home=home,
                                  kickoff_ms=int(g.get("date") or 0))
        self._schedule_cache[wk] = out
        return out

    def _weekly(self, p, schedule: dict[str, ProGame]) -> WeeklyPlayer:
        """One box-score player. Field notes from the live probe on 2026-09-09:
          * projected_points and points exist HERE and are None on the
            season-level roster object, which is why the weekly path reads box
            scores rather than team.roster.
          * pro_opponent is the string "None" because ESPN sends opponent id 0
            here. Opponent comes from pro_schedule() instead.
          * game_played is 0 before kickoff and 100 when final.
        A player whose NFL team has no game this week is on bye, which is a
        stronger signal than ESPN's own on_bye flag because it is derived from
        the schedule rather than reported.
        """
        game = schedule.get((getattr(p, "proTeam", None) or "").upper())
        return WeeklyPlayer(
            player_id=str(getattr(p, "playerId", "")),
            name=getattr(p, "name", "?"),
            team=getattr(p, "proTeam", None),
            pos=getattr(p, "position", "?"),
            slot=getattr(p, "slot_position", None) or "BE",
            eligible_slots=frozenset(getattr(p, "eligibleSlots", ()) or ()),
            status=_status(p),
            game=game,
            projected=float(getattr(p, "projected_points", 0.0) or 0.0),
            actual=float(getattr(p, "points", 0.0) or 0.0),
            played=bool(getattr(p, "game_played", 0)),
            on_bye=game is None or bool(getattr(p, "on_bye", False)),
        )

    def matchup(self, week: int | None = None) -> Matchup:
        """My box score for one week, both lineups.

        box_scores() 404s in preseason and worked from week 1 onward, verified
        live 2026-09-09. A bye-week matchup hands back an int 0 in place of a
        team object, hence the getattr guards.
        """
        wk = int(week or self.week)
        schedule = self.pro_schedule(wk)
        for b in self.league.box_scores(wk):
            for side in ("home", "away"):
                team = getattr(b, f"{side}_team", None)
                if team is None or str(getattr(team, "team_id", "")) != str(self.cfg.team_id):
                    continue
                other = getattr(b, "away_team" if side == "home" else "home_team", None)
                return Matchup(
                    week=wk,
                    home_team=getattr(b.home_team, "team_name", "?")
                    if side == "home" else getattr(other, "team_name", "BYE"),
                    away_team=getattr(other, "team_name", "BYE")
                    if side == "home" else getattr(b.away_team, "team_name", "?"),
                    home_proj=float(getattr(b, "home_projected", 0.0) or 0.0),
                    away_proj=float(getattr(b, "away_projected", 0.0) or 0.0),
                    home_lineup=[self._weekly(p, schedule) for p in (b.home_lineup or [])],
                    away_lineup=[self._weekly(p, schedule) for p in (b.away_lineup or [])],
                    home_score=float(getattr(b, "home_score", 0.0) or 0.0),
                    away_score=float(getattr(b, "away_score", 0.0) or 0.0),
                    mine=side,
                )
        raise LookupError(f"team_id {self.cfg.team_id} has no box score in week {wk}")

    def weekly_lineup(self, week: int | None = None) -> list[WeeklyPlayer]:
        """My full roster for one week, starters and bench, with weekly numbers."""
        return self.matchup(week).my_lineup
