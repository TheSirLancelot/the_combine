"""The hand-built front end.

These are route and template tests, not pipeline tests: the numbers are already
covered where they are computed, and re-asserting them here would only mean two
places to update when one of them changes.

What this file is actually for is the things a template can get wrong quietly.
A page that renders with an exception swallowed, a partial that returns a whole
document, a gate that lets a request through because the middleware order moved.
"""

from __future__ import annotations

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
SCORES = {"games": [{"league": "RCL", "week": 2, "me": "Mine", "them": "Theirs",
                     "my_score": 88.2, "their_score": 79.4, "my_proj": 120.0,
                     "their_proj": 118.0, "yet_to_play": 3,
                     "their_yet_to_play": 2, "margin": 8.8,
                     "projected_margin": 2.0, "final": False, "started": True}],
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
    assert seen == {"league": "rcl", "give": ["A", "B"], "get": ["C"],
                    "fill": ["D"]}
