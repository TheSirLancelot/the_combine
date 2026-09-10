"""How much was actually sitting on the waiver wire, measured on a past season.

WHAT THIS CAN AND CANNOT ESTABLISH, because the difference matters.

It CAN measure the prize exactly. For every week of 2025 it reconstructs who was
unrostered in a league, looks up what those players really scored under that
league's own rules, and asks what the single best legal addition would have been
worth with perfect hindsight. That bounds the opportunity: if flawless waiver
play was worth two points a week, no method is worth building.

It CANNOT replay what `waivers.find` would have recommended. ESPN does not retain
historical weekly PROJECTIONS: for any player, in any past week, `projected_points`
comes back None, verified across a batch of 60. Projections for rostered players
survive only because we stored them week by week as the season ran. So the
method's ranking cannot be scored against the past, and this is not the flip test
the residual model got. The honest position is that this measures the ceiling, and
the optimizer backtest is the only evidence that projection-ranked lineup
decisions convert.

Availability is exact rather than estimated: every roster in the league was stored
for every week, so a player absent from `espn_player_week` for that league-week
was rostered by nobody, which is what available means.

The pool is limited to players in the id crosswalk, which is everyone rostered in
either league across 2025 plus the current pool. That biases UPWARD: it excludes
the genuinely unrosterable and includes players somebody eventually wanted. So
treat the number as a generous ceiling, not an estimate.

TODO: kickers and team defenses are in this pool if they were rostered, but their
availability week to week is dominated by streaming, so read those separately.
"""

from __future__ import annotations

from dataclasses import dataclass

from .optimize import best_lineup

# Player-weeks where the game never happened carry a zero that means "did not
# play", not "played badly". Including them makes the wire look worse than it was.
MIN_ACTUAL_WEEKS = 1

# Trailing weeks used to judge form, and how many prior weeks are needed before
# form means anything. Matched to the RECENT window used elsewhere in the repo.
FORM_WINDOW = 3
FORM_MIN_WEEKS = 2
# Only the top-ranked eligible candidate is taken, because this is meant to model
# a rule someone would follow, not a search over the pool.
FORM_CANDIDATES = 40


def _form(by_week: dict[int, float], week: int) -> float | None:
    """Mean actual over the weeks before `week`. None when too thin to judge.

    The leakage rule again: form for week W uses only weeks before W.
    """
    prior = [points for wk, points in by_week.items() if wk < week]
    if len(prior) < FORM_MIN_WEEKS:
        return None
    recent = [points for wk, points in sorted(by_week.items())
              if wk < week][-FORM_WINDOW:]
    return sum(recent) / len(recent)


@dataclass(frozen=True)
class WireWeek:
    league: str
    week: int
    fielded: float           # what my starters actually scored
    best_lineup_value: float  # best legal lineup from my roster, hindsight
    with_best_add: float     # same, plus the single best legal addition
    add_name: str | None
    add_pos: str | None
    drop_name: str | None
    opponent_actual: float
    # What an ex-ante rule actually got: pick the available player with the best
    # trailing form, no foreknowledge. This is the number that means something.
    with_form_add: float = 0.0
    form_add_name: str | None = None

    @property
    def wire_value(self) -> float:
        """What the best possible add was worth, over an already perfect lineup.
        Isolated from lineup mistakes on purpose: both sides use hindsight."""
        return self.with_best_add - self.best_lineup_value

    @property
    def form_value(self) -> float:
        """What the trailing-form pick was worth. Can be negative: an ex-ante
        rule is allowed to be wrong, and that is the point of measuring it."""
        return self.with_form_add - self.best_lineup_value

    @property
    def form_flipped(self) -> bool:
        return (self.best_lineup_value <= self.opponent_actual
                < self.with_form_add)

    @property
    def flipped(self) -> bool:
        """The add turned a loss into a win."""
        return (self.best_lineup_value <= self.opponent_actual
                < self.with_best_add)


def _slot_list(rows) -> list[str]:
    return [r["slot"] for r in rows if r["started"]]


def _candidate(espn_id: str, name: str, pos: str, eligible: set[str],
               actual: float) -> dict:
    return {"espn_id": espn_id, "name": name, "pos": pos,
            "eligible": eligible, "proj": actual, "playable": True}


def form_ranking(actuals: dict[str, dict[int, float]],
                 weeks: range) -> dict[int, list[tuple[str, float]]]:
    """{week: [(espn id, trailing form)]}, best form first.

    Hoisted out of the per-team loop because form depends on the player and the
    week, not on whose roster we are asking about. Recomputing it per team made a
    twelve-team sweep twelve times slower than it needed to be.
    """
    out: dict[int, list[tuple[str, float]]] = {}
    for week in weeks:
        scored = []
        for espn_id, by_week in actuals.items():
            form = _form(by_week, week)
            if form is not None:
                scored.append((espn_id, form))
        scored.sort(key=lambda pair: -pair[1])
        out[week] = scored[:FORM_CANDIDATES]
    return out


def run(conn, client, season: int, my_team: str,
        actuals: dict[str, dict[int, float]],
        eligibility: dict[str, set[str]],
        weeks: range | None = None, ceiling: bool = True,
        ranking: dict[int, list[tuple[str, float]]] | None = None
        ) -> list[WireWeek]:
    """One row per week. `actuals` and `eligibility` come from `fetch`.

    `ceiling=False` skips the hindsight best-possible-add search, which is the
    expensive half and the less meaningful number. Off, this runs fast enough to
    sweep every team in the league, which is what makes the ex-ante figure
    measurable rather than one team's noise.
    """
    out: list[WireWeek] = []
    league = client.slug
    span = weeks or range(1, 19)

    for week in span:
        mine = [dict(r) for r in conn.execute(
            "SELECT * FROM espn_player_week WHERE league=? AND season=? AND week=?"
            " AND fantasy_team=?", (league, season, week, my_team))]
        if not mine:
            continue
        slots = _slot_list(mine)
        if not slots:
            continue

        rostered = {r["espn_id"] for r in conn.execute(
            "SELECT DISTINCT espn_id FROM espn_player_week"
            " WHERE league=? AND season=? AND week=?", (league, season, week))}

        versus = next((r["versus"] for r in mine if r["versus"]), "")
        opponent = conn.execute(
            "SELECT COALESCE(SUM(actual), 0) s FROM espn_player_week"
            " WHERE league=? AND season=? AND week=? AND fantasy_team=? AND started=1",
            (league, season, week, versus)).fetchone()["s"]

        roster = [_candidate(r["espn_id"], r["name"], r["pos"],
                             set((r["eligible"] or "").split(",")) - {""},
                             float(r["actual"] or 0.0)) for r in mine]
        fielded = sum(float(r["actual"] or 0.0) for r in mine if r["started"])
        # One solve, reused. This was two separate calls, and the second sat
        # inside a comprehension's condition so it re-ran once per roster player:
        # 21 solves a week instead of 1, and an eleven-fold slowdown that
        # profiling found in a minute and reading the code had not.
        chosen = best_lineup(roster, slots, key=lambda c: c["proj"])
        chosen_ids = {c["espn_id"] for c in chosen}
        base = sum(c["proj"] for c in chosen)

        # Everyone the crosswalk knows who nobody in this league rostered.
        pool = []
        for espn_id, by_week in actuals.items():
            if espn_id in rostered or week not in by_week:
                continue
            slots_for = eligibility.get(espn_id)
            if not slots_for:
                continue
            pool.append(_candidate(espn_id, espn_id, "", slots_for,
                                   float(by_week[week])))

        best_value, best_add, best_drop = base, None, None
        # Under hindsight the cheapest body to give up is unambiguous, so there is
        # one drop to consider rather than several.
        drop = min(roster, key=lambda c: c["proj"])
        kept = [c for c in roster if c["espn_id"] != drop["espn_id"]]

        # The optimizer is the expensive part, so it only runs for candidates who
        # could possibly help: a player cannot improve the lineup unless he beat
        # the weakest starter he was eligible to replace. Without this filter the
        # whole pool goes through the solver every week and the run never
        # finishes.
        started = [c for c in roster if c["espn_id"] in chosen_ids]
        floor: dict[str, float] = {}
        for slot in set(slots):
            eligible_now = [c["proj"] for c in started if slot in c["eligible"]]
            floor[slot] = min(eligible_now) if eligible_now else 0.0

        for candidate in (pool if ceiling else []):
            if candidate["proj"] <= 0:
                continue
            if not any(candidate["proj"] > floor.get(slot, 1e9)
                       for slot in candidate["eligible"]):
                continue
            trial = [*kept, candidate]
            value = sum(c["proj"] for c in best_lineup(
                trial, slots, key=lambda c: c["proj"]))
            if value > best_value:
                best_value, best_add, best_drop = value, candidate, drop

        # Now the same thing WITHOUT foreknowledge. Rank the pool by trailing form
        # from weeks strictly before this one, take the best eligible one, and see
        # what he actually did. Projections would be the better signal and are not
        # retained, so this is a floor on what a projection-driven method could
        # manage rather than an estimate of it.
        form_value, form_add = base, None
        by_id = {c["espn_id"]: c for c in pool}
        order = (ranking or {}).get(week)
        if order is None:
            order = form_ranking(actuals, range(week, week + 1))[week]
        for espn_id, _form_value in order:
            candidate = by_id.get(espn_id)
            if candidate is None:
                continue      # rostered this week, so not available
            trial = [*kept, candidate]
            value = sum(c["proj"] for c in best_lineup(
                trial, slots, key=lambda c: c["proj"]))
            # Whether he helped is judged on ACTUALS, but he was CHOSEN on form.
            if value > form_value:
                form_value, form_add = value, candidate
            break        # one pick per week: this is a rule, not a search

        out.append(WireWeek(
            league=league, week=week, fielded=fielded, best_lineup_value=base,
            with_best_add=best_value,
            add_name=best_add["espn_id"] if best_add else None,
            add_pos=best_add["pos"] if best_add else None,
            drop_name=best_drop["name"] if best_drop else None,
            opponent_actual=float(opponent or 0.0),
            with_form_add=form_value,
            form_add_name=form_add["espn_id"] if form_add else None,
        ))
    return out


def fetch(client, conn, season: int
          ) -> tuple[dict[str, dict[int, float]], dict[str, set[str]]]:
    """({espn id: {week: actual}}, {espn id: eligible slots}) for the whole pool.

    Batched: ESPN takes a list of player ids and answers with every week at once,
    so this is a couple of requests rather than hundreds. The actuals come back
    already scored under THIS league's rules, which is the reason to ask ESPN
    rather than rescore PFF stat lines by hand.
    """
    ids = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT espn_id FROM espn_player_week WHERE season=?", (season,))
        if str(r[0]).isdigit()]
    actuals: dict[str, dict[int, float]] = {}
    eligibility: dict[str, set[str]] = {}
    names: dict[str, str] = {}

    for start in range(0, len(ids), 200):
        chunk = ids[start:start + 200]
        found = client.league.player_info(playerId=chunk)
        for player in (found if isinstance(found, list) else [found]):
            if player is None:
                continue
            espn_id = str(getattr(player, "playerId", ""))
            weeks = {
                week: float(stats.get("points") or 0.0)
                for week, stats in (getattr(player, "stats", {}) or {}).items()
                if week and stats.get("points") is not None
            }
            if len(weeks) < MIN_ACTUAL_WEEKS:
                continue
            actuals[espn_id] = weeks
            eligibility[espn_id] = set(getattr(player, "eligibleSlots", ()) or ())
            names[espn_id] = getattr(player, "name", espn_id)
    # Names are carried on the eligibility map's twin so callers can label rows.
    fetch.names = names
    return actuals, eligibility


def render(rows: list[WireWeek], league_name: str,
           names: dict[str, str] | None = None) -> str:
    if not rows:
        return f"{league_name}: nothing stored to measure"
    names = names or {}
    weeks = len(rows)
    total = sum(r.wire_value for r in rows)
    flips = sum(1 for r in rows if r.flipped)
    helped = [r for r in rows if r.wire_value > 0.05]

    form_total = sum(r.form_value for r in rows)
    form_flips = sum(1 for r in rows if r.form_flipped)
    form_right = sum(1 for r in rows if r.form_value > 0.05)

    out = [f"{league_name}: {weeks} weeks",
           "  HINDSIGHT CEILING (picks the best available player knowing the result)",
           f"    worth {total:.0f} points, {total / weeks:.1f} a week, "
           f"changed the result in {flips} of {weeks} weeks",
           f"    a useful add existed in {len(helped)} of {weeks} weeks",
           "  EX-ANTE RULE (best trailing form, no foreknowledge)",
           f"    worth {form_total:.0f} points, {form_total / weeks:+.1f} a week, "
           f"changed the result in {form_flips} of {weeks} weeks",
           f"    helped in {form_right} of {weeks} weeks"]
    if helped:
        biggest = max(helped, key=lambda r: r.wire_value)
        who = names.get(biggest.add_name or "", biggest.add_name or "?")
        out.append(f"  biggest single week: +{biggest.wire_value:.1f} in week "
                   f"{biggest.week} ({who})")
    out.append("  the ceiling mostly measures pool size: the best of 300 players "
               "is high by\n  arithmetic. The ex-ante line is the one that means "
               "something, and it is a\n  FLOOR on a projection-driven method, "
               "since ESPN keeps no historical\n  projections and trailing form "
               "is a weaker signal than a projection.")
    return "\n".join(out)
