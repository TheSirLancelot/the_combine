"""ESPN -> PFF player_id resolution.

No network: the API is stubbed. What is being tested is the conservatism, not
the plumbing. A wrong id silently attaches another player's usage to the one
you are deciding to start, which is the failure mode worth guarding.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.crosswalk import PffPlayer, load_ids, resolve_by_lookup, resolve_ids, save_ids
from combine.platforms import PlayerState


def espn(pid, name, pos="WR", team="KC") -> PlayerState:
    return PlayerState(player_id=pid, name=name, team=team, pos=pos)


def pff(pid, name, pos="WR", team="KC") -> PffPlayer:
    return PffPlayer(name=name, team=team, pos=pos, pff_id=pid)


class StubApi:
    """Stands in for /v1/players, which is a substring search."""

    def __init__(self, players):
        self._players = players
        self.queries = []

    def players(self, name):
        self.queries.append(name)
        want = name.lower()
        return [p for p in self._players
                if want in f"{p['first_name']} {p['last_name']}".lower()
                or want in p["last_name"].lower()]


def api_row(pid, first, last, pos="WR", team="KC"):
    return {"id": pid, "first_name": first, "last_name": last,
            "position": pos, "team": {"abbreviation": team}}


def test_exact_name_resolves():
    rows, misses = resolve_ids([espn("1", "Rashee Rice")], [pff(500, "Rashee Rice")])
    assert misses == []
    assert rows[0]["pff_id"] == 500 and rows[0]["how"] == "exact"


def test_absent_player_is_reported_not_guessed():
    rows, misses = resolve_ids([espn("1", "Some Rookie")], [pff(500, "Rashee Rice")])
    assert rows == []
    assert misses[0]["espn_name"] == "Some Rookie"
    assert misses[0]["espn_id"] == "1"


def test_same_espn_player_is_resolved_once():
    """The pool and the roster overlap, and a duplicate row would double-count
    the match rate."""
    dupe = [espn("1", "Rashee Rice"), espn("1", "Rashee Rice")]
    rows, _ = resolve_ids(dupe, [pff(500, "Rashee Rice")])
    assert len(rows) == 1


def test_team_and_position_break_a_duplicate_name():
    directory = [pff(1, "Josh Allen", pos="QB", team="BUF"),
                 pff(2, "Josh Allen", pos="ED", team="ARI")]
    rows, _ = resolve_ids([espn("9", "Josh Allen", pos="QB", team="BUF")], directory)
    assert rows[0]["pff_id"] == 1


def test_lookup_pass_finds_a_player_the_directory_never_charted():
    """Rookies have an id but no snaps last season, so they are absent from the
    facet directory and only the name lookup can find them."""
    api = StubApi([api_row(900, "Some", "Rookie")])
    misses = [{"espn_id": "7", "espn_name": "Some Rookie", "espn_pos": "WR",
               "espn_team": "KC", "reason": "unmatched", "pff_name": ""}]
    found, still = resolve_by_lookup(api, misses)
    assert still == []
    assert found[0]["pff_id"] == 900 and found[0]["how"] == "lookup"


def test_lookup_pass_refuses_a_substring_hit():
    """'Josh Allen' also returns Josh Hines-Allen. Different player."""
    api = StubApi([api_row(901, "Josh", "Hines-Allen", pos="ED", team="JAX")])
    misses = [{"espn_id": "7", "espn_name": "Josh Allen", "espn_pos": "QB",
               "espn_team": "BUF", "reason": "unmatched", "pff_name": ""}]
    found, still = resolve_by_lookup(api, misses)
    assert found == [] and len(still) == 1


def test_lookup_surname_pass_handles_a_nickname_the_query_cannot_reach():
    """ESPN says Riq Woolen, PFF says Tariq Woolen. The nickname does not
    substring-match, so the surname query plus team and position is the only
    safe route."""
    api = StubApi([api_row(902, "Tariq", "Woolen", pos="CB", team="PHI")])
    misses = [{"espn_id": "7", "espn_name": "Riq Woolen", "espn_pos": "CB",
               "espn_team": "PHI", "reason": "unmatched", "pff_name": ""}]
    found, still = resolve_by_lookup(api, misses)
    assert still == []
    assert found[0]["pff_id"] == 902 and found[0]["how"] == "lookup-surname"


def test_lookup_surname_pass_will_not_cross_teams():
    api = StubApi([api_row(902, "Tariq", "Woolen", pos="CB", team="SEA")])
    misses = [{"espn_id": "7", "espn_name": "Riq Woolen", "espn_pos": "CB",
               "espn_team": "PHI", "reason": "unmatched", "pff_name": ""}]
    found, _ = resolve_by_lookup(api, misses)
    assert found == []


def test_save_merges_across_leagues(tmp_path):
    """ESPN ids are global, both leagues share one file, and resolving one
    must not drop what the other found."""
    path = tmp_path / "ids.csv"
    save_ids([{"espn_id": "1", "espn_name": "A", "espn_pos": "WR", "espn_team": "KC",
               "pff_id": 10, "pff_name": "A", "pff_pos": "WR", "pff_team": "KC",
               "how": "exact"}], path)
    save_ids([{"espn_id": "2", "espn_name": "B", "espn_pos": "RB", "espn_team": "SF",
               "pff_id": 20, "pff_name": "B", "pff_pos": "HB", "pff_team": "SF",
               "how": "exact"}], path)
    assert load_ids(path) == {"1": 10, "2": 20}


def test_a_fresh_row_replaces_a_stored_one(tmp_path):
    path = tmp_path / "ids.csv"
    base = {"espn_id": "1", "espn_name": "A", "espn_pos": "WR", "espn_team": "KC",
            "pff_id": 10, "pff_name": "A", "pff_pos": "WR", "pff_team": "KC",
            "how": "fuzzy"}
    save_ids([base], path)
    save_ids([{**base, "pff_id": 11, "how": "exact"}], path)
    assert load_ids(path) == {"1": 11}


def test_load_ids_is_empty_before_the_first_build(tmp_path):
    assert load_ids(tmp_path / "nope.csv") == {}
