"""Merged draft board: two projections plus the market.

Sources:
  ESPN   season projection, already scored under this league's rules
  PFF    season projection, already scored under this league's rules
  ADP    PFF's draft rankings export, league-agnostic

CONS is the mean of the two projections. VAL is the interesting number: ADP
minus our overall consensus rank. Positive means the room is letting him fall
past where the numbers say he belongs, which is the only edge a draft offers.

Deliberate shortcut for the 2026 draft: this blends finished point totals
rather than rescoring PFF's stat lines through the league rules. That is
phase 2 work and is not needed to draft. The stat lines are parsed and kept,
so doing it properly later changes the blend, not the ingest.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..format import tiers
from ..platforms import PlayerState
from .crosswalk import build_index, load_overrides, match
from .providers import news as news_src, opinion, pff_csv, pff_rankings
from .vorp import replacement_points

DISAGREE_AT = 10   # positional rank gap between sources worth flagging
VALUE_AT = 12      # ADP vs consensus gap worth flagging


@dataclass(frozen=True)
class _NameOnly:
    """The crosswalk matches on objects with name/team/pos. The cheat sheet has
    only names, so team and pos stay empty and matching falls back to name."""
    name: str
    team: str = ""
    pos: str = ""


@dataclass
class BoardRow:
    state: PlayerState
    espn_pts: float
    pff_pts: float | None
    pff_name: str | None
    pff_auction: float | None
    bye: int | None
    how: str
    adp: float | None = None
    rank_proj: float | None = None   # PFF rankings' own points, for the zero check
    pff_rank_pos: int | None = None  # PFF analysts' positional rank, not their model's
    buzz: object | None = None       # analyst sentiment, never blended
    news: object | None = None       # late-breaking status, never blended
    consensus: float = 0.0
    vorp: float = 0.0        # consensus minus replacement level at his position
    overall_rank: int = 0
    cons_pos_rank: int = 0   # rank at his position by consensus, whole pool
    value: int | None = None
    flag: str = ""


def build(league: str, states: list[PlayerState], slots: dict | None = None,
          teams: int = 12) -> tuple[list[BoardRow], dict]:
    """states should be the UNFILTERED available pool, so overall_rank and the
    ADP comparison are meaningful. Filter for display afterwards.

    Ordering is by VORP, not raw points. ADP is a draft-order number, so
    comparing it against a points-rank compares two different scales and made
    every VAL systematically negative. Pass the league's slot counts to get
    this right; without them it falls back to points order."""
    rows: list[BoardRow] = []
    have_pff = pff_csv.available(league)
    idx = build_index(pff_csv.load(league)) if have_pff else {}
    overrides = load_overrides()

    rank_rows = pff_rankings.load(league)
    rank_idx = build_index(rank_rows) if rank_rows else {}

    # ESPN cheat sheet. Opinion, kept beside the numbers and never inside them.
    sentiment = opinion.load()
    news_items = news_src.load()
    news_idx = build_index([_NameOnly(n) for n in news_items]) if news_items else {}
    buzz_idx = build_index([_NameOnly(n) for n in sentiment]) if sentiment else {}

    counts: dict[str, int] = {}
    for s in states:
        hit, how = (None, "no-source")
        if have_pff:
            hit, how = match(s.name, s.team, s.pos, idx, overrides)
        counts[how] = counts.get(how, 0) + 1

        rank_hit = None
        if rank_idx:
            rank_hit, _ = match(s.name, s.team, s.pos, rank_idx, overrides)

        news_hit = None
        if news_idx:
            shim, _ = match(s.name, s.team, s.pos, news_idx, overrides)
            news_hit = news_items.get(shim.name) if shim else None

        buzz_hit = None
        if buzz_idx:
            shim, _ = match(s.name, s.team, s.pos, buzz_idx, overrides)
            buzz_hit = sentiment.get(shim.name) if shim else None

        rows.append(
            BoardRow(
                state=s,
                espn_pts=s.platform_proj or 0.0,
                pff_pts=hit.fantasy_points if hit else None,
                pff_name=hit.name if hit else None,
                pff_auction=hit.auction_value if hit else None,
                bye=(hit.bye if hit else None) or (rank_hit.bye if rank_hit else None),
                how=how,
                adp=rank_hit.adp if rank_hit else None,
                rank_proj=rank_hit.proj_points if rank_hit else None,
                pff_rank_pos=rank_hit.pos_rank if rank_hit else None,
                buzz=buzz_hit,
                news=news_hit,
            )
        )

    for r in rows:
        r.consensus = (r.espn_pts + r.pff_pts) / 2 if r.pff_pts is not None else r.espn_pts

    if slots:
        repl = replacement_points(rows, slots, teams)
        for r in rows:
            r.vorp = r.consensus - repl.get(r.state.pos, 0.0)
    else:
        for r in rows:
            r.vorp = r.consensus

    rows.sort(key=lambda r: r.vorp, reverse=True)
    for i, r in enumerate(rows):
        r.overall_rank = i + 1
        if r.adp is not None:
            r.value = int(round(r.adp - r.overall_rank))

    # our own consensus positional rank, for comparison against PFF's ranking
    by_pos_cons: dict[str, list[BoardRow]] = {}
    for r in rows:
        by_pos_cons.setdefault(r.state.pos, []).append(r)
    for group in by_pos_cons.values():
        for i, r in enumerate(group):
            r.cons_pos_rank = i + 1

    # positional rank within each source, to surface disagreement
    for src in ("espn_pts", "pff_pts"):
        by_pos: dict[str, list[BoardRow]] = {}
        for r in rows:
            if getattr(r, src) is not None:
                by_pos.setdefault(r.state.pos, []).append(r)
        for group in by_pos.values():
            group.sort(key=lambda r: getattr(r, src), reverse=True)
            for i, r in enumerate(group):
                setattr(r, f"_{src}_rank", i + 1)

    for r in rows:
        # Ordered by how much it should change your pick.
        if r.rank_proj == 0 and r.adp is not None:
            r.flag = "OUT?"          # ranked, drafted early, projected zero
        elif r.news is not None and r.news.severity == "high":
            r.flag = "NEWS!"         # reporting the projections have not absorbed
        elif r.pff_pts is None:
            r.flag = "no-pff"
        elif r.value is not None and abs(r.value) >= VALUE_AT:
            r.flag = f"{'VALUE' if r.value > 0 else 'REACH'}{r.value:+d}"
        else:
            e, p = getattr(r, "_espn_pts_rank", None), getattr(r, "_pff_pts_rank", None)
            cons, pffrk = r.cons_pos_rank, r.pff_rank_pos
            if e and p and abs(e - p) >= DISAGREE_AT:
                r.flag = f"{'PFF' if p < e else 'ESPN'}+{abs(e - p)}"
            elif cons and pffrk and abs(cons - pffrk) >= DISAGREE_AT:
                # PFF's analysts ranking a player against PFF's own projections.
                # The only signal available on the IDP side, where there is no ADP.
                r.flag = f"PFFRK{pffrk - cons:+d}"

    rows.sort(key=lambda r: r.vorp, reverse=True)
    return rows, counts


def filter_pos(rows: list[BoardRow], position: str) -> list[BoardRow]:
    if not position:
        return rows
    want = position.strip().upper()
    return [r for r in rows if (r.state.pos or "").upper() == want]


def render(rows: list[BoardRow], header: str) -> str:
    tier_of = tiers([r.vorp for r in rows])
    # Both ranks, computed against the whole pool, so they do not shift when
    # the caller filters by position. A bare line number is misleading here.
    out = [header,
           f"{'#':>4} {'POS':<5} {'PLAYER':<21} {'TM':<3} {'ESPN':>6} {'PFF':>6} "
           f"{'CONS':>6} {'VOR':>6} {'ADP':>5} {'VAL':>4} {'BYE':>3} TIER {'BUZZ':>5} FLAG"]
    for i, r in enumerate(rows):
        s = r.state
        pff = f"{r.pff_pts:>6.1f}" if r.pff_pts is not None else "     -"
        adp = f"{r.adp:>5.1f}" if r.adp is not None else "    -"
        val = f"{r.value:>+4d}" if r.value is not None else "   -"
        bye = f"{r.bye:>3}" if r.bye else "  -"
        line = (f"{'#' + str(r.overall_rank):>4} {s.pos + str(r.cons_pos_rank):<5} "
                f"{s.name[:21]:<21} {(s.team or '--'):<3} "
                f"{r.espn_pts:>6.1f} {pff} {r.consensus:>6.1f} {r.vorp:>6.1f} {adp} {val} {bye} "
                f"T{tier_of[i]:<3}")
        if r.buzz is not None:
            line += f" {('SPLIT' if r.buzz.split else f'{r.buzz.net:+d}'):>5}"
        else:
            line += "      "
        extras = [x for x in (r.flag, s.status if s.status != "OK" else "") if x]
        return_line = line + (" " + " ".join(extras) if extras else "")
        out.append(return_line)
    return "\n".join(out)
