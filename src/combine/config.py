"""Env loading and the league registry.

Every tool in the MCP layer takes a league slug, so this is the single place
that maps a slug you type in Claude to a platform and a league id.
Rename the slugs to whatever you actually call these leagues.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = REPO_ROOT / "config"
LOG_DIR = REPO_ROOT / "logs"
DB_PATH = DATA_DIR / "combine.db"
YAHOO_TOKEN_PATH = DATA_DIR / "yahoo_token.json"


class ConfigError(RuntimeError):
    pass


def env(key: str, default: str | None = None, *, required: bool = False) -> str | None:
    val = os.environ.get(key) or default
    if required and not val:
        raise ConfigError(f"missing required env var {key}")
    return val


SEASON = int(env("SEASON", "2026"))
BEARER_TOKEN = env("COMBINE_BEARER_TOKEN")
HOST = env("COMBINE_HOST", "127.0.0.1")
PORT = int(env("COMBINE_PORT", "8787"))
PATH = env("COMBINE_PATH", "/mcp")


@dataclass(frozen=True)
class LeagueConfig:
    slug: str
    name: str
    platform: str  # "espn" | "yahoo"
    league_id: str
    team_id: str
    draft_slot: int = 0  # <SLUG>_DRAFT_POS, 0 = unknown


# (slug, full name, platform, league-id env var, team-id env var)
# Slugs are what you type in Claude, so they stay short.
_SPECS = [
    ("rcl", "The REAL Champions League", "espn", "ESPN_RCL_ID", "ESPN_RCL_TEAM_ID"),
    ("dmwd", "Dont Mess With Dexas", "espn", "ESPN_DMWD_ID", "ESPN_DMWD_TEAM_ID"),
    ("work", "Work league", "yahoo", "YAHOO_LEAGUE_ID", "YAHOO_TEAM_KEY"),
]


def leagues() -> dict[str, LeagueConfig]:
    """Configured leagues only. A league with missing env is skipped, not fatal,
    so one dead credential does not take the whole server down."""
    out: dict[str, LeagueConfig] = {}
    for slug, name, platform, id_key, team_key in _SPECS:
        league_id, team_id = os.environ.get(id_key), os.environ.get(team_key)
        if league_id and team_id:
            slot = os.environ.get(f"{slug.upper()}_DRAFT_POS", "")
            out[slug] = LeagueConfig(slug, name, platform, league_id, team_id,
                                     int(slot) if slot.strip().isdigit() else 0)
    return out


def get_league(slug: str) -> LeagueConfig:
    found = leagues().get(slug)
    if not found:
        known = ", ".join(leagues()) or "none configured"
        raise ConfigError(f"unknown league '{slug}'. configured: {known}")
    return found


def missing_env() -> list[str]:
    """Env vars referenced by _SPECS or required for auth that are unset."""
    keys = ["COMBINE_BEARER_TOKEN"]
    for _, _name, platform, id_key, team_key in _SPECS:
        keys += [id_key, team_key]
        keys += ["ESPN_S2", "ESPN_SWID"] if platform == "espn" else [
            "YAHOO_CONSUMER_KEY",
            "YAHOO_CONSUMER_SECRET",
        ]
    return sorted({k for k in keys if not os.environ.get(k)})


def platform_ready(platform: str) -> bool:
    """Whether the shared credentials for a platform are present at all.
    Lets doctor distinguish 'not set up yet' from 'broken'."""
    need = ("ESPN_S2", "ESPN_SWID") if platform == "espn" else (
        "YAHOO_CONSUMER_KEY", "YAHOO_CONSUMER_SECRET")
    return all(os.environ.get(k) for k in need)
