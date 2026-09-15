"""Teaching yahoofantasy a season it has never heard of.

The library carries a hardcoded season -> game id table that ends at 2025, so
the first authorised 2026 call fails with "2026 is not a valid season for nfl"
before it reaches Yahoo at all. That error looks like a broken token and is not
one, which is the reason this has its own tests and its own docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.platforms.yahoo import ensure_game


@pytest.fixture
def table(monkeypatch):
    """A copy of the library's table, so a test cannot poison the real one."""
    from yahoofantasy.api import games as games_mod

    monkeypatch.setitem(games_mod.games, "nfl", dict(games_mod.games["nfl"]))
    return games_mod.games["nfl"]


def test_a_known_season_does_not_ask_yahoo(table, monkeypatch):
    """2025 is in the shipped table. Asking anyway would be a wasted call on
    every single league fetch."""
    from yahoofantasy.api import games as games_mod

    def boom(*a, **k):
        raise AssertionError("should not have called Yahoo")

    monkeypatch.setattr(games_mod, "_find_game_id", boom)
    assert ensure_game(object(), 2025) == "461"


def test_an_unknown_season_is_resolved_and_remembered(table, monkeypatch):
    from yahoofantasy.api import games as games_mod

    calls = []

    def fake(game, season, ctx):
        calls.append((game, season))
        return "472"

    monkeypatch.setattr(games_mod, "_find_game_id", fake)
    assert ensure_game(object(), 2026) == "472"
    assert calls == [("nfl", 2026)]

    # Second time through it is in the table, so Yahoo is not asked again.
    assert ensure_game(object(), 2026) == "472"
    assert len(calls) == 1


def test_a_season_yahoo_has_no_answer_for_says_so(table, monkeypatch):
    """Before a season opens on Yahoo's side there is genuinely nothing to
    fetch, and that should not read as a broken token."""
    from yahoofantasy.api import games as games_mod

    monkeypatch.setattr(games_mod, "_find_game_id", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="did not return an NFL game id"):
        ensure_game(object(), 2031)


def test_the_resolved_id_is_what_the_library_will_look_up(table, monkeypatch):
    """Registering it under the wrong key would resolve fine here and still
    fail inside the library."""
    from yahoofantasy.api import games as games_mod
    from yahoofantasy.api.games import get_game_id

    monkeypatch.setattr(games_mod, "_find_game_id", lambda *a, **k: "472")
    ensure_game(object(), 2026)
    assert str(get_game_id("nfl", 2026)) == "472"
