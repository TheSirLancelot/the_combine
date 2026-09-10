"""Which season a role line describes, and which snaps are in it.

Both of these were wrong and neither one crashed. PFF's season-level totals
silently fold in preseason and playoff snaps -- Drake Maye's 2025 total is 23
games and 770 dropbacks against a regular season of 17 and 601, and his passing
grade reads 75.2 instead of 87.8. And the moment PFF's default week hit 1 the
tool switched to a season nobody had played yet, so most starters showed no
role at all and the few who did were showing preseason.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.providers.pff_api import REGULAR_WEEKS, SeasonState
from combine.pipeline.usage import SMALL_SAMPLE, Usage, load


class FakeApi:
    """Records every facet call so the tests can assert on what was asked."""

    def __init__(self, season=2026, week=1, rows=None):
        self.state = SeasonState(season=season, week=week)
        self.rows = rows or {}
        self.calls = []

    def season_state(self):
        return self.state

    def regular_weeks(self, season):
        from combine.pipeline.providers.pff_api import PffApi

        return PffApi.regular_weeks(self, season)

    def facet(self, area, report="summary", season=None, week=None, ttl=None):
        self.calls.append((area, season, week))
        return self.rows.get((area, season), [])


def row(pid, games, **kw):
    return {"player_id": pid, "player": f"p{pid}", "player_game_count": games, **kw}


def test_a_bare_season_total_is_never_requested():
    """The whole bug in one assertion. A season total includes preseason and
    playoff snaps, so every request has to name its weeks."""
    api = FakeApi(season=2026, week=4)
    load(api, areas=("passing",))
    assert api.calls, "it should have asked for something"
    assert all(week for _area, _season, week in api.calls)


def test_a_finished_season_asks_for_every_regular_week():
    api = FakeApi(season=2026, week=4)
    load(api, areas=("passing",))
    prior = [week for _a, season, week in api.calls if season == 2025]
    assert prior == [",".join(str(w) for w in range(1, REGULAR_WEEKS + 1))]


def test_the_season_in_progress_asks_only_for_weeks_that_happened():
    """Asking for week 12 in week 4 is not harmless: it is how preseason rows
    got in in the first place."""
    api = FakeApi(season=2026, week=4)
    load(api, areas=("passing",))
    current = [week for _a, season, week in api.calls if season == 2026]
    assert current == ["1,2,3,4"]


def test_preseason_reads_last_season_and_does_not_ask_for_this_one():
    api = FakeApi(season=2026, week=0)
    load(api, areas=("passing",))
    assert {season for _a, season, _w in api.calls} == {2025}


def test_a_thin_current_season_keeps_last_season():
    """Week 2 rates built on two games are noise. Last season is the better
    prior until this season has enough games to stop warning about it."""
    api = FakeApi(season=2026, week=2, rows={
        ("passing", 2025): [row(1, 17, dropbacks=600)],
        ("passing", 2026): [row(1, 2, dropbacks=70)],
    })
    out = load(api, areas=("passing",))
    assert out[1].season == 2025
    assert out[1].games == 17


def test_enough_games_switches_to_this_season():
    api = FakeApi(season=2026, week=10, rows={
        ("passing", 2025): [row(1, 17, dropbacks=600)],
        ("passing", 2026): [row(1, SMALL_SAMPLE, dropbacks=300)],
    })
    out = load(api, areas=("passing",))
    assert out[1].season == 2026


def test_a_rookie_with_no_prior_still_gets_what_there_is():
    """Nothing to fall back to. One real regular-season game beats a blank."""
    api = FakeApi(season=2026, week=2, rows={
        ("passing", 2025): [],
        ("passing", 2026): [row(99, 1, dropbacks=30)],
    })
    out = load(api, areas=("passing",))
    assert out[99].season == 2026 and out[99].games == 1


def test_every_role_line_says_which_season_it_is():
    """The table mixes seasons by design, so an untagged row leaves you
    guessing which year you are reading."""
    u = Usage(pff_id=1, name="p", season=2025,
              rows={"passing": row(1, 17, dropbacks=600, ypa=7.6)})
    line = u.line("QB")
    assert line.startswith("2025 17g")
    assert "db/g 35.3" in line


def test_the_prior_caveat_follows_the_season_not_the_calendar():
    """In week 3 the calendar is in season and the row is still last year's.
    Keying the caveat off the calendar made it stay silent exactly then."""
    old = Usage(pff_id=1, name="p", season=2025, rows={"passing": row(1, 17)})
    assert any("prior" in c for c in old.caveats(in_season=True, season=2026))
    now = Usage(pff_id=1, name="p", season=2026, rows={"passing": row(1, 17)})
    assert not any("prior" in c for c in now.caveats(in_season=True, season=2026))
