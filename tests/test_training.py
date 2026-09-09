"""The leakage rule, and the metric the model will be graded on.

Leakage is the failure that does not announce itself: a frame that quietly
includes week W's own stats produces a model that scores brilliantly in
backtest and is worthless on Sunday. These tests exist so that failure is loud.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine import db
from combine.pipeline.evaluate import CLOSE, pairwise, population, score
from combine.pipeline.training import _mean, _prior, build, defense_allowed


def test_prior_excludes_the_week_itself():
    rows = [{"week": w, "v": w} for w in (1, 2, 3, 4)]
    assert [r["week"] for r in _prior(rows, 3)] == [1, 2]
    assert [r["week"] for r in _prior(rows, 1)] == []


def test_prior_window_takes_the_most_recent():
    rows = [{"week": w} for w in (1, 2, 3, 4, 5)]
    assert [r["week"] for r in _prior(rows, 5, window=2)] == [3, 4]


def test_mean_ignores_missing_rather_than_treating_them_as_zero():
    assert _mean([{"v": 10}, {"v": None}, {"v": 20}], "v") == 15
    assert _mean([{"v": None}], "v") is None


def test_defense_allowed_is_keyed_by_week_so_it_can_be_lagged():
    espn = [
        {"opponent": "@ KC", "pos": "WR", "week": 1, "actual": 10.0, "played": 1},
        {"opponent": "vs KC", "pos": "WR", "week": 2, "actual": 20.0, "played": 1},
        {"opponent": "@ KC", "pos": "WR", "week": 2, "actual": 0.0, "played": 0},
    ]
    allowed = defense_allowed(espn)
    assert allowed[("KC", "WR", 1)] == 10.0
    assert allowed[("KC", "WR", 2)] == 20.0    # the unplayed row is excluded


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.ensure_schema(path)
    c = db.connect(path)
    yield c
    c.close()


def add_espn(conn, week, espn_id, proj, actual, pos="WR", league="x", played=1):
    conn.execute(
        "INSERT OR REPLACE INTO espn_player_week (league, season, week, espn_id,"
        " fantasy_team, name, pos, slot, started, team, opponent, is_home,"
        " projected, actual, played, status, pulled_at)"
        " VALUES (?,2025,?,?,'T','P',?,?,1,'KC','@ SF',0,?,?,?,'OK','now')",
        (league, week, espn_id, pos, pos, proj, actual, played))
    conn.commit()


def add_pff(conn, week, pff_id, **stats):
    conn.execute(
        "INSERT OR REPLACE INTO pff_player_week (season, week, pff_id, area,"
        " player, team, position, stats, pulled_at)"
        " VALUES (2025,?,?, 'receiving','P','KC','WR',?, 'now')",
        (week, pff_id, json.dumps({"routes": 0, "targets": 0, **stats})))
    conn.commit()


def test_features_never_see_the_week_being_predicted(conn):
    """The week-3 row must be built from weeks 1 and 2 only. If week 3's own
    60 targets leak in, this is the test that catches it."""
    for wk, targets in ((1, 4), (2, 6), (3, 60)):
        add_espn(conn, wk, "p1", proj=10.0, actual=12.0)
        add_pff(conn, wk, 900, routes=30, targets=targets)
    df = build(conn, 2025, ids={"p1": 900})
    wk3 = df[df.week == 3].iloc[0]
    assert wk3["targets_r"] == 5.0            # mean of 4 and 6, not 60
    assert wk3["games_r"] == 2


def test_the_first_week_has_no_history_and_says_so(conn):
    add_espn(conn, 1, "p1", proj=10.0, actual=12.0)
    add_pff(conn, 1, 900, routes=30, targets=8)
    row = build(conn, 2025, ids={"p1": 900}).iloc[0]
    assert row["prior_weeks"] == 0
    assert bool(row["thin_history"]) is True
    assert pd.isna(row["targets_r"])


def test_residual_is_the_target_not_the_raw_score(conn):
    add_espn(conn, 1, "p1", proj=10.0, actual=13.5)
    row = build(conn, 2025, ids={}).iloc[0]
    assert row["residual"] == pytest.approx(3.5)
    assert not row["has_pff"]


def test_unplayed_rows_are_dropped(conn):
    add_espn(conn, 1, "p1", proj=10.0, actual=0.0, played=0)
    assert build(conn, 2025, ids={}).empty


def test_population_drops_rows_espn_projected_at_zero():
    """A zero projection is ESPN saying there was no decision here."""
    df = pd.DataFrame({"espn_proj": [0.0, 8.0], "actual": [0.0, 9.0]})
    assert len(population(df)) == 1


def test_pairwise_counts_ordering_not_closeness():
    """A prediction can be far off and still order the pair correctly, which is
    the whole reason this metric exists alongside MAE."""
    df = pd.DataFrame({
        "league": ["x"] * 2, "week": [1] * 2, "family": ["pass-catcher"] * 2,
        "pred": [100.0, 50.0], "actual": [12.0, 4.0],
    })
    acc, _, _ = pairwise(df, "pred")
    assert acc == 1.0


def test_pairwise_close_bucket_uses_the_prediction_gap():
    rows = pd.DataFrame({
        "league": ["x"] * 3, "week": [1] * 3, "family": ["rb"] * 3,
        "pred": [10.0, 11.0, 30.0], "actual": [5.0, 9.0, 20.0],
    })
    _, _, n_close = pairwise(rows, "pred")
    assert n_close == 1          # only the 10 vs 11 pair is within CLOSE


def test_pairwise_ignores_pairs_that_tie():
    df = pd.DataFrame({
        "league": ["x"] * 2, "week": [1] * 2, "family": ["rb"] * 2,
        "pred": [10.0, 10.0], "actual": [5.0, 9.0],
    })
    assert pairwise(df, "pred") == (0.0, 0.0, 0)


def test_score_reports_the_sample_it_used():
    df = pd.DataFrame({
        "league": ["x"] * 3, "week": [1] * 3, "family": ["rb"] * 3,
        "espn_proj": [10.0, 12.0, None], "actual": [8.0, 14.0, 5.0],
    })
    s = score(df, "espn_proj", "espn")
    assert s.n == 2 and s.mae == pytest.approx(2.0)


def test_close_threshold_is_where_the_decisions_are():
    assert CLOSE == 3.0


def test_flip_test_measures_only_the_disagreements():
    """Two close pairs. The model agrees on one and flips the other, and only
    the flipped one should count toward flip accuracy."""
    from combine.pipeline.evaluate import flip_test
    df = pd.DataFrame({
        "league": ["x"] * 4, "week": [1, 1, 2, 2], "family": ["rb"] * 4,
        "espn_proj": [10.0, 9.0, 10.0, 9.0],
        # week 1: model keeps ESPN's order. week 2: model flips it, and is right.
        "model_pred": [11.0, 8.0, 8.0, 11.0],
        "actual": [15.0, 5.0, 5.0, 15.0],
    })
    f = flip_test(df, "model_pred")
    assert f["close_pairs"] == 2
    assert f["flips"] == 1
    assert f["flip_acc"] == 1.0
    assert f["base_acc"] == 0.5      # espn got week 1 right and week 2 wrong


def test_flip_test_ignores_pairs_the_projection_already_separates():
    from combine.pipeline.evaluate import flip_test
    df = pd.DataFrame({
        "league": ["x"] * 2, "week": [1] * 2, "family": ["rb"] * 2,
        "espn_proj": [20.0, 5.0], "model_pred": [5.0, 20.0], "actual": [1.0, 30.0],
    })
    assert flip_test(df, "model_pred")["close_pairs"] == 0


def test_model_declines_to_speak_without_history():
    """A prediction built from median-imputed features is a guess dressed as a
    number, so those rows must come back as ESPN untouched."""
    from combine.pipeline.model import apply
    frame = pd.DataFrame({"espn_proj": [10.0, 12.0], "prior_weeks": [0, 5]})

    class Stub:
        def predict(self, df):
            return pd.Series([5.0] * len(df)).to_numpy()

    out = apply(Stub(), frame)
    assert out.iloc[0] == 10.0       # no history, ESPN stands
    assert out.iloc[1] == 17.0


def test_no_model_means_espn_untouched():
    from combine.pipeline.model import apply
    frame = pd.DataFrame({"espn_proj": [10.0], "prior_weeks": [9]})
    assert apply(None, frame).iloc[0] == 10.0
