"""The Combine — draft day UI.

Streamlit front end over the same code the MCP tools use. It calls build_board
directly and renders DataFrames rather than parsing the CLI's text tables, so
sorting and filtering come from Streamlit instead of from me.

  uv run streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from combine import config  # noqa: E402
from combine.pipeline.board import build as build_board  # noqa: E402
from combine.pipeline.draftplan import next_pick, partition, snake_picks  # noqa: E402
from combine.pipeline.needs import compute as compute_needs  # noqa: E402
from combine.platforms import client_for  # noqa: E402

st.set_page_config(page_title="The Combine", page_icon="🏈", layout="wide")

POOL_SIZE = 250


# --- data -----------------------------------------------------------------

@st.cache_data(ttl=25, show_spinner="pulling live league state...")
def load(league: str, _nonce: int) -> dict:
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
    byes = {r.state.name: r.bye for r in rows if r.bye}

    df = pd.DataFrame([{
        "#": r.overall_rank,
        "POS": r.state.pos,
        "PosRk": f"{r.state.pos}{r.avg_pos_rank}",
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

    return {
        "df": df,
        "roster": [(p.name, p.pos, p.team, p.status) for p in roster],
        "needs": compute_needs(roster, slots, byes),
        "slots": slots, "teams": teams, "rounds": c.roster_size(),
        "unmatched": counts.get("unmatched", 0) + counts.get("ambiguous", 0),
        "league_name": c.ping(),
    }


def style(df: pd.DataFrame):
    """Red for 'find out why', amber for value, muted for stale."""
    def row_color(row):
        flag = str(row.get("FLAG", ""))
        if flag.startswith("OUT?") or flag.startswith("NEWS!"):
            return ["background-color: rgba(220,50,50,0.18)"] * len(row)
        if flag.startswith("VALUE"):
            return ["background-color: rgba(60,180,90,0.15)"] * len(row)
        if flag.startswith("no-pff"):
            return ["opacity: 0.55"] * len(row)
        return [""] * len(row)
    return df.style.apply(row_color, axis=1)


BOARD_COLS = ["#", "PosRk", "Player", "TM", "ESPN", "PFF", "AVG", "VORP",
              "ADP", "VAL", "TD%", "G", "BYE", "TIER", "BUZZ", "FLAG"]

COL_CONFIG = {
    "#": st.column_config.NumberColumn("#", help="Overall rank by VORP, whole pool", width="small"),
    "PosRk": st.column_config.TextColumn("Pos", help="Rank at his position, whole pool", width="small"),
    "VORP": st.column_config.NumberColumn("VORP", help="Value over replacement. This sets the order.", format="%.1f"),
    "AVG": st.column_config.NumberColumn("AVG", help="Mean of the projection sources", format="%.1f"),
    "ADP": st.column_config.NumberColumn("ADP", help="PFF average draft position for this scoring format", format="%.1f"),
    "VAL": st.column_config.NumberColumn("VAL", help="ADP minus VORP rank. Positive = the room lets him fall."),
    "TD%": st.column_config.NumberColumn("TD%", help="Share of his projection that is touchdowns. High = volatile.", format="%d%%"),
    "G": st.column_config.NumberColumn("G", help="Projected games. Under 17 means an absence is priced in.", width="small"),
    "TIER": st.column_config.NumberColumn("Tier", help="Tier within his position", width="small"),
    "BUZZ": st.column_config.TextColumn("Buzz", help="Net analyst sentiment, SPLIT when they disagree", width="small"),
}


# --- sidebar --------------------------------------------------------------

leagues = config.leagues()
if not leagues:
    st.error("No leagues configured. Check .env, then run `combine doctor`.")
    st.stop()

with st.sidebar:
    st.title("The Combine")
    league = st.radio("League", list(leagues),
                      format_func=lambda s: f"{s} · {leagues[s].name}")
    cfg = leagues[league]

    st.divider()
    slot = st.number_input("Your draft slot", 1, 32,
                           value=cfg.draft_slot or 1,
                           help="Defaults to <SLUG>_DRAFT_POS in .env")
    on_clock = st.number_input("Pick on the clock", 1, 400, value=1)
    st.divider()

    auto = st.toggle("Auto refresh", value=True)
    every = st.select_slider("Every", [15, 30, 45, 60], value=30,
                             disabled=not auto, format_func=lambda n: f"{n}s")
    if st.button("Refresh now", use_container_width=True, type="primary"):
        st.session_state.nonce = st.session_state.get("nonce", 0) + 1
    st.caption("Auto refresh paused" if not auto else f"Polling every {every}s")

st.session_state.setdefault("nonce", 0)


# --- page -----------------------------------------------------------------

@st.fragment(run_every=f"{every}s" if auto else None)
def page():
    try:
        data = load(league, st.session_state.nonce)
    except Exception as exc:  # cookies die mid-season; say so plainly
        st.error(f"{type(exc).__name__}: {exc}")
        st.info("If this is a 401 or an empty league, the ESPN cookies expired. "
                "Run `python scripts/refresh_espn_cookies.py`.")
        return

    df, needs = data["df"], data["needs"]
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

    # ---- needs
    st.subheader("Needs")
    if needs.empty:
        gaps = " · ".join(f"**{s}** x{n}" for s, n in needs.empty.items())
        st.warning(f"Unfilled starting slots: {gaps}", icon="⚠️")
        st.caption("Positions that fill them: "
                   + ", ".join(sorted(needs.open_positions)))
    elif data["roster"]:
        st.success("All starting slots filled. Everything from here is depth.")
    else:
        st.info("Nothing drafted yet, so every slot reads empty. "
                "Use the board until you have picks.")

    stacked = [(w, n) for w, n in sorted(needs.bye_load.items()) if n >= 3]
    if stacked:
        st.error("Bye pileup: "
                 + ", ".join(f"week {w} has {n} starters" for w, n in stacked))

    if data["roster"]:
        with st.expander(f"Roster ({len(data['roster'])})"):
            st.dataframe(pd.DataFrame(data["roster"],
                                      columns=["Player", "POS", "TM", "Status"]),
                         hide_index=True, use_container_width=True)

    # ---- plan
    st.subheader(f"Timing against pick {nxt}")
    rows_for_plan = df.dropna(subset=["ADP"])
    gone = rows_for_plan[rows_for_plan["ADP"] < (nxt or 999) - 8]
    flip = rows_for_plan[(rows_for_plan["ADP"] >= (nxt or 999) - 8)
                         & (rows_for_plan["ADP"] <= (nxt or 999) + 8)]
    safe = rows_for_plan[rows_for_plan["ADP"] > (nxt or 999) + 8]

    need_only = st.checkbox("Only positions I still need", value=False,
                            disabled=not needs.empty)
    def trim(d):
        if need_only and needs.open_positions:
            d = d[d["POS"].isin(needs.open_positions)]
        return d.head(10)[BOARD_COLS]

    cols = st.columns(3)
    for col, (title, frame, note) in zip(cols, [
        ("Gone before your pick", gone, "Your real choices. Take the best of these."),
        ("Coin flip", flip, "Within 8 picks either way. ADP is an average."),
        ("Still there", safe, "You can wait. Spend the pick elsewhere."),
    ]):
        with col:
            st.markdown(f"**{title}**")
            st.caption(note)
            st.dataframe(style(trim(frame)), hide_index=True,
                         use_container_width=True, column_config=COL_CONFIG)

    # ---- board
    st.subheader("Board")
    c1, c2 = st.columns([3, 1])
    positions = sorted(df["POS"].dropna().unique())
    pick_pos = c1.multiselect("Positions", positions, default=[])
    limit = c2.slider("Rows", 10, 150, 40, step=10)

    view = df[df["POS"].isin(pick_pos)] if pick_pos else df
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
        m[3].metric("ADP", row["ADP"] if pd.notna(row["ADP"]) else "-")
        m[4].metric("TD share", f"{row['TD%']}%" if pd.notna(row["TD%"]) else "-")
        if row["News"]:
            st.error(f"News: {row['News']}")
        if row["Analysts"]:
            st.info(f"Analysts: {row['Analysts']}")


page()
