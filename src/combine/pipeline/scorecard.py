"""Did the advice work?

Everything this tool claims rests on a backtest of 2025: the optimizer's +3.5pp
win rate, the waiver wire's +2.50 points a week, the 0.25 threshold. All of it
was measured on a season that is over, and none of it has been checked against
a single live decision.

So every recommendation is written down when it is made, with the numbers as
advised, and scored once the week is final. Two rules make it honest.

It records what the TOOL said, not what William did. He may take a
recommendation or ignore it, and either way the question worth answering is
"would this have helped", which is what decides whether the advice is worth
following. That makes this counterfactual by design rather than by accident.

And the projection is stored at the moment of the call and never restated. ESPN
revises projections through the day, so scoring a Sunday recommendation against
Sunday-night numbers would quietly grade the tool against information it did not
have.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import db

KINDS = ("start", "optimal", "waiver")


@dataclass(frozen=True)
class Row:
    kind: str
    subject_id: str
    subject_name: str
    against_id: str
    against_name: str
    subject_proj: float
    against_proj: float
    edge: float
    bar: float | None = None


def record(league: str, season: int, week: int, rows: list[Row], conn=None) -> int:
    """Write recommendations, keeping the FIRST version of each.

    `DO NOTHING` rather than `REPLACE` on purpose: the honest record is what the
    tool said when it first said it. Re-running `/startsit` an hour later with
    nudged projections must not overwrite the call it is being graded on.
    """
    if not rows:
        return 0
    owned = conn is None
    conn = conn or db.connect()
    try:
        written = 0
        for r in rows:
            cur = conn.execute(
                "INSERT INTO recommendation (league, season, week, kind,"
                " subject_id, subject_name, against_id, against_name,"
                " subject_proj, against_proj, edge, bar, made_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT DO NOTHING",
                (league, season, week, r.kind, str(r.subject_id), r.subject_name,
                 str(r.against_id or ""), r.against_name or "",
                 r.subject_proj, r.against_proj, r.edge, r.bar, db.now()))
            written += cur.rowcount or 0
        conn.commit()
        return written
    finally:
        if owned:
            conn.close()


def from_startsit(calls, add=(), drop=()) -> list[Row]:
    """The two things start/sit asserts: swap these two, and this lineup is not
    optimal."""
    rows = [Row(kind="start", subject_id=c.bench.player_id,
                subject_name=c.bench.name, against_id=c.starter.player_id,
                against_name=c.starter.name, subject_proj=c.bench.projected,
                against_proj=c.starter.projected, edge=c.proj_edge,
                bar=getattr(c, "needed", None))
            for c in calls]
    # The optimizer moves a set, not a pair, so add and drop are zipped by rank
    # rather than being a claim about any particular pairing.
    for a, d in zip(add, drop, strict=False):
        rows.append(Row(kind="optimal", subject_id=a.player_id,
                        subject_name=a.name, against_id=d.player_id,
                        against_name=d.name, subject_proj=a.projected,
                        against_proj=d.projected,
                        edge=a.projected - d.projected))
    return rows


def from_waivers(candidates) -> list[Row]:
    """An add is graded against the man it would have displaced, not against the
    player dropped: displacing is what changes the score this week."""
    rows = []
    for c in candidates:
        rows.append(Row(kind="waiver",
                        subject_id=getattr(c, "player_id", "") or c.name,
                        subject_name=c.name,
                        against_id=c.displaces or c.drop_name,
                        against_name=c.displaces or c.drop_name,
                        subject_proj=c.week_proj, against_proj=None,
                        edge=c.week_gain))
    return rows


def unscored(conn, season: int, week: int | None = None) -> list[dict]:
    sql = ("SELECT * FROM recommendation WHERE season=? AND scored_at IS NULL"
           + ("" if week is None else " AND week=?"))
    args = (season,) if week is None else (season, week)
    return [dict(r) for r in conn.execute(sql, args)]


def _actuals(conn, league: str, season: int, week: int) -> dict[str, float]:
    return {str(r["espn_id"]): r["actual"] for r in conn.execute(
        "SELECT espn_id, actual FROM espn_player_week"
        " WHERE league=? AND season=? AND week=?", (league, season, week))}


def _started(conn, league: str, season: int, week: int) -> dict[str, bool]:
    """{espn id: did he actually start} for one league-week."""
    return {str(r["espn_id"]): bool(r["started"]) for r in conn.execute(
        "SELECT espn_id, started FROM espn_player_week"
        " WHERE league=? AND season=? AND week=?", (league, season, week))}


def _acted_on(league: str, week: int) -> dict[str, str]:
    """Adds that actually went through, for deciding what was taken."""
    try:
        from ..platforms import client_for
        from .waivers import executed_adds

        return executed_adds(client_for(league), week)
    except Exception:
        return {}


def _by_name(conn, league: str, season: int, week: int) -> dict[str, float]:
    """D/ST and free agents are recorded by name, because a pool player has no
    row in espn_player_week to carry an id."""
    return {str(r["name"]): r["actual"] for r in conn.execute(
        "SELECT name, actual FROM espn_player_week"
        " WHERE league=? AND season=? AND week=?", (league, season, week))}


def _from_espn(league: str, season: int, week: int, ids: set[str]
               ) -> dict[str, float]:
    """Actuals for players NOT on anyone's roster, straight from ESPN.

    `espn_player_week` only holds players somebody rostered, so a waiver
    recommendation for a player nobody picked up has no row there and could
    never be graded. That is the common case, not the rare one: the whole point
    of the recommendation is that he was available.
    """
    numeric = [int(i) for i in ids if str(i).isdigit()]
    if not numeric:
        return {}
    try:
        from ..platforms import client_for

        client = client_for(league, season=season)
        found = client.league.player_info(playerId=numeric)
    except Exception:
        return {}
    out: dict[str, float] = {}
    for player in (found if isinstance(found, list) else [found]):
        if player is None:
            continue
        stats = (getattr(player, "stats", {}) or {}).get(week, {}) or {}
        points = stats.get("points")
        if points is not None:
            out[str(getattr(player, "playerId", ""))] = float(points)
    return out


def score(conn, season: int, week: int) -> tuple[int, int]:
    """Fill in what happened. Returns (scored, still unresolved).

    A row is scored only when BOTH sides resolve. Marking it done with one side
    missing was worse than leaving it open: it could never produce a result, it
    was excluded from every summary, and nothing would ever retry it. Four week
    1 rows sat in exactly that state, which is why the scorecard read "nothing
    scored yet" while claiming four rows were recorded.

    A recommendation that still cannot be resolved stays open rather than
    scoring as zero. A zero is a real football outcome and inventing one would
    bias the record toward the tool looking worse than it was.
    """
    open_rows = unscored(conn, season, week)
    if not open_rows:
        return 0, 0
    scored = missing = 0

    # One ESPN call per league for everyone the local table cannot answer.
    wanted: dict[str, set[str]] = {}
    for row in open_rows:
        known = _actuals(conn, row["league"], season, week)
        for ident in (row["subject_id"], row["against_id"]):
            if ident and str(ident) not in known:
                wanted.setdefault(row["league"], set()).add(str(ident))
    from_espn = {lg: _from_espn(lg, season, week, ids)
                 for lg, ids in wanted.items()}
    # Which of these were actually acted on. Separate from whether the advice
    # was right: the scorecard grades the tool either way, and the gap between
    # the two is the number worth looking at.
    leagues = {row["league"] for row in open_rows}
    executed = {lg: _acted_on(lg, week) for lg in leagues}

    for row in open_rows:
        by_id = _actuals(conn, row["league"], season, week)
        by_name = _by_name(conn, row["league"], season, week)
        remote = from_espn.get(row["league"], {})

        def look(ident, name, by_id=by_id, by_name=by_name, remote=remote):
            if ident and str(ident) in by_id:
                return by_id[str(ident)]
            if ident and str(ident) in remote:
                return remote[str(ident)]
            return by_name.get(name)

        subject = look(row["subject_id"], row["subject_name"])
        against = look(row["against_id"], row["against_name"])
        if subject is None or against is None:
            missing += 1
            continue

        # A waiver call is taken if the add went through. A lineup call is
        # taken if the man we named actually started. Both are observable after
        # the fact and neither needs William to tell us anything.
        if row["kind"] == "waiver":
            taken = int(str(row["subject_id"]) in executed.get(row["league"], {}))
        else:
            started = _started(conn, row["league"], season, week)
            flag = started.get(str(row["subject_id"]))
            taken = None if flag is None else int(flag)

        conn.execute(
            "UPDATE recommendation SET subject_actual=?, against_actual=?,"
            " taken=?, scored_at=? WHERE league=? AND season=? AND week=?"
            " AND kind=? AND subject_id=? AND against_id=?",
            (subject, against, taken, db.now(), row["league"], season, week,
             row["kind"], row["subject_id"], row["against_id"]))
        scored += 1
    conn.commit()
    return scored, missing


def frame(conn, season: int):
    """Every scored recommendation, with the outcome worked out."""
    import pandas as pd

    df = pd.read_sql_query(
        "SELECT * FROM recommendation WHERE season=? ORDER BY week, league",
        conn, params=(season,))
    if df.empty:
        return df
    # Nullable dtypes on purpose. An unscored row is not False and not zero, and
    # a plain bool column cannot hold "we do not know yet" -- it would silently
    # become False, which reads as a wrong call rather than an open one.
    resolved = df["subject_actual"].notna() & df["against_actual"].notna()
    df["gain"] = (df["subject_actual"] - df["against_actual"]).astype("Float64")
    df["right"] = pd.Series(pd.NA, index=df.index, dtype="boolean")
    df.loc[resolved, "right"] = df.loc[resolved, "gain"] > 0
    df.loc[~resolved, "gain"] = pd.NA
    return df


def _line(label: str, cell) -> dict:
    return {
        "kind": label,
        "n": len(cell),
        "right": float(cell["right"].astype(float).mean()),
        "points": float(cell["gain"].sum()),
        "per_call": float(cell["gain"].mean()),
    }


def summary(df) -> list[dict]:
    """One line per kind, then the taken/not-taken split.

    The split is the point. "Was the advice right" and "did following it help"
    are different questions, and the gap between them is what says whether
    ignoring the tool costs anything.
    """
    if df is None or df.empty:
        return []
    done = df[df["gain"].notna()]
    if done.empty:
        return []
    out = [_line(kind, cell) for kind, cell in done.groupby("kind")]
    out.sort(key=lambda r: -r["n"])

    if "taken" in done.columns:
        acted = done[done["taken"] == 1]
        ignored = done[done["taken"] == 0]
        if len(acted):
            out.append(_line("— taken", acted))
        if len(ignored):
            out.append(_line("— not taken", ignored))
    return out + [_line("all", done)]


def render(df) -> str:
    rows = summary(df)
    if not rows:
        return ("Nothing scored yet. Recommendations are written down as they are\n"
                "made and scored after the week's games are final.")
    out = [f"{'KIND':<10}{'N':>5}{'RIGHT':>8}{'POINTS':>9}{'PER CALL':>10}"]
    for r in rows:
        out.append(f"{r['kind']:<10}{r['n']:>5}{r['right'] * 100:>7.0f}%"
                   f"{r['points']:>+9.1f}{r['per_call']:>+10.2f}")
    out.append("\nRIGHT is how often the recommended player outscored the one he\n"
               "would have replaced. POINTS is what following every call would\n"
               "have been worth.\n\nThe taken and not-taken rows answer different "
               "questions: whether the\nadvice was right, and whether following "
               "it helped. The gap between\nthem is what says whether ignoring "
               "the tool costs anything.")
    return "\n".join(out)
