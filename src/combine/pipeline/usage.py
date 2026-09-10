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

# How many regular-season games this season needs before its numbers replace
# last season's, and how far into the season that rule starts applying.
#
# One game, from week 2. The reasoning is that ROLE is what these lines are for,
# and role is the half that stabilises immediately: route/g, touch/g and snap/g
# are meaningful from a player's first game, and they are also the half most
# likely to have changed over an offseason, which is exactly when last season
# stops being a good prior. The rates sharing those lines -- yprr, grade, brk%
# -- are close to noise at one game, and what carries that is the season/games
# tag on every line plus the SMALL_SAMPLE caveat, which will fire constantly
# through September. That is correct rather than annoying.
#
# The week 2 floor exists so the table does not half-switch in the middle of
# week 1, when three teams have played and the rest have not.
USAGE_MIN_GAMES = 1
USAGE_FROM_WEEK = 2


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

    @property
    def tag(self) -> str:
        """Which season these numbers are, and how much of it.

        On every line, not only the doubtful ones. Early in a year the table
        mixes seasons by design -- last season for most, this season for anyone
        with enough games -- and an untagged row leaves you guessing which you
        are looking at.
        """
        return f"{self.season} {self.games}g" if self.games else str(self.season)

    def line(self, pos: str) -> str:
        """A compact, position-appropriate role line. Volume first, because
        volume is what projects; efficiency second, because it explains.

        Tagged with the season it describes, since the table mixes them.
        """
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

    def _fmt(self, parts) -> str:
        body = "  ".join(f"{label} {value:.{dp}f}"
                         for label, value, dp in parts if value is not None)
        return f"{self.tag}  {body}" if body else ""

    def caveats(self, in_season: bool, season: int | None = None) -> list[str]:
        """`in_season` is kept for callers that only know that much.

        The real question is whether these numbers are from the season being
        played, which is not the same thing now that the table falls back to
        last season per player: in week 3 the calendar is in season and the row
        is still last year's.
        """
        out = []
        stale = self.season < season if season is not None else not in_season
        if stale:
            out.append(f"{self.season} numbers, a prior and not evidence about this week")
        if 0 < self.games < SMALL_SAMPLE:
            out.append(f"only {self.games} games charted")
        if not self.rows:
            out.append("no PFF data")
        return out


def _one_season(api, season: int, areas) -> dict[int, Usage]:
    """{pff player_id: Usage} for one season, REGULAR SEASON ONLY.

    Never a bare season total. PFF folds preseason and playoff snaps into those,
    which is not a rounding error: Drake Maye's 2025 total is 23 games and 770
    dropbacks against a regular season of 17 and 601, and his passing grade
    reads 75.2 instead of 87.8.
    """
    weeks = api.regular_weeks(season)
    if not weeks:
        return {}                      # preseason: no regular-season rows exist
    merged: dict[int, Usage] = {}
    for area in areas:
        for row in api.facet(area, "summary", season=season, week=weeks):
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


def load(api, season: int | None = None, areas=AREAS) -> dict[int, Usage]:
    """{pff player_id: Usage}, this season where it says anything, last season
    where it does not.

    Anyone who has played this season reads this season, from week 2 onward.
    Anyone who has not -- hurt, inactive, or the week not played yet -- keeps
    last season, because a real role from last year beats an empty cell and the
    tag says which one you are looking at.

    See USAGE_MIN_GAMES for why the bar is one game.
    """
    if season is not None:
        return _one_season(api, season, areas)

    state = api.season_state()
    prior = _one_season(api, state.season - 1, areas)
    if not state.in_season:
        return prior

    current = _one_season(api, state.season, areas)
    bar = USAGE_MIN_GAMES if state.week >= USAGE_FROM_WEEK else SMALL_SAMPLE
    merged = dict(prior)
    for pid, usage in current.items():
        if usage.games >= bar or pid not in merged:
            merged[pid] = usage
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
    ("Every role line", (
        ("2025 17g", (
            "the season these numbers describe and how many games are charted. "
            "Early in a year most rows are last season, because this season has "
            "too few games to read")),
    )),
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
