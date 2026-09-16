"""The Starlette app. Routes in, HTML out.

Two things shape this file.

**Every page is a real page.** A URL renders server side and works with
JavaScript off. The navigation then upgrades itself: a tap fetches the same URL
with `X-Partial`, gets back just the main region, and swaps it in, so moving
between tabs on a phone costs one small response instead of a full reload. If
that fetch fails for any reason the link is still a link and the browser does
what browsers do.

**Authentication is the same gate the Streamlit app uses**, in middleware, so a
request that did not come through Cloudflare Access never reaches a handler and
never touches ESPN.
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from .. import access, config
from . import data
from .cache import asset_version, forget

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

# The sentences these views need are written once, in the pipeline, so the web
# app and Discord cannot drift into saying different things about one deal.
from ..pipeline.trades import claim_note, headline

templates.env.globals.update(claim_note=claim_note, headline=headline,
                             assets=asset_version)

# slug, tab label, icon path, and the heading its page will show. The last one
# is what lets the loading outline carry a real title instead of a grey block.
TABS = (
    ("week", "Week", "M3 4h18M3 10h18M3 16h18", "This week"),
    ("waivers", "Wire", "M12 3v18M5 10l7-7 7 7", "The wire"),
    ("trades", "Trades", "M7 7h14l-4-4M17 17H3l4 4", "Trades"),
    ("scores", "Scores", "M4 20V10M10 20V4M16 20v-8M22 20V7", "Scores"),
    ("card", "Record", "M4 6h16v12H4zM8 10h8M8 14h5", "Record"),
)


def _league(request) -> str:
    """The league in the query string, or the first configured one."""
    known = [x["slug"] for x in data.leagues()]
    asked = request.query_params.get("league", "")
    return asked if asked in known else (known[0] if known else "")


def _week(request) -> int:
    try:
        return max(0, int(request.query_params.get("week", 0)))
    except ValueError:
        return 0


def render(request, page: str, ctx: dict) -> HTMLResponse:
    """A whole page, or just the main region when the nav asked for one."""
    ctx = {
        "request": request, "page": page, "tabs": TABS,
        "leagues": data.leagues(), "espn": data.espn_leagues(),
        "league": _league(request), "week": _week(request),
        "who": getattr(request.state, "who", ""),
        **ctx,
    }
    name = f"pages/{page}.html"
    if request.headers.get("x-partial"):
        return templates.TemplateResponse(request, name, ctx)
    return templates.TemplateResponse(request, "base.html",
                                      {**ctx, "body": name})


async def home(request):
    return RedirectResponse(f"/week?league={_league(request)}")


async def week(request):
    league = _league(request)
    cfg = config.get_league(league)
    if cfg.platform != "espn":
        return render(request, "week", {"unavailable": cfg.name, "data": None})
    return render(request, "week",
                  {"data": data.week(league, _week(request) or None),
                   "unavailable": ""})


async def waivers(request):
    league = _league(request)
    cfg = config.get_league(league)
    if cfg.platform != "espn":
        return render(request, "waivers",
                      {"unavailable": cfg.name, "data": None})
    return render(request, "waivers",
                  {"data": data.waivers(league, _week(request) or None),
                   "unavailable": ""})


async def scores(request):
    return render(request, "scores", {"data": data.scores(_week(request) or None)})


async def card(request):
    return render(request, "card",
                  {"data": data.scorecard(_week(request) or None)})


async def trades(request):
    league = _league(request)
    cfg = config.get_league(league)
    if cfg.platform != "espn":
        return render(request, "trades", {"mine": [], "theirs": [], "wire": [],
                                          "teams": [], "unavailable": cfg.name})
    return render(request, "trades",
                  {**data.pickers(league, _week(request) or None),
                   "unavailable": ""})


def _many(form, name: str) -> list[str]:
    return [x for x in form.getlist(name) if x]


def fragment(request, name: str, ctx: dict) -> HTMLResponse:
    return templates.TemplateResponse(request, f"bits/{name}.html",
                                      {"request": request, **ctx})


async def trade_grade(request):
    form = await request.form()
    verdict, err = data.grade(form.get("league", ""), _many(form, "give"),
                              _many(form, "get"), _many(form, "fill"))
    return fragment(request, "grade", {"v": verdict, "err": err})


async def trade_target(request):
    form = await request.form()
    items, err = data.target(form.get("league", ""), form.get("player", ""))
    return fragment(request, "target",
                    {"items": items, "err": err,
                     "who": form.get("player", "")})


async def trade_raid(request):
    form = await request.form()
    try:
        reach = float(form.get("reach", 2))
    except ValueError:
        reach = 2.0
    deals = data.raid(form.get("league", ""), form.get("team", ""), reach)
    return fragment(request, "deals",
                    {"deals": deals, "team": form.get("team", "")})


async def trade_find(request):
    form = await request.form()
    deals = data.find_trades(form.get("league", ""))
    return fragment(request, "deals", {"deals": deals, "team": ""})


async def refresh(request):
    forget()
    back = request.headers.get("referer") or "/week"
    return RedirectResponse(back, status_code=303)


async def health(request):
    return JSONResponse({"ok": True, "leagues": [x["slug"]
                                                 for x in data.leagues()]})


class Gate(BaseHTTPMiddleware):
    """Cloudflare Access, checked before anything else runs.

    The same `access.check` the Streamlit app calls, in the one place every
    request has to pass through. Static files are let past because a stylesheet
    is not a roster, and because a login page that cannot load its own CSS is a
    worse experience for no gain.
    """

    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/static/"):
            return await call_next(request)
        peer = request.client.host if request.client else None
        verdict = access.check(request.headers, peer)
        if not verdict.ok:
            return templates.TemplateResponse(
                request, "denied.html",
                {"request": request, "why": verdict.why}, status_code=403)
        request.state.who = verdict.email
        request.state.gate_off = verdict.off
        return await call_next(request)


def build() -> Starlette:
    return Starlette(
        routes=[
            Route("/", home),
            Route("/week", week),
            Route("/waivers", waivers),
            Route("/trades", trades),
            Route("/trades/grade", trade_grade, methods=["POST"]),
            Route("/trades/target", trade_target, methods=["POST"]),
            Route("/trades/raid", trade_raid, methods=["POST"]),
            Route("/trades/find", trade_find, methods=["POST"]),
            Route("/scores", scores),
            Route("/card", card),
            Route("/refresh", refresh, methods=["POST", "GET"]),
            Route("/healthz", health),
            Mount("/static", StaticFiles(directory=str(HERE / "static")),
                  name="static"),
        ],
        middleware=[Middleware(Gate)],
    )


app = build()
