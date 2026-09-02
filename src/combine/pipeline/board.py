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
from .providers import pff_csv, pff_rankings

DISAGREE_AT = 10   # positional rank gap between sources worth flagging
VALUE_AT = 12      # ADP vs consensus gap worth flagging


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
    consensus: float = 0.0
    overall_rank: int = 0
    value: int | None = None
    flag: str = ""


def build(league: str, states: list[PlayerState]) -> tuple[list[BoardRow], dict]:
    """states should be the UNFILTERED available pool, so overall_rank and the
    ADP comparison are meaningful. Filter for display afterwards."""
    rows: list[BoardRow] = []
    have_pff = pff_csv.available(league)
    idx = build_index(pff_csv.load(league)) if have_pff else {}
    overrides = load_overrides()

    rank_rows = pff_rankings.load(league)
    rank_idx = build_index(rank_rows) if rank_rows else {}

    counts: dict[str, int] = {}
    for s in states:
        hit, how = (None, "no-source")
        if have_pff:
            hit, how = match(s.name, s.team, s.pos, idx, overrides)
        counts[how] = counts.get(how, 0) + 1

        rank_hit = None
        if rank_idx:
            rank_hit, _ = match(s.name, s.team, s.pos, rank_idx, overrides)

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
            )
        )

    for r in rows:
        r.consensus = (r.espn_pts + r.pff_pts) / 2 if r.pff_pts is not None else r.espn_pts

    rows.sort(key=lambda r: r.consensus, reverse=True)
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
            r._cons_pos_rank = i + 1

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
        elif r.pff_pts is None:
            r.flag = "no-pff"
        elif r.value is not None and abs(r.value) >= VALUE_AT:
            r.flag = f"{'VALUE' if r.value > 0 else 'REACH'}{r.value:+d}"
        else:
            e, p = getattr(r, "_espn_pts_rank", None), getattr(r, "_pff_pts_rank", None)
            cons, pffrk = getattr(r, "_cons_pos_rank", None), r.pff_rank_pos
            if e and p and abs(e - p) >= DISAGREE_AT:
                r.flag = f"{'PFF' if p < e else 'ESPN'}+{abs(e - p)}"
            elif cons and pffrk and abs(cons - pffrk) >= DISAGREE_AT:
                # PFF's analysts ranking a player against PFF's own projections.
                # The only signal available on the IDP side, where there is no ADP.
                r.flag = f"PFFRK{pffrk - cons:+d}"

    rows.sort(key=lambda r: r.consensus, reverse=True)
    return rows, counts


def filter_pos(rows: list[BoardRow], position: str) -> list[BoardRow]:
    if not position:
        return rows
    want = position.strip().upper()
    return [r for r in rows if (r.state.pos or "").upper() == want]


def render(rows: list[BoardRow], header: str) -> str:
    tier_of = tiers([r.consensus for r in rows])
    out = [header,
           f"{'#':>3} {'POS':<4} {'PLAYER':<21} {'TM':<3} {'ESPN':>6} {'PFF':>6} "
           f"{'CONS':>6} {'ADP':>5} {'VAL':>4} {'BYE':>3} TIER FLAG"]
    for i, r in enumerate(rows):
        s = r.state
        pff = f"{r.pff_pts:>6.1f}" if r.pff_pts is not None else "     -"
        adp = f"{r.adp:>5.1f}" if r.adp is not None else "    -"
        val = f"{r.value:>+4d}" if r.value is not None else "   -"
        bye = f"{r.bye:>3}" if r.bye else "  -"
        line = (f"{i+1:>3} {s.pos:<4} {s.name[:21]:<21} {(s.team or '--'):<3} "
                f"{r.espn_pts:>6.1f} {pff} {r.consensus:>6.1f} {adp} {val} {bye} "
                f"T{tier_of[i]:<3}")
        extras = [x for x in (r.flag, s.status if s.status != "OK" else "") if x]
        return_line = line + (" " + " ".join(extras) if extras else "")
        out.append(return_line)
    return "\n".join(out)
