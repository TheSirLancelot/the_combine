"""The Combine — draft day and in-season UI.

Streamlit front end over the same code the MCP tools use. It calls build_board
directly and renders DataFrames rather than parsing the CLI's text tables, so
sorting and filtering come from Streamlit instead of from me.

  uv run streamlit run app.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from combine import config
from combine.pipeline.board import build as build_board
from combine.pipeline.crosswalk import load_ids
from combine.pipeline.distribution import Distribution
from combine.pipeline.draftplan import next_pick, snake_picks
from combine.pipeline.lineup import (
    near_misses,
    optimal_moves,
    order_starters,
    problems,
    split,
    swaps,
)
from combine.pipeline.needs import compute as compute_needs
from combine.pipeline.providers.pff_api import PffApi
from combine.pipeline.startsit import review
from combine.pipeline.usage import GLOSSARY, OUTCOME_GLOSSARY, family, for_espn
from combine.pipeline.usage import load as load_usage
from combine.platforms import client_for

st.set_page_config(page_title="The Combine", page_icon="🏈", layout="wide")

POOL_SIZE = 250


def _code_version() -> str:
    """A token that changes whenever the combine package changes on disk.

    Every cached function below takes it as an argument, so editing any module
    invalidates every cache. Without this, Streamlit re-executes app.py on save
    but keeps already-imported modules and their cached return values, so new
    code runs against objects built by the old code. That is not a hypothetical:
    adding a field to the Band dataclass produced exactly that, an
    AttributeError for a field the running code had just introduced.

    Cheap enough to do on every rerun: a stat call per module, no reads.
    """
    root = Path(__file__).resolve().parent / "src" / "combine"
    stamps = sorted(f"{p.name}:{p.stat().st_mtime_ns}" for p in root.rglob("*.py"))
    return hashlib.sha1("|".join(stamps).encode()).hexdigest()[:12]


# --- data -----------------------------------------------------------------

@st.cache_data(ttl=25, show_spinner="pulling live league state...")
def load(league: str, _nonce: int, _version: str) -> dict:
    """One ESPN round trip per refresh, shared by every section on the page.

    _nonce is a cache buster the Refresh button increments; ttl keeps the
    auto-poll honest without hammering ESPN on every widget interaction.
    """
    c = client_for(league)
    slots, teams = c.roster_slots(), c.team_count()
    rows, counts = build_board(
        league, c.free_agents(position=None, limit=POOL_SIZE),
        slots, teams, c.scoring_rules(),
    )
    roster = c.my_roster()
    draft_err = ""
    try:
        taken_ids, my_ids = c.draft_picks()
    except Exception as exc:
        taken_ids, my_ids = set(), set()
        draft_err = f"{type(exc).__name__}: {exc}"
    byes = {r.state.name: r.bye for r in rows if r.bye}

    df = pd.DataFrame([{
        "#": r.overall_rank,
        "POS": r.state.pos,
        "PosRk": f"{r.state.pos}{r.avg_pos_rank}",
        "PosN": r.avg_pos_rank,
        "_id": r.state.player_id,
        "Player": r.state.name,
        "TM": r.state.team or "",
        "ESPN": round(r.espn_pts, 1),
        "PFF": round(r.pff_pts, 1) if r.pff_pts is not None else None,
        "AVG": round(r.avg, 1),
        "VORP": round(r.vorp, 1),
        "ADP": r.adp,
        "VAL": r.value,
        "TD%": round(r.td_share * 100) if r.td_share is not None else None,
        "G": round(r.games) if r.games else None,
        "BYE": r.bye,
        "TIER": r.tier,
        "BUZZ": ("SPLIT" if r.buzz.split else f"{r.buzz.net:+d}") if r.buzz else "",
        "FLAG": r.flag,
        "News": r.news.note if r.news else "",
        "Analysts": r.buzz.describe() if r.buzz else "",
    } for r in rows])

    # A column with any missing value gets upcast to float64 by pandas, which
    # is why BYE rendered as 11.0. Nullable Int64 keeps them integers.
    for col in ("#", "PosN", "VAL", "TD%", "G", "BYE", "TIER"):
        df[col] = df[col].astype("Int64")
    for col in ("ESPN", "PFF", "AVG", "VORP", "ADP"):
        df[col] = df[col].round(1)

    return {
        "df": df,
        "taken_ids": taken_ids, "my_ids": my_ids, "draft_err": draft_err,
        "roster": [(p.name, p.pos, p.team, p.status) for p in roster],
        "needs": compute_needs(roster, slots, byes),
        "slots": slots, "teams": teams, "rounds": c.roster_size(),
        "unmatched": counts.get("unmatched", 0) + counts.get("ambiguous", 0),
        "league_name": c.ping(),
    }


# Row colours, in the same priority order the FLAG column uses. Kept in one
# place so the legend below cannot drift from what actually renders.
ROW_STYLES = [
    ("OUT?",   "rgba(220,50,50,0.18)",  "red",    "Projected zero but drafted early. Something happened; find out what."),
    ("NEWS!",  "rgba(220,50,50,0.18)",  "red",    "Injury, suspension or legal news the projections have not absorbed."),
    ("VALUE",  "rgba(60,180,90,0.15)",  "green",  "ADP has him falling 12+ spots past where the numbers put him."),
    ("REACH",  "rgba(230,150,40,0.15)", "amber",  "The room drafts him 12+ spots earlier than the numbers justify."),
    ("no-pff", None,                    "dimmed", "No PFF match, so AVG is ESPN alone. Deep bench guy usually; high up, treat like OUT?."),
]


def style(df: pd.DataFrame):
    def row_color(row):
        flag = str(row.get("FLAG", ""))
        for prefix, bg, _, _ in ROW_STYLES:
            if flag.startswith(prefix):
                css = f"background-color: {bg}" if bg else "opacity: 0.55"
                return [css] * len(row)
        return [""] * len(row)
    return df.style.apply(row_color, axis=1)


def legend():
    with st.expander("What the row colours mean"):
        for prefix, bg, name, why in ROW_STYLES:
            swatch = (f"<span style='background:{bg};padding:1px 10px;"
                      f"border-radius:3px'>&nbsp;</span>" if bg
                      else "<span style='opacity:0.55'>dimmed</span>")
            st.markdown(f"{swatch} &nbsp;`{prefix}` — {why}",
                        unsafe_allow_html=True)


BOARD_COLS = ["#", "PosRk", "Player", "TM", "ESPN", "PFF", "AVG", "VORP",
              "ADP", "VAL", "TD%", "G", "BYE", "TIER", "BUZZ", "FLAG"]

COL_CONFIG = {
    "#": st.column_config.NumberColumn("#", help="Overall rank by VORP, whole pool", width="small", format="%d"),
    "ESPN": st.column_config.NumberColumn("ESPN", help="ESPN projection, scored for this league", format="%.1f"),
    "PFF": st.column_config.NumberColumn("PFF", help="PFF projection, scored for this league", format="%.1f"),
    "BYE": st.column_config.NumberColumn("Bye", width="small", format="%d"),
    "PosRk": st.column_config.TextColumn("Pos", help="Rank at his position, whole pool", width="small"),
    "VORP": st.column_config.NumberColumn("VORP", help="Value over replacement. This sets the order.", format="%.1f"),
    "AVG": st.column_config.NumberColumn("AVG", help="Mean of the projection sources", format="%.1f"),
    "ADP": st.column_config.NumberColumn("ADP", help="PFF average draft position for this scoring format", format="%.1f"),
    "VAL": st.column_config.NumberColumn("VAL", help="ADP minus VORP rank. Positive = the room lets him fall.", format="%d"),
    "TD%": st.column_config.NumberColumn("TD%", help="Share of his projection that is touchdowns. High = volatile.", format="%d%%"),
    "G": st.column_config.NumberColumn("G", help="Projected games. Under 17 means an absence is priced in.", width="small", format="%d"),
    "TIER": st.column_config.NumberColumn("Tier", help="Tier within his position", width="small", format="%d"),
    "BUZZ": st.column_config.TextColumn("Buzz", help="Net analyst sentiment, SPLIT when they disagree", width="small"),
}


@st.cache_data(show_spinner="reading outcome history...")
def outcome_frame(season: int, _version: str):
    """Past outcomes as a plain DataFrame.

    A DataFrame on purpose, not a Distribution. Streamlit re-executes this
    script on every edit but keeps already-imported modules, and a cached
    OBJECT built by an older version of a module survives that reload while the
    code around it moves on. That is how a stale `Band` ends up in front of new
    code that expects a field it does not have. Caching plain data and
    rebuilding the object each run makes the whole class of bug impossible.
    """
    from combine import db
    from combine.pipeline.training import build as build_frame

    try:
        with db.connect(readonly=True) as conn:
            return build_frame(conn, season)
    except Exception:
        return None


def outcome_distribution(season: int):
    """Rebuilt per run from the cached frame. Cheap: a filter and a column."""
    frame = outcome_frame(season, _code_version())
    if frame is None or frame.empty:
        return None
    dist = Distribution(frame)
    return None if dist.empty else dist


@st.cache_data(ttl=60, show_spinner="pulling this week's lineup...")
def load_week(league: str, week: int, _nonce: int, _version: str) -> dict:
    """One box-score round trip. Much cheaper than the draft loader: no
    250-player pool, no crosswalk, no draft-pick scrape.

    Weekly projections only exist on the box score. The season-level roster
    object returns projected_points=None, which is why this does not reuse
    load() above.
    """
    c = client_for(league)
    m = c.matchup(week or None)
    starters, bench = split(m.my_lineup)

    # PFF is optional here on purpose: an expired key or an unbuilt crosswalk
    # should cost you the usage column, not the lineup.
    calls, usage, ids, in_season, pff_err = [], {}, {}, True, ""
    try:
        ids = load_ids()
        api = PffApi()
        in_season = api.season_state().in_season
        usage = load_usage(api)
        calls, _ = review(m, usage, ids, dist=outcome_distribution(config.SEASON - 1))
    except Exception as exc:
        pff_err = f"{type(exc).__name__}: {exc}"

    return {
        "matchup": m,
        "slots": c.roster_slots(),
        "starters": order_starters(starters, c.roster_slots()),
        "bench": bench,
        "problems": problems(starters),
        "swaps": swaps(starters, bench,
                       dist=outcome_distribution(config.SEASON - 1)),
        "near": near_misses(starters, bench,
                            dist=outcome_distribution(config.SEASON - 1)),
        "calls": calls,
        "optimal": optimal_moves(m.my_lineup, c.roster_slots()),
        "dist": outcome_distribution(config.SEASON - 1),
        "usage": usage,
        "ids": ids,
        "in_season": in_season,
        "pff_err": pff_err,
    }


def lineup_frame(players, show_actual: bool, usage=None, ids=None,
                 dist=None) -> pd.DataFrame:
    rows = []
    for p in players:
        role = for_espn(usage, ids, p.player_id) if usage and ids else None
        band = dist.for_player(family(p.pos), p.projected) if dist else None
        rows.append({
            "SLOT": p.slot,
            "POS": p.pos,
            "Player": p.name,
            "TM": p.team or "",
            "OPP": p.opponent,
            "PROJ": round(p.projected, 1),
            "ACT": round(p.actual, 1),
            "ROLE": role.line(p.pos) if role else "",
            "FLOOR": round(band.floor, 1) if band else None,
            "CEIL": round(band.ceiling, 1) if band else None,
            "BOOM": round(band.boom * 100) if band else None,
            "BUST": round(band.bust * 100) if band else None,
            "NOTE": " ".join(x for x in (p.status if p.status != "OK" else "",
                                         "LOCK" if p.locked and not p.played else "")
                             if x),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if not show_actual:
        df = df.drop(columns=["ACT"])
    if dist is None:
        df = df.drop(columns=["FLOOR", "CEIL", "BOOM", "BUST"])
    return df


WEEK_COLS = {
    "PROJ": st.column_config.NumberColumn(
        "Proj", help="ESPN's weekly projection. A mean, not a typical outcome.",
        format="%.1f"),
    "ACT": st.column_config.NumberColumn("Act", format="%.1f"),
    "FLOOR": st.column_config.NumberColumn(
        "Floor", help="10th percentile outcome for comparable players", format="%.1f"),
    "CEIL": st.column_config.NumberColumn(
        "Ceil", help="90th percentile outcome for comparable players", format="%.1f"),
    "BOOM": st.column_config.NumberColumn(
        "Boom", help="Chance of a 20+ point game", format="%d%%", width="small"),
    "BUST": st.column_config.NumberColumn(
        "Bust", help="Chance of under half the projection", format="%d%%",
        width="small"),
    "ROLE": st.column_config.TextColumn(
        "Role (PFF)", help="See the glossary below the tables", width="large"),
}


def role_legend(in_season: bool, dist_season: int | None):
    with st.expander("What the Role and outcome columns mean"):
        st.caption(
            "Role is PFF usage and efficiency, shown beside the projection and "
            "deliberately never blended into it. Grades and rates are on scales "
            "that have nothing to do with fantasy points."
            + ("" if in_season else " Before kickoff these are last season's "
                                    "numbers, a prior rather than evidence about "
                                    "this week."))
        for title, entries in GLOSSARY:
            st.markdown(f"**{title}**")
            for token, meaning in entries:
                st.markdown(f"&nbsp;&nbsp;`{token}` &nbsp; {meaning}",
                            unsafe_allow_html=True)
        if dist_season:
            st.markdown("**Outcome columns**")
            for token, meaning in OUTCOME_GLOSSARY:
                st.markdown(f"&nbsp;&nbsp;`{token}` &nbsp; {meaning}",
                            unsafe_allow_html=True)
            st.caption(
                f"Outcome columns are measured on {dist_season}. "
                "These describe the spread around a projection, which ESPN does "
                "not give you: at 8 to 16 projected points a back booms 15.4% of "
                "the time against a receiver's 11.8% and a defender's 9.8%. They "
                "are context for a close call, not a ranking. Sorting a lineup by "
                "ceiling or floor was backtested and lost at every threshold.")


# --- sidebar --------------------------------------------------------------

leagues = config.leagues()
if not leagues:
    st.error("No leagues configured. Check .env, then run `combine doctor`.")
    st.stop()

with st.sidebar:
    st.title("The Combine")

    # Draft is dormant outside August, so Week leads.
    mode = st.radio("Mode", ["Week", "Draft"], horizontal=True)

    league = st.radio("League", list(leagues),
                      format_func=lambda s: f"{s} · {leagues[s].name}")
    cfg = leagues[league]

    slot, on_clock, week_no = cfg.draft_slot or 1, 1, 0
    if mode == "Draft":
        with st.expander("Draft slot", expanded=False):
            slot = st.number_input("Your draft slot", 1, 32,
                                   value=cfg.draft_slot or 1,
                                   help="Defaults to <SLUG>_DRAFT_POS in .env")
        on_clock = st.number_input("Pick on the clock", 1, 400, value=1)
    else:
        week_no = st.number_input("Week", 0, 18, value=0,
                                  help="0 follows the league's current week")

    with st.expander("Refresh", expanded=False):
        # A draft moves every few seconds; a lineup does not.
        auto = st.toggle("Auto refresh", value=mode == "Draft")
        every = st.select_slider("Every", [15, 30, 45, 60], value=30,
                                 disabled=not auto, format_func=lambda n: f"{n}s")
        if st.button("Refresh now", use_container_width=True, type="primary"):
            st.session_state.nonce = st.session_state.get("nonce", 0) + 1
        st.caption("paused" if not auto else f"polling every {every}s")

st.session_state.setdefault("nonce", 0)

# ESPN's league API does not expose a live draft: picks stay in the draft room
# service and land on rosters only after it finishes. Verified mid-draft
# 2026-09-06, draft endpoint and every roster returned zero. So the pool is
# tracked by hand during a draft.
st.session_state.setdefault("gone", [])
st.session_state.setdefault("mine", [])


# --- page -----------------------------------------------------------------

@st.fragment(run_every=f"{every}s" if auto else None)
def page():
    try:
        data = load(league, st.session_state.nonce, _code_version())
    except Exception as exc:  # cookies die mid-season; say so plainly
        st.error(f"{type(exc).__name__}: {exc}")
        st.info("If this is a 401 or an empty league, the ESPN cookies expired. "
                "Run `python scripts/refresh_espn_cookies.py`.")
        return

    base = data["df"]
    live_taken = set(data["taken_ids"])
    live_mine = set(data["my_ids"])

    all_names = base["Player"].tolist()
    with st.sidebar:
        st.divider()
        st.markdown("**Draft tracking**")
        pasted = st.text_area("Paste pick history", height=120,
                              placeholder="Pick History tab, then the console snippet")
        if pasted:
            blob = " ".join(pasted.split()).lower()
            st.session_state.pasted_gone = [n for n in all_names if n.lower() in blob]
        st.session_state.setdefault("pasted_gone", [])
        st.caption(f"{len(st.session_state.get('pasted_gone', []))} matched from paste")
        if live_taken:
            st.caption(f"Live draft: {len(live_taken)} picks in, "
                       f"{len(live_mine)} yours")
        elif data.get("draft_err"):
            st.error(f"draft fetch failed: {data['draft_err']}")
        else:
            st.caption("No live picks seen. Track by hand if needed.")
        with st.expander("Manual entry", expanded=False):
            st.session_state.mine = st.multiselect(
                "My picks", all_names, default=st.session_state.mine)
            st.session_state.gone = st.multiselect(
                "Taken by others", all_names, default=st.session_state.gone)


    taken_names = (set(st.session_state.mine) | set(st.session_state.gone)
                   | set(st.session_state.get("pasted_gone", [])))
    df = base[~base["_id"].isin(live_taken) & ~base["Player"].isin(taken_names)].copy()

    from combine.platforms import PlayerState
    mine_rows = base[base["_id"].isin(live_mine)
                     | base["Player"].isin(st.session_state.mine)]
    roster = [PlayerState(player_id="", name=r["Player"], team=r["TM"],
                          pos=r["POS"]) for _, r in mine_rows.iterrows()]
    byes = {r["Player"]: r["BYE"] for _, r in base.iterrows() if pd.notna(r["BYE"])}
    needs = compute_needs(roster, data["slots"], byes)
    data = {**data, "roster": [(p.name, p.pos, p.team, p.status) for p in roster]}

    picks = snake_picks(slot, data["teams"], data["rounds"])
    nxt = next_pick(on_clock, picks)

    top = st.columns([2, 1, 1, 1])
    top[0].metric("League", cfg.name)
    top[1].metric("Roster", f"{len(data['roster'])}/{data['rounds']}")
    top[2].metric("On the clock", on_clock)
    top[3].metric("Your next pick", nxt or "done")
    st.caption(f"{data['league_name']} · your picks: "
               f"{', '.join(str(p) for p in picks[:8])}..."
               + (f" · {data['unmatched']} of {len(df)} had no PFF match"
                  if data["unmatched"] else ""))

    # Needs section removed 2026-09-06: it depends on reading your roster,
    # and ESPN does not populate rosters until the draft completes. The manual
    # "My picks" list is the only source mid-draft and is not worth the space.
    # Bring it back once the draft-room reader exists.

    # ---- plan
    st.subheader(f"Timing against pick {nxt}")
    rows_for_plan = df.dropna(subset=["ADP"])
    gone = rows_for_plan[rows_for_plan["ADP"] < (nxt or 999) - 8]
    flip = rows_for_plan[(rows_for_plan["ADP"] >= (nxt or 999) - 8)
                         & (rows_for_plan["ADP"] <= (nxt or 999) + 8)]
    safe = rows_for_plan[rows_for_plan["ADP"] > (nxt or 999) + 8]

    o1, o2 = st.columns([3, 1])
    need_only = o1.checkbox("Only positions I still need", value=False,
                            disabled=not needs.empty)
    rows_each = o2.slider("Rows per group", 5, 25, 10, step=5)
    def trim(d):
        if need_only and needs.open_positions:
            d = d[d["POS"].isin(needs.open_positions)]
        return d.head(rows_each)[BOARD_COLS]

    legend()

    # Players with no ADP never appear in the timing buckets, which in RCL is
    # every defender, i.e. five of the starting slots. No IDP draft position
    # exists anywhere in PFF's exports, so we do not invent timing for them.
    # We just show them, best first, so they are not silently missing.
    no_adp = df[df["ADP"].isna()]

    for title, frame, note in [
        ("Gone before your pick", gone, "Your real choices. Take the best of these."),
        ("Coin flip", flip, "Within 8 picks either way. ADP is an average, not a deadline."),
        ("Still there", safe, "He lasts. Spend this pick elsewhere and come back for him."),
        ("No ADP — timing unknown", no_adp,
         "Mostly IDP. PFF publishes no IDP draft position, so there is no market "
         "signal here. Ranked by VORP; use tier and positional scarcity instead."),
    ]:
        st.markdown(f"**{title}** &nbsp; <span style='opacity:0.6'>{note}</span>",
                    unsafe_allow_html=True)
        st.dataframe(style(trim(frame)), hide_index=True,
                     use_container_width=True, column_config=COL_CONFIG)

    # ---- round plan
    positions_all = sorted(df["POS"].dropna().unique())
    st.subheader("Targets by pick")
    st.caption("For each of your remaining picks, who should plausibly still be "
               "there (ADP at or past that pick) ranked by VORP. Blended ESPN + PFF.")
    rp1, rp2 = st.columns([1, 3])
    n_picks = rp1.slider("Picks ahead", 2, 10, 6)
    strat = rp2.multiselect(
        "Limit to positions", positions_all,
        default=[p for p in ("RB", "WR", "TE") if p in positions_all],
        help="Your strategy. Clear it to see every position.")

    upcoming = [p for p in picks if p >= on_clock][:n_picks]
    pool = df[df["POS"].isin(strat)] if strat else df
    for p in upcoming:
        rnd = picks.index(p) + 1
        # Anyone whose ADP is at or beyond this pick should plausibly last.
        # Slack of 6 because ADP is an average, not a guarantee.
        avail = pool[pool["ADP"].isna() | (pool["ADP"] >= p - 6)]
        best = avail.head(5)
        names = " · ".join(
            f"{r['PosRk']} {r['Player']}"
            + (f" ({r['ADP']:.0f})" if pd.notna(r["ADP"]) else "")
            for _, r in best.iterrows())
        st.markdown(f"**R{rnd} pick {p}** &nbsp; <span style='opacity:0.75'>"
                    f"{names or 'nothing left at those positions'}</span>",
                    unsafe_allow_html=True)

    # ---- board
    st.subheader("Board")
    positions = positions_all
    c1, c2 = st.columns([3, 1])
    pick_pos = c1.multiselect("Positions", positions, default=[])
    limit = c2.slider("Max rows", 10, 200, 60, step=10)

    view = df[df["POS"].isin(pick_pos)] if pick_pos else df

    # Rank window. With one position selected this walks that position's own
    # ranking (RB20 to RB40); otherwise it walks the overall board. Useful when
    # the pool does not reflect who has actually been drafted and the top of
    # the list is full of players who are already gone.
    single = pick_pos[0] if len(pick_pos) == 1 else None
    rank_col = "PosN" if single else "#"
    label = f"{single} rank window" if single else "Overall rank window"
    if not view.empty:
        lo_all = int(view[rank_col].min())
        hi_all = int(view[rank_col].max())
        if hi_all > lo_all:
            lo, hi = st.slider(label, lo_all, hi_all, (lo_all, hi_all))
            view = view[(view[rank_col] >= lo) & (view[rank_col] <= hi)]
            st.caption(f"showing {single or 'overall'} {lo} to {hi} · "
                       f"{len(view)} players")

    st.dataframe(style(view.head(limit)[BOARD_COLS]), hide_index=True,
                 use_container_width=True, column_config=COL_CONFIG, height=600)

    # ---- one player
    st.subheader("Player detail")
    who = st.selectbox("Player", [""] + df["Player"].tolist())
    if who:
        row = df[df["Player"] == who].iloc[0]
        m = st.columns(5)
        m[0].metric("Overall", f"#{row['#']}")
        m[1].metric("Position", row["PosRk"])
        m[2].metric("VORP", f"{row['VORP']:.1f}")
        m[3].metric("ADP", f"{row['ADP']:.1f}" if pd.notna(row["ADP"]) else "-")
        m[4].metric("TD share", f"{row['TD%']}%" if pd.notna(row["TD%"]) else "-")
        if row["News"]:
            st.error(f"News: {row['News']}")
        if row["Analysts"]:
            st.info(f"Analysts: {row['Analysts']}")


@st.fragment(run_every=f"{every}s" if auto else None)
def week_page():
    try:
        data = load_week(league, int(week_no), st.session_state.nonce,
                         _code_version())
    except Exception as exc:
        st.error(f"{type(exc).__name__}: {exc}")
        st.info("If this is a 401 or an empty league, the ESPN cookies expired. "
                "Run `python scripts/refresh_espn_cookies.py`.")
        return

    m = data["matchup"]
    played = any(p.played for p in m.my_lineup)
    final = all(p.played for p in m.my_lineup if p.starting)

    st.subheader(f"Week {m.week} · {m.my_team} vs {m.their_team}")
    cols = st.columns(4)
    cols[0].metric("My projection", f"{m.my_proj:.1f}")
    cols[1].metric("Their projection", f"{m.their_proj:.1f}",
                   delta=f"{m.my_proj - m.their_proj:+.1f} me", delta_color="normal")
    if played:
        cols[2].metric("My actual", f"{m.my_score:.1f}")
        cols[3].metric("Their actual", f"{m.their_score:.1f}")
    else:
        cols[2].metric("State", "pregame")

    for p in data["problems"]:
        st.error(f"{p.slot}: {p.name} is {'on bye' if p.on_bye else p.status}"
                 f" and still in your lineup")

    if data["pff_err"]:
        st.info(f"PFF usage unavailable ({data['pff_err']}). Lineup below is "
                f"ESPN's projection only. If the crosswalk has never been built, "
                f"run `combine pffids {league}`.")

    add, drop, gain = data["optimal"]
    if gain > 0.05 and add:
        st.error(f"Lineup is {gain:.1f} projected points short of optimal")
        for a in add:
            st.markdown(f"START &nbsp; **{a.name}** ({a.pos}) {a.projected:.1f} "
                        f"{a.opponent}")
        for d in drop:
            st.markdown(f"BENCH &nbsp; {d.name} ({d.pos}) {d.projected:.1f}")
        st.caption("Exact slot assignment, not a prediction. Worth about +3.5pp "
                   "of win rate in the 2025 backtest.")

    if data["calls"]:
        st.warning("Start/sit questions this week")
        for c in sorted(data["calls"], key=lambda x: -x.proj_edge):
            label = "SWAP" if c.verdict == "CLEAR" else c.verdict
            with st.container(border=True):
                st.markdown(f"**{label}** &nbsp; `{c.slot}` &nbsp; "
                            f"+{c.proj_edge:.1f} projected")
                st.markdown(f"IN &nbsp; **{c.bench.name}** {c.bench.projected:.1f} "
                            f"{c.bench.opponent}")
                st.markdown(f"OUT &nbsp; {c.starter.name} {c.starter.projected:.1f} "
                            f"{c.starter.opponent}")
                if c.opp_edge is not None:
                    word = "more" if c.opp_edge > 0 else "fewer"
                    st.caption(f"usage: {abs(c.opp_edge):.1f} {word} opportunities a "
                               f"game for {c.bench.name}")
                for r in c.reasons:
                    st.caption(r)
    elif not data["pff_err"]:
        st.success("No start/sit questions: no bench player is projected far "
                   "enough ahead of a starter he could replace.")
        if data["near"]:
            with st.expander("Closest comparisons, none of them close enough"):
                for c in data["near"]:
                    st.markdown(
                        f"**{c.bench.name}** {c.bench.projected:.1f} would need "
                        f"**{c.short_by:.1f} more** to be worth weighing against "
                        f"{c.starter.name} {c.starter.projected:.1f} "
                        f"(`{c.starter.slot}`)")
                st.caption(
                    f"A bench player has to be {data['near'][0].needed:.1f}+ points "
                    f"AHEAD of a starter before the gap beats the noise, not level "
                    f"with him. That figure scales with how widely these two "
                    f"positions actually scatter; see the glossary.")

    if data["usage"] and not data["in_season"]:
        st.caption("Usage columns are last season's numbers, a prior rather than "
                   "evidence about this week.")

    left, right = st.container(), st.container()
    with left:
        st.markdown("**Starters**")
        st.dataframe(
            lineup_frame(data["starters"], played, data["usage"], data["ids"],
                         data["dist"]),
            hide_index=True, use_container_width=True, column_config=WEEK_COLS)
    with right:
        st.markdown("**Bench**")
        st.dataframe(
            lineup_frame(data["bench"], played, data["usage"], data["ids"],
                         data["dist"]),
            hide_index=True, use_container_width=True, column_config=WEEK_COLS)

    role_legend(data["in_season"], config.SEASON - 1 if data["dist"] else None)

    if final:
        st.caption("week is final")


if mode == "Draft":
    page()
else:
    week_page()
