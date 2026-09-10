"""What a player actually does, from PFF, joined by player_id.

This is the layer that makes a start/sit call more than a projection lookup.
A projection is one number with no explanation; usage says whether it rests on
a role the player genuinely has. Two receivers projected for 12 points are not
the same bet if one runs 34 routes a game and the other runs 11 and needs a
touchdown.

Deliberately NOT blended into any projection. Same rule as opinion and news on
the draft side: these are grades and rates on a scale that has nothing to do
with fantasy points, and folding them into a points number would corrupt a
figure that currently means something. They sit beside it.

Before the season starts these are last season's numbers, which the API client
resolves and reports. Read them as a prior, not as evidence about this week.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Which facet reports feed which ESPN positions. A player can appear in several
# (a receiving back is in both rushing and receiving), so all of them are kept
# and the ESPN position decides which one is rendered.
AREAS = ("passing", "rushing", "receiving", "defense")

IDP = frozenset({"LB", "DL", "DE", "DT", "DB", "CB", "S", "DP", "EDGE"})
SMALL_SAMPLE = 8


def family(pos: str) -> str:
    """Which usage currency a position is paid in.

    Opportunities only compare inside a family. A tight end's 5.6 targets a
    game and a running back's 15.9 touches are different units, and subtracting
    them produces a confident-looking number that means nothing. Same class of
    mistake as ranking the draft board on raw points against ADP.
    """
    pos = (pos or "").upper()
    if pos == "QB":
        return "qb"
    if pos == "RB":
        return "rb"
    if pos in ("WR", "TE"):
        return "pass-catcher"
    if pos in IDP:
        return "idp"
    return "other"


def _num(row: dict | None, key: str) -> float | None:
    if not row:
        return None
    v = row.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _per_game(value: float | None, games: int) -> float | None:
    if value is None or not games:
        return None
    return value / games


@dataclass(frozen=True)
class Usage:
    """One player's charted season, across every report he appears in."""
    pff_id: int
    name: str
    season: int
    rows: dict[str, dict] = field(default_factory=dict)

    @property
    def games(self) -> int:
        return max((int(r.get("player_game_count") or 0) for r in self.rows.values()),
                   default=0)

    def opportunities(self, pos: str) -> float | None:
        """Per-game touches, targets, dropbacks or snaps, whichever this
        position scores off. The single most predictive thing PFF gives us,
        and the number a projection is implicitly betting on."""
        g = self.games
        pos = pos.upper()
        if pos == "QB":
            return _per_game(_num(self.rows.get("passing"), "dropbacks"), g)
        if pos in IDP:
            return _per_game(_num(self.rows.get("defense"), "snap_counts_defense"), g)
        if pos == "RB":
            carries = _num(self.rows.get("rushing"), "attempts") or 0
            catches = _num(self.rows.get("rushing"), "receptions") or 0
            return _per_game(carries + catches, g) or None
        return _per_game(_num(self.rows.get("receiving"), "targets"), g)

    def line(self, pos: str) -> str:
        """A compact, position-appropriate role line. Volume first, because
        volume is what projects; efficiency second, because it explains."""
        g = self.games
        pos = pos.upper()
        if pos == "QB":
            r = self.rows.get("passing")
            return self._fmt([
                ("db/g", _per_game(_num(r, "dropbacks"), g), 1),
                ("ypa", _num(r, "ypa"), 1),
                ("btt%", _num(r, "btt_rate"), 1),
                ("twp%", _num(r, "twp_rate"), 1),
                ("grade", _num(r, "grades_pass"), 1),
            ])
        if pos in IDP:
            r = self.rows.get("defense")
            stops = (_num(r, "tackles") or 0) + (_num(r, "assists") or 0)
            return self._fmt([
                ("snap/g", _per_game(_num(r, "snap_counts_defense"), g), 1),
                ("tkl/g", _per_game(stops, g) or None, 1),
                ("sacks", _num(r, "sacks"), 1),
                ("press", _num(r, "total_pressures"), 0),
                ("grade", _num(r, "grades_defense"), 1),
            ])
        if pos == "RB":
            run, rec = self.rows.get("rushing"), self.rows.get("receiving")
            touches = (_num(run, "attempts") or 0) + (_num(run, "receptions") or 0)
            return self._fmt([
                ("touch/g", _per_game(touches, g) or None, 1),
                ("route/g", _per_game(_num(rec, "routes"), g), 1),
                ("yco/att", _num(run, "yco_attempt"), 2),
                ("brk%", _num(run, "breakaway_percent"), 1),
                ("grade", _num(run, "grades_offense"), 1),
            ])
        r = self.rows.get("receiving")
        return self._fmt([
            ("route/g", _per_game(_num(r, "routes"), g), 1),
            ("rt%", _num(r, "route_rate"), 1),
            ("tgt/g", _per_game(_num(r, "targets"), g), 1),
            ("yprr", _num(r, "yprr"), 2),
            ("adot", _num(r, "avg_depth_of_target"), 1),
            ("grade", _num(r, "grades_pass_route"), 1),
        ])

    @staticmethod
    def _fmt(parts) -> str:
        return "  ".join(f"{label} {value:.{dp}f}"
                         for label, value, dp in parts if value is not None)

    def caveats(self, in_season: bool) -> list[str]:
        out = []
        if not in_season:
            out.append(f"{self.season} numbers, a prior and not evidence about this week")
        if 0 < self.games < SMALL_SAMPLE:
            out.append(f"only {self.games} games charted")
        if not self.rows:
            out.append("no PFF data")
        return out


def load(api, season: int | None = None, areas=AREAS) -> dict[int, Usage]:
    """{pff player_id: Usage}. One cached request per area."""
    if season is None:
        season = api.season_state().stats_season
    merged: dict[int, Usage] = {}
    for area in areas:
        for row in api.facet(area, "summary", season=season):
            pid = row.get("player_id")
            if not pid:
                continue
            pid = int(pid)
            cur = merged.get(pid)
            if cur is None:
                cur = Usage(pff_id=pid, name=row.get("player") or "?", season=season)
                merged[pid] = cur
            cur.rows[area] = row
    return merged


def for_espn(usage: dict[int, Usage], ids: dict[str, int], espn_id: str) -> Usage | None:
    """Usage for an ESPN player, via the stored id crosswalk. None when he was
    never resolved (a D/ST, or someone PFF has not charted)."""
    pff_id = ids.get(str(espn_id))
    return usage.get(pff_id) if pff_id else None


# What every token in a role line means. Lives here, beside the code that
# renders those lines, so the explanation cannot drift from the output. The CLI
# prints it via `combine glossary`; the app shows it under the week tables.
GLOSSARY = (
    ("Pass catchers (WR, TE)", (
        ("route/g", "routes run per game. The opportunity everything else "
                    "multiplies against"),
        ("rt%", "route participation: share of his team's pass plays he ran a "
                "route on. Under about 70% is a part-time role"),
        ("tgt/g", "targets per game"),
        ("yprr", "yards per route run, the efficiency number most predictive of "
                 "receiving production. 2.0+ is very good, under 1.2 is poor"),
        ("adot", "average depth of target in yards. High is boom or bust, low is "
                 "a volume floor"),
        ("grade", "PFF receiving grade, 0-100. About 60 is average, 85+ elite"),
    )),
    ("Backs (RB)", (
        ("touch/g", "carries plus receptions per game"),
        ("route/g", "routes run per game. This is the passing-game role, and it "
                    "separates a back who scores in PPR from one who does not"),
        ("yco/att", "yards after contact per attempt, which credits the back "
                    "rather than his line"),
        ("brk%", "breakaway rate: share of his rushing yards on runs of 15+. "
                 "High means he needs a long run to hit his projection"),
        ("grade", "PFF offensive grade, 0-100"),
    )),
    ("Quarterbacks", (
        ("db/g", "dropbacks per game, the volume number"),
        ("ypa", "yards per attempt"),
        ("btt%", "big-time throw rate: share of throws that were high value and "
                 "high difficulty. This is the upside"),
        ("twp%", "turnover-worthy play rate, the downside. It counts drops that "
                 "should have been interceptions"),
        ("grade", "PFF passing grade, 0-100"),
    )),
    ("Defenders (IDP)", (
        ("snap/g", "defensive snaps per game. In an IDP league this is most of "
                   "the story"),
        ("tkl/g", "tackles plus assists per game"),
        ("sacks", "season sacks"),
        ("press", "total pressures: sacks, hits and hurries. Predicts future "
                  "sacks far better than sacks themselves do"),
        ("grade", "PFF defensive grade, 0-100"),
    )),
)

OUTCOME_GLOSSARY = (
    ("floor / ceiling", "the 10th and 90th percentile outcomes for players at "
                        "the same position and projection level last season"),
    ("boom", "how often those players scored 20+"),
    ("bust", "how often they came in under half their projection"),
)
