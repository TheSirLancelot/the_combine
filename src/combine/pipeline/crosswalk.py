"""Resolve one source's players onto another's.

Two jobs, same matching rules:
  * ESPN <-> the hand-exported PFF projection CSVs, by name, because those
    exports carry no ids at all.
  * ESPN -> PFF's stable player_id, from the API. This is the one worth
    keeping: it is resolved once and stored, so start/sit and player
    comparison join on an id rather than re-matching names on every call.

Deliberately conservative. A wrong match is worse than no match, since it
silently attaches someone else's projection to a player you are about to draft.
Anything ambiguous goes to the unmatched report for a human to resolve in
config/crosswalk_overrides.csv.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..config import CONFIG_DIR, DATA_DIR

OVERRIDES = CONFIG_DIR / "crosswalk_overrides.csv"
# Derived, not hand-written, and rebuildable in one command, so it lives in
# data/ rather than config/.
PFF_IDS = DATA_DIR / "crosswalk_pff_ids.csv"

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
# ESPN writes "Texans D/ST", PFF writes "Texans DST". Same thing, and there are
# 32 of them, so collapse to one token rather than leaving it to fuzzy matching.
_DST = re.compile(r"\b(d\s*st|dst|def|defense|special teams)\b")
_PUNCT = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")

# PFF and ESPN disagree on several team codes. Canonicalize before comparing.
_TEAM_ALIAS = {
    "HST": "HOU", "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE",
    "WAS": "WSH", "LA": "LAR", "SD": "LAC", "OAK": "LV", "STL": "LAR",
}


def team_key(team: str | None) -> str:
    t = (team or "").strip().upper()
    return _TEAM_ALIAS.get(t, t)


def name_parts(name: str) -> tuple[str, str]:
    """(first initial, surname) off a normalized name."""
    bits = norm(name).split()
    if not bits:
        return "", ""
    return bits[0][:1], bits[-1]


FUZZ_MIN = 92      # rapidfuzz score to accept
FUZZ_MARGIN = 4    # best must beat runner-up by this much


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = _PUNCT.sub(" ", s.lower())
    s = _SUFFIX.sub(" ", s)
    s = _DST.sub(" dst", s)
    return _WS.sub(" ", s).strip()


def load_overrides() -> dict[str, str]:
    """espn player name (normalized) -> pff player name (verbatim). Empty pff
    name means 'deliberately no match, stop reporting it'."""
    if not OVERRIDES.exists():
        return {}
    with OVERRIDES.open(newline="") as fh:
        return {
            norm(r["espn_name"]): r["pff_name"].strip()
            for r in csv.DictReader(fh)
            if r.get("espn_name")
        }


def build_index(pff_rows) -> dict[str, list]:
    idx: dict[str, list] = {}
    for row in pff_rows:
        idx.setdefault(norm(row.name), []).append(row)
    return idx


def match(espn_name: str, espn_team: str | None, espn_pos: str | None,
          idx: dict[str, list], overrides: dict[str, str]):
    """Return (row, how) where how is exact|team|pos|fuzzy|override|None."""
    key = norm(espn_name)

    if key in overrides:
        target = overrides[key]
        if not target:
            return None, "ignored"
        for cands in idx.values():
            for row in cands:
                if row.name == target:
                    return row, "override"
        return None, "override-miss"

    cands = idx.get(key, [])
    if len(cands) == 1:
        return cands[0], "exact"
    if len(cands) > 1:
        by_team = [c for c in cands if espn_team and team_key(c.team) == team_key(espn_team)]
        if len(by_team) == 1:
            return by_team[0], "team"
        by_pos = [c for c in cands if espn_pos and c.pos.upper() == espn_pos.upper()]
        if len(by_pos) == 1:
            return by_pos[0], "pos"
        return None, "ambiguous"

    # PFF uses formal first names where ESPN uses nicknames (Kenneth/Kenny,
    # Chigoziem/Chig, Camryn/Cam). Surname + first initial + same NFL team is
    # deterministic and safe, where loosening the fuzzy threshold would not be.
    init, surname = name_parts(espn_name)
    if surname:
        near = [
            r for rows in idx.values() for r in rows
            if name_parts(r.name) == (init, surname)
            and team_key(r.team) == team_key(espn_team)
        ]
        if len(near) == 1:
            return near[0], "nickname"
        if len(near) > 1 and espn_pos:
            same_pos = [r for r in near if r.pos.upper() == espn_pos.upper()]
            if len(same_pos) == 1:
                return same_pos[0], "nickname"

    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        return None, "unmatched"

    hits = process.extract(key, list(idx), scorer=fuzz.WRatio, limit=2)
    if hits and hits[0][1] >= FUZZ_MIN and (len(hits) == 1 or hits[0][1] - hits[1][1] >= FUZZ_MARGIN):
        cands = idx[hits[0][0]]
        if len(cands) == 1:
            return cands[0], "fuzzy"
    return None, "unmatched"


def write_unmatched(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["espn_id", "espn_name", "espn_pos", "espn_team", "reason", "pff_name"],
            restval="",  # the CSV crosswalk report has no espn_id column
        )
        w.writeheader()
        w.writerows(rows)


# --- ESPN -> PFF player_id ------------------------------------------------

# Facet reports that between them name every player who can score fantasy
# points. defense/summary is slow (1456 rows, ~15s cold) but RCL is IDP, so it
# is not optional. There is deliberately no D/ST report: PFF has no team
# defense entity in these facets, so every D/ST comes back unmatched, which is
# correct rather than a failure.
DIRECTORY_REPORTS = (
    ("passing", "summary"),
    ("rushing", "summary"),
    ("receiving", "summary"),
    ("defense", "summary"),
    ("field_goal", "summary"),
)


@dataclass(frozen=True)
class PffPlayer:
    """A PFF player shaped for match(), which wants .name/.team/.pos."""
    name: str
    team: str
    pos: str
    pff_id: int


def directory(api, season: int | None = None,
              reports=DIRECTORY_REPORTS) -> list[PffPlayer]:
    """Every player PFF has charted, deduped by player_id.

    Built from facets rather than from /v1/players?name=, which is one request
    per player and a substring search on top.
    """
    seen: dict[int, PffPlayer] = {}
    for area, report in reports:
        for row in api.facet(area, report, season=season):
            pid = row.get("player_id")
            name = row.get("player")
            if not pid or not name:
                continue
            seen.setdefault(int(pid), PffPlayer(
                name=name,
                team=(row.get("team_name") or ""),
                pos=(row.get("position") or ""),
                pff_id=int(pid),
            ))
    return list(seen.values())


def resolve_ids(espn_players, pff_players: list[PffPlayer]
                ) -> tuple[list[dict], list[dict]]:
    """(matched rows, unmatched report).

    Same conservative rules as the CSV crosswalk: a wrong id is worse than no
    id, because it silently attaches another player's usage to the one you are
    deciding to start. Rows are returned rather than a bare dict so the stored
    file carries both names and can be eyeballed.
    """
    idx = build_index(pff_players)
    overrides = load_overrides()
    rows: list[dict] = []
    misses: list[dict] = []
    seen: set[str] = set()
    for p in espn_players:
        key = str(p.player_id)
        if key in seen:
            continue
        seen.add(key)
        hit, how = match(p.name, p.team, p.pos, idx, overrides)
        if hit is not None:
            rows.append({"espn_id": key, "espn_name": p.name, "espn_pos": p.pos,
                         "espn_team": p.team or "", "pff_id": hit.pff_id,
                         "pff_name": hit.name, "pff_pos": hit.pos,
                         "pff_team": hit.team, "how": how})
        elif how != "ignored":
            misses.append({"espn_id": key, "espn_name": p.name, "espn_pos": p.pos,
                           "espn_team": p.team or "", "reason": how, "pff_name": ""})
    return rows, misses


ID_FIELDS = ["espn_id", "espn_name", "espn_pos", "espn_team",
             "pff_id", "pff_name", "pff_pos", "pff_team", "how"]


def load_rows(path: Path = PFF_IDS) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def save_ids(rows: list[dict], path: Path = PFF_IDS, merge: bool = True) -> int:
    """Store the mapping. Returns the number of rows in the file afterwards.

    Merges by default. ESPN player ids are global rather than per league, so
    both leagues share one file, and resolving one league must not throw away
    what the other found. A fresh row wins over a stored one for the same
    player, since teams and statuses move.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    by_id = {r["espn_id"]: r for r in (load_rows(path) if merge else [])}
    for r in rows:
        by_id[str(r["espn_id"])] = {k: r.get(k, "") for k in ID_FIELDS}
    out = sorted(by_id.values(), key=lambda r: (r.get("espn_name") or ""))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ID_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    return len(out)


def load_ids(path: Path = PFF_IDS) -> dict[str, int]:
    """espn player_id -> pff player_id. Empty when it has not been built yet."""
    if not path.exists():
        return {}
    with path.open(newline="") as fh:
        return {r["espn_id"]: int(r["pff_id"]) for r in csv.DictReader(fh)
                if r.get("espn_id") and r.get("pff_id")}


def resolve_by_lookup(api, misses: list[dict]) -> tuple[list[dict], list[dict]]:
    """Second pass over the unmatched, using PFF's name lookup.

    The facet directory only contains players PFF has charted, so anyone
    without snaps last season is absent from it: rookies, mostly. They still
    have a player_id, and /v1/players finds it. One request per miss, cached
    for a week, so this is only sane as a second pass over the leftovers rather
    than as the primary path.

    Just as conservative as everything else here. The lookup is a substring
    search ("Josh Allen" also returns Josh Hines-Allen), so a hit has to match
    the full normalized name, and anything still ambiguous after the team and
    position tiebreaks is left unmatched.
    """
    got: list[dict] = []
    still: list[dict] = []
    for m in misses:
        want = norm(m["espn_name"])
        try:
            hits = api.players(m["espn_name"])
        except Exception:
            still.append(m)
            continue
        cands = []
        for h in hits:
            full = f"{h.get('first_name', '')} {h.get('last_name', '')}"
            if norm(full) == want:
                cands.append((h, full))
        if len(cands) > 1 and m.get("espn_team"):
            narrowed = [c for c in cands
                        if team_key((c[0].get("team") or {}).get("abbreviation"))
                        == team_key(m["espn_team"])]
            if narrowed:
                cands = narrowed
        if len(cands) > 1 and m.get("espn_pos"):
            narrowed = [c for c in cands
                        if (c[0].get("position") or "").upper() == m["espn_pos"].upper()]
            if len(narrowed) == 1:
                cands = narrowed
        how = "lookup"
        if not cands and m.get("espn_team"):
            # PFF uses formal first names where ESPN uses nicknames, and a
            # nickname does not substring-match the formal name (Riq / Tariq),
            # so the full-name query finds nothing. Re-ask by surname and
            # require surname + NFL team + position, which is deterministic.
            # This is the same rule the directory pass uses; it only fails
            # there because the directory carries last season's team.
            surname = name_parts(m["espn_name"])[1]
            if surname:
                try:
                    hits = api.players(surname)
                except Exception:
                    hits = []
                cands = [
                    (h, f"{h.get('first_name', '')} {h.get('last_name', '')}")
                    for h in hits
                    if norm(h.get("last_name", "")) == surname
                    and team_key((h.get("team") or {}).get("abbreviation"))
                    == team_key(m["espn_team"])
                    and (h.get("position") or "").upper() == (m.get("espn_pos") or "").upper()
                ]
                how = "lookup-surname"
        if len(cands) != 1:
            still.append({**m, "reason": "ambiguous-lookup" if cands else m["reason"]})
            continue
        hit, full = cands[0]
        got.append({"espn_id": m.get("espn_id", ""), "espn_name": m["espn_name"],
                    "espn_pos": m["espn_pos"], "espn_team": m["espn_team"],
                    "pff_id": int(hit["id"]), "pff_name": full,
                    "pff_pos": hit.get("position") or "",
                    "pff_team": (hit.get("team") or {}).get("abbreviation") or "",
                    "how": how})
    return got, still
