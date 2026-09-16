"""The hand-built front end.

These are route and template tests, not pipeline tests: the numbers are already
covered where they are computed, and re-asserting them here would only mean two
places to update when one of them changes.

What this file is actually for is the things a template can get wrong quietly.
A page that renders with an exception swallowed, a partial that returns a whole
document, a gate that lets a request through because the middleware order moved.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from starlette.testclient import TestClient

from combine.web import app as web
from combine.web import cache, data

PAGES = ("/week", "/waivers", "/trades", "/scores", "/card")


@pytest.fixture
def client(monkeypatch):
    """Every page, with the pipeline stubbed. No network, no ESPN."""
    monkeypatch.delenv("COMBINE_ACCESS_TEAM", raising=False)
    monkeypatch.delenv("COMBINE_ACCESS_AUD", raising=False)
    monkeypatch.setattr(data, "leagues", lambda: [
        {"slug": "rcl", "name": "The REAL Champions League", "platform": "espn"},
        {"slug": "work", "name": "League of Degenerates", "platform": "manual"}])
    monkeypatch.setattr(data, "espn_leagues",
                        lambda: [x for x in data.leagues()
                                 if x["platform"] == "espn"])
    monkeypatch.setattr(data, "week", lambda league, wk=None: WEEK)
    monkeypatch.setattr(data, "waivers", lambda league, wk=None: WIRE)
    monkeypatch.setattr(data, "scores", lambda wk=None: SCORES)
    monkeypatch.setattr(data, "scorecard", lambda wk=None: CARD)
    monkeypatch.setattr(data, "pickers", lambda league, wk=None: PICKERS)
    return TestClient(web.app)


PLAYER = {"id": "1", "name": "Jahmyr Gibbs", "pos": "RB", "slot": "RB",
          "team": "DET", "opp": "vs CHI", "proj": 18.4, "actual": 0.0,
          "role": "2025 17g  touch/g 15.9", "floor": 8.0, "ceiling": 30.1,
          "boom": 41, "bust": 19, "status": "", "locked": False, "bye": False}
WEEK = {"week": 2, "me": "Mine", "them": "Theirs", "my_proj": 149.8,
        "their_proj": 142.7, "my_score": 0.0, "their_score": 0.0,
        "my_odds": 0.54, "their_odds": 0.46,
        "starters": [PLAYER], "bench": [{**PLAYER, "slot": "BE"}],
        "problems": [], "calls": [], "near": [],
        "optimal": {"in": [], "out": [], "gain": 0.0},
        "pff_err": "", "in_season": True, "at": "09:00 PDT"}
WIRE = {"candidates": [{"name": "A Free Agent", "pos": "WR", "team": "KC",
                        "proj": 9.1, "week": 1.6, "season": -12.0,
                        "drop": "Somebody", "note": "because", "free": False,
                        "stash": False, "corrected": False}],
        "notes": "", "stash": "", "at": "09:00 PDT",
        "depth": [{"name": "My Guy", "pos": "CB", "season": 139,
                   "best": "Free Guy", "theirs": 172, "gain": 32,
                   "note": "because"}]}
CELL = {"name": "Brock Purdy", "pos": "QB", "team": "SF", "opponent": "vs MIA",
        "kickoff": "Sun 1:25 PM", "status": "", "line": "", "points": 0.0,
        "projected": 18.2, "played": False, "locked": False}
THEIRS = {**CELL, "name": "Jalen Hurts", "team": "PHI", "opponent": "@ TEN",
          "line": "18/25, 220 yd, 2 TD", "projected": 24.0, "points": 21.4,
          "played": True}
SCORES = {"games": [{"league": "RCL", "slug": "rcl", "week": 2, "me": "Mine",
                     "them": "Theirs",
                     "my_score": 88.2, "their_score": 79.4, "my_proj": 120.0,
                     "their_proj": 118.0, "yet_to_play": 3,
                     "their_yet_to_play": 2, "margin": 8.8,
                     "my_odds": 0.61, "their_odds": 0.39,
                     "projected_margin": 2.0, "final": False, "started": True,
                     "starters": [{"slot": "QB", "mine": CELL,
                                   "theirs": THEIRS, "lead": -21.4}],
                     "bench": [{"slot": "BE", "mine": CELL, "theirs": None,
                                "lead": 0.0}]}],
          "missing": [{"league": "Work", "why": "hand-entered"}],
          "at": "13:20 PDT"}
CARD = {"rows": [], "summary": [], "at": "09:00 PDT", "error": ""}
PICKERS = {"mine": [{"name": "Mine", "label": "Mine · RB"}],
           "theirs": [{"name": "Theirs", "label": "Theirs · WR · Them"}],
           "wire": [{"name": "Free", "label": "Free · QB"}],
           "teams": ["Them"]}


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders(client, path):
    r = client.get(path + "?league=rcl")
    assert r.status_code == 200, r.text[:400]
    assert "<!doctype html>" in r.text.lower()
    assert "</html>" in r.text


@pytest.mark.parametrize("path", PAGES)
def test_a_partial_is_a_fragment_and_not_a_document(client, path):
    """The nav swaps this into <main>. A whole document would nest a second
    <html> inside the page, which browsers paper over and nobody notices until
    something subtle breaks."""
    r = client.get(path + "?league=rcl", headers={"X-Partial": "1"})
    assert r.status_code == 200
    assert "<!doctype" not in r.text.lower()
    assert "<html" not in r.text.lower()
    assert "<nav" not in r.text.lower()


def test_the_root_goes_to_the_week(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/week" in r.headers["location"]


def test_an_unknown_league_falls_back_rather_than_failing(client):
    """A stale bookmark or a league removed from .env should not be a 500."""
    assert client.get("/week?league=nonsense").status_code == 200


def test_a_league_without_the_api_says_so_instead_of_breaking(client):
    r = client.get("/week?league=work")
    assert r.status_code == 200
    assert "League of Degenerates" in r.text


def test_the_page_offers_every_league_and_marks_the_current_one(client):
    r = client.get("/week?league=rcl")
    assert 'data-league="rcl"' in r.text and 'data-league="work"' in r.text
    assert r.text.count('aria-current="true"') == 1


def test_healthz_does_not_need_a_league(client):
    assert client.get("/healthz").json()["ok"] is True


# --- the gate ---------------------------------------------------------------

def test_a_request_without_access_is_refused_before_any_handler(monkeypatch):
    """The middleware has to run first. If it moved below routing, a page
    would build its data (and hit ESPN) and only then be thrown away."""
    monkeypatch.setenv("COMBINE_ACCESS_TEAM", "combine.cloudflareaccess.com")
    monkeypatch.setenv("COMBINE_ACCESS_AUD", "aud")

    def boom(*a, **k):
        raise AssertionError("the handler ran for an unauthenticated request")

    monkeypatch.setattr(data, "week", boom)
    r = TestClient(web.app).get("/week?league=rcl")
    assert r.status_code == 403
    assert "Not signed in" in r.text


def test_static_files_are_served_without_a_token(monkeypatch):
    """A refusal page that cannot load its own stylesheet is a worse
    experience for no gain: a stylesheet is not a roster."""
    monkeypatch.setenv("COMBINE_ACCESS_TEAM", "combine.cloudflareaccess.com")
    monkeypatch.setenv("COMBINE_ACCESS_AUD", "aud")
    r = TestClient(web.app).get("/static/app.css")
    assert r.status_code == 200
    assert "--accent" in r.text


# --- the cache --------------------------------------------------------------

def test_the_cache_builds_once_inside_the_window():
    calls = []
    cache.forget()
    for _ in range(3):
        cache.memo(("k",), 60, lambda: calls.append(1))
    assert len(calls) == 1


def test_forgetting_makes_it_build_again():
    calls = []
    cache.forget()
    cache.memo(("k2",), 60, lambda: calls.append(1))
    cache.forget()
    cache.memo(("k2",), 60, lambda: calls.append(1))
    assert len(calls) == 2


def test_a_zero_ttl_never_serves_a_stale_answer():
    calls = []
    cache.forget()
    for _ in range(2):
        cache.memo(("k3",), 0, lambda: calls.append(1))
    assert len(calls) == 2


def test_the_depth_row_leads_with_the_man_you_would_add(client):
    """It read backwards: your own player in the big name next to a big green
    +32 says "get this guy" about somebody you already own. The section above
    it puts the pickup in the big name, and this has to match."""
    body = client.get("/waivers?league=rcl").text
    mine = body.index("My Guy")
    theirs = body.index("Free Guy")
    assert theirs < mine, "the free agent comes first"
    assert '<div class="name">Free Guy' in body
    assert "over your My Guy" in body


def test_the_wire_row_also_leads_with_the_man_you_would_add(client):
    """The same rule one section up, so the two cannot drift apart again."""
    body = client.get("/waivers?league=rcl").text
    assert '<div class="name">A Free Agent' in body
    assert "drop Somebody" in body


def test_every_tab_carries_the_title_its_page_will_show(client):
    """The loading outline is titled from this. Without it the first thing you
    see after a tap is a grey block, which is what made the wire feel dead."""
    body = client.get("/week?league=rcl").text
    for title in ("This week", "The wire", "Trades", "Scores", "Record"):
        assert f'data-title="{title}"' in body


def test_the_trade_pickers_are_real_selects_in_the_markup(client):
    """The search box is an enhancement layered over these, not a replacement.
    The select stays in the DOM holding the value, so the form posts exactly
    what it posted before and a browser with the script blocked still works."""
    body = client.get("/trades?league=rcl").text
    assert body.count("<select") >= 5
    assert 'name="give" multiple' in body
    assert 'name="get" multiple' in body
    assert 'name="fill" multiple' in body
    assert "<option value=\"Mine\">" in body


def finish(client, response, tries: int = 200):
    """Follow a ticket to its answer.

    The trade tools hand back a job rather than a result, so a test that wants
    the result has to walk the same path the browser does.
    """
    import re
    import time

    body = response.text
    for _ in range(tries):
        found = re.search(r'data-job="([^"]+)"', body)
        if not found:
            return body
        time.sleep(0.02)
        body = client.get("/jobs/" + found.group(1)).text
    raise AssertionError("job never finished")


def test_the_grade_endpoint_takes_what_the_form_posts(client, monkeypatch):
    """Whatever the picker does on screen, the server contract is a plain form
    post of the same names."""
    seen = {}

    def fake(league, give, get, fill=None):
        seen.update(league=league, give=give, get=get, fill=fill)
        return None, "stubbed"

    monkeypatch.setattr(data, "grade", fake)
    r = client.post("/trades/grade", data={
        "league": "rcl", "give": ["A", "B"], "get": ["C"], "fill": ["D"]})
    assert r.status_code == 200
    finish(client, r)
    assert seen == {"league": "rcl", "give": ["A", "B"], "get": ["C"],
                    "fill": ["D"]}


def test_the_static_urls_carry_a_fingerprint(client):
    """A phone that cached app.js will keep running it against markup it no
    longer matches, and the symptom is a feature that works everywhere except
    on the device in your hand. Stamping the URL makes the stale copy
    unreachable rather than unlikely."""
    from combine.web.cache import asset_version

    body = client.get("/week?league=rcl").text
    stamp = asset_version()
    assert f"/static/app.js?v={stamp}" in body
    assert f"/static/app.css?v={stamp}" in body


def test_the_fingerprint_moves_when_a_static_file_does(tmp_path, monkeypatch):
    """Otherwise it is decoration. `code_version` walks the Python only, which
    is why it could not do this job."""
    from combine.web import cache

    before = cache.asset_version()
    js = Path(cache.__file__).resolve().parent / "static" / "app.js"
    stamp = js.stat()
    try:
        os.utime(js, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
        assert cache.asset_version() != before
    finally:
        os.utime(js, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert cache.asset_version() == before


def test_the_scoreboard_opens_into_a_head_to_head(client):
    """The summary is the score, and the detail behind it is the two lineups
    facing each other slot by slot, which is the only layout that answers the
    question a live scoreboard is actually asked: who is beating me where."""
    body = client.get("/scores").text
    assert '<details class="game" data-game="rcl"' in body
    assert "Brock Purdy" in body and "Jalen Hurts" in body
    assert '<div class="slot">QB</div>' in body
    assert "Bench" in body


def test_a_player_who_has_not_kicked_off_shows_no_score(client):
    """A zero and a not-yet are different facts and a scoreboard that prints
    them the same way is lying. Purdy is 0.0 and unplayed; Hurts has 21.4."""
    body = client.get("/scores").text
    assert "Sun 1:25 PM" in body        # the kickoff stands in for the score
    assert "21.4" in body
    assert "proj 24.0" in body          # his is behind him, next to the actual
    assert "proj 18.2" not in body      # Purdy's waits until he plays


def test_the_empty_side_of_a_row_is_still_a_row(client):
    """His bench being shorter than mine is information. Dropping the row would
    silently re-pair everything below it against the wrong man."""
    body = client.get("/scores").text
    assert "Empty" in body


def test_the_scores_page_carries_an_auto_refresh_switch(client):
    """The script finds it by id and does nothing at all when it is absent, so
    this is the whole contract between the two."""
    assert 'id="autorefresh"' in client.get("/scores").text
    assert 'id="autorefresh"' not in client.get("/week?league=rcl").text


def test_espn_win_odds_show_on_both_views(client):
    """Both, because both answer 'how am I doing' and a number that appears on
    one of them is a number you have to go looking for."""
    assert "61%" in client.get("/scores").text
    assert "54%" in client.get("/week?league=rcl").text


def test_the_odds_are_labelled_as_espn_s(client):
    """We have odds of our own and these are not them. An unattributed bar on a
    page full of our own numbers reads as one of ours."""
    assert "ESPN's chance to win" in client.get("/scores").text


def test_no_odds_means_no_bar_rather_than_an_even_one(client, monkeypatch):
    """ESPN publishes a chance to win only for the week in play. An empty bar
    would say 50/50, which is an answer; saying nothing is the truth."""
    blank = {**SCORES, "games": [{**SCORES["games"][0],
                                  "my_odds": None, "their_odds": None}]}
    monkeypatch.setattr(data, "scores", lambda wk=None: blank)
    body = client.get("/scores").text
    assert 'class="odds"' not in body
    assert "88.2" in body          # the rest of the card is untouched


def test_a_player_who_has_played_shows_what_he_did(client):
    body = client.get("/scores").text
    assert "18/25, 220 yd, 2 TD" in body


def test_a_slow_search_answers_immediately_with_a_ticket(client, monkeypatch):
    """The 524 this exists to prevent: Cloudflare gives the origin 100 seconds.
    The POST has to come back in one, however long the search runs."""
    import threading
    import time

    gate = threading.Event()

    def slow(league):
        gate.wait(5)
        return []

    monkeypatch.setattr(data, "find_trades", slow)
    began = time.time()
    r = client.post("/trades/find", data={"league": "rcl"})
    assert time.time() - began < 1.0
    assert r.status_code == 200
    assert "data-job=" in r.text
    gate.set()


def test_the_page_stays_answerable_while_a_search_runs(client, monkeypatch):
    """The quieter half of the same bug. These were `async def` around blocking
    calls, so a search held the event loop and every other request queued
    behind it — the app looked dead to anyone who touched it mid-search."""
    import threading
    import time

    gate = threading.Event()
    monkeypatch.setattr(data, "find_trades", lambda league: gate.wait(5) or [])
    client.post("/trades/find", data={"league": "rcl"})

    began = time.time()
    assert client.get("/scores").status_code == 200
    assert time.time() - began < 1.0
    gate.set()


def test_polling_a_ticket_gives_the_answer_once_it_lands(client, monkeypatch):
    monkeypatch.setattr(data, "find_trades", lambda league: [])
    body = finish(client, client.post("/trades/find", data={"league": "rcl"}))
    assert "data-job=" not in body


def test_a_ticket_that_has_been_swept_says_so_rather_than_hanging(client):
    body = client.get("/jobs/neverwasajob").text
    assert "no longer around" in body
    assert "data-job=" not in body          # nothing left to poll


def test_a_search_that_fails_reaches_the_person(client, monkeypatch):
    def boom(league):
        raise RuntimeError("ESPN timed out")

    monkeypatch.setattr(data, "find_trades", boom)
    body = finish(client, client.post("/trades/find", data={"league": "rcl"}))
    assert "ESPN timed out" in body
    assert "data-job=" not in body


class FakePlayer:
    def __init__(self, name, pos="RB", team="SF", projected=12.0):
        self.name, self.pos, self.team, self.projected = name, pos, team, projected


class FakeMove:
    def __init__(self, name, pos, value, joining):
        self.name, self.pos, self.value, self.joining = name, pos, value, joining


class FakeDeal:
    give = FakePlayer("Demario Davis", "LB", "NYJ")
    get = FakePlayer("Malik Nabers", "WR", "NYG")
    partner = "Super Lamario 64"
    my_season, their_season, my_week, their_week = 36.0, 8.0, 1.2, 0.7
    partner_thin, partner_deep, bar = "LB", "QB", 17.0
    odds_now, odds_after = 0.57, 0.58
    their_moves = (FakeMove("Josh Jacobs", "RB", 159.0, True),
                   FakeMove("Josiah Trotter", "LB", 117.0, False))
    ask = "stretch"

    def why_they_might(self):
        return "they start Josh Jacobs 159; out comes Josiah Trotter 117"


def test_a_deal_opens_into_its_reasoning(client, monkeypatch):
    """The line explaining the deal was being cut off mid-sentence, which is
    the worst of the three options: it reads as the whole answer while being
    half of one. It gets a panel instead."""
    monkeypatch.setattr(data, "find_trades", lambda league: [FakeDeal()])
    body = finish(client, client.post("/trades/find", data={"league": "rcl"}))
    assert '<details class="deal">' in body
    # the summary keeps what was on screen
    assert "Demario Davis → Malik Nabers" in body
    # and the panel carries the rest, whole
    assert "Josiah Trotter" in body and "117" in body
    assert "is thinnest at LB" in body


def test_the_badge_is_spelled_out_against_the_number_it_came_from(client,
                                                                 monkeypatch):
    """'STR' with a legend at the bottom of the page is a code to look up. The
    panel says what it means and which figure decided it."""
    monkeypatch.setattr(data, "find_trades", lambda league: [FakeDeal()])
    body = finish(client, client.post("/trades/find", data={"league": "rcl"}))
    assert "17 point band" in body
    assert "cannot\n          tell a gain from a loss" in body


def test_the_summary_no_longer_carries_the_truncated_reasoning(client,
                                                              monkeypatch):
    """It moved inside. Leaving it in both places is how a row ends up three
    lines tall and still cut off."""
    monkeypatch.setattr(data, "find_trades", lambda league: [FakeDeal()])
    body = finish(client, client.post("/trades/find", data={"league": "rcl"}))
    summary = body.split("</summary>")[0]
    assert "out comes" not in summary
