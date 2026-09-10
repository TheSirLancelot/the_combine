"""Shaping output for Discord.

Discord caps a message at 2000 characters and wraps code blocks rather than
scrolling them, so width and packing are correctness concerns, not cosmetics.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.discord_out import LIMIT, chunk, has_news, week_message
from combine.platforms import Matchup, ProGame, WeeklyPlayer

FUTURE = 4_000_000_000_000


def wp(name, pos, slot, proj=10.0, status="OK"):
    return WeeklyPlayer(player_id=name, name=name, team="KC", pos=pos, slot=slot,
                        eligible_slots=frozenset({pos, "BE"}), projected=proj,
                        status=status,
                        game=ProGame(opponent="SF", home=True, kickoff_ms=FUTURE))


def test_chunk_never_splits_a_block():
    """Half a table is worse than a second message, and a code fence broken
    across messages renders as garbage."""
    table = "```\n" + "x" * 500 + "\n```"
    out = chunk([table] * 5)
    assert all(part.count("```") % 2 == 0 for part in out)
    assert all(len(part) <= LIMIT for part in out)


def test_chunk_packs_rather_than_one_message_per_block():
    out = chunk(["a", "b", "c"])
    assert len(out) == 1 and out[0] == "a\n\nb\n\nc"


def test_chunk_drops_empties():
    assert chunk(["a", "", None or "", "b"]) == ["a\n\nb"]


def test_an_oversized_block_goes_alone_and_is_truncated():
    out = chunk(["small", "y" * (LIMIT + 200)])
    assert out[0] == "small"
    assert len(out[1]) == LIMIT


def test_week_message_stays_phone_readable():
    """Anything past about sixty characters wraps into mush on a phone, which is
    where this gets read."""
    lineup = [wp("Christian McCaffrey", "RB", "RB", 18.6),
              wp("A Very Long Player Name Indeed", "WR", "WR", 12.0),
              wp("Somebody Benched", "WR", "BE", 6.0)]
    m = Matchup(week=1, home_team="Me", away_team="Them", home_proj=110.0,
                away_proj=105.0, home_lineup=lineup, away_lineup=[], mine="home")
    messages = week_message(m, {"RB": 1, "WR": 1}, "Test League")
    assert len(messages) == 1
    widest = max(len(line) for line in messages[0].splitlines())
    assert widest <= 60, f"widest line is {widest}"


def test_week_message_flags_a_starter_who_cannot_play():
    lineup = [wp("Hurt Guy", "RB", "RB", 11.0, status="O")]
    m = Matchup(week=1, home_team="Me", away_team="Them", home_proj=11.0,
                away_proj=0.0, home_lineup=lineup, away_lineup=[], mine="home")
    text = "\n".join(week_message(m, {"RB": 1}, "T"))
    assert "Cannot play" in text and "Hurt Guy" in text


def test_no_opponent_means_no_invented_matchup_line():
    """The hand-entered league has no opponent, so a margin would be fiction."""
    m = Matchup(week=1, home_team="Mine", away_team="(none)", home_proj=140.0,
                away_proj=0.0, home_lineup=[wp("A", "RB", "RB")],
                away_lineup=[], mine="home")
    text = "\n".join(week_message(m, {"RB": 1}, "T"))
    assert "vs (none)" not in text
    assert "projected from your starters" in text


def test_silence_is_the_default():
    """A bot that posts "nothing to report" every week is a bot you mute, and
    then you miss the week it mattered."""
    assert has_news([], [], 0.0) is False
    assert has_news([], [], 0.04) is False        # inside the noise
    assert has_news([], ["a hurt starter"], 0.0) is True
    assert has_news(["a call"], [], 0.0) is True
    assert has_news([], [], 3.0) is True          # lineup is not optimal


# --- the on-demand notification ------------------------------------------

def test_notify_stays_quiet_when_there_is_nothing_to_say(monkeypatch, capsys):
    """The normal outcome. A check that posts every week gets muted."""
    from combine import bot

    monkeypatch.setattr(bot.config, "leagues", lambda: {"rcl": object()})
    monkeypatch.setattr(bot, "build_startsit", lambda lg, wk: (["a report"], False))
    sent = []
    monkeypatch.setattr(bot, "_post", lambda *a: sent.append(a))

    assert bot.notify(dry_run=False) == 0
    assert sent == []
    assert "nothing worth posting" in capsys.readouterr().out


def test_force_posts_anyway_and_labels_itself_a_test(monkeypatch, capsys):
    """Delivery cannot be proven on a quiet week without this, and a forced
    post must not read as a real recommendation."""
    from combine import bot

    monkeypatch.setattr(bot.config, "leagues", lambda: {"rcl": object()})
    monkeypatch.setattr(bot, "build_startsit", lambda lg, wk: (["a report"], False))
    assert bot.notify(force=True, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Manual test" in out and "a report" in out


def test_dry_run_never_sends(monkeypatch, capsys):
    from combine import bot

    monkeypatch.setattr(bot.config, "leagues", lambda: {"rcl": object()})
    monkeypatch.setattr(bot, "build_startsit", lambda lg, wk: (["real news"], True))
    sent = []
    monkeypatch.setattr(bot, "_post", lambda *a: sent.append(a))
    assert bot.notify(dry_run=True) == 0
    assert sent == []
    assert "would post" in capsys.readouterr().out


def test_one_league_failing_does_not_stop_the_others(monkeypatch, capsys):
    from combine import bot

    monkeypatch.setattr(bot.config, "leagues",
                        lambda: {"rcl": object(), "dmwd": object()})

    def build(league, week):
        if league == "rcl":
            raise RuntimeError("espn cookies expired")
        return (["dmwd news"], True)

    monkeypatch.setattr(bot, "build_startsit", build)
    assert bot.notify(dry_run=True) == 1        # non-zero: something failed
    assert "dmwd" in capsys.readouterr().out


def test_all_leagues_continues_past_one_that_fails(monkeypatch):
    """Expired ESPN cookies in one league must not hide the other two."""
    from combine import bot

    monkeypatch.setattr(bot.config, "leagues",
                        lambda: {"rcl": object(), "dmwd": object(), "work": object()})

    def build(league, week):
        if league == "dmwd":
            raise RuntimeError("401 unauthorized")
        return ([f"{league} report"], False)

    monkeypatch.setattr(bot, "build_startsit", build)
    out = "\n".join(bot.build_startsit_all())
    assert "rcl report" in out and "work report" in out
    assert "dmwd" in out and "401 unauthorized" in out


def test_all_leagues_reports_every_league(monkeypatch):
    from combine import bot

    monkeypatch.setattr(bot.config, "leagues", lambda: {"a": object(), "b": object()})
    monkeypatch.setattr(bot, "build_week", lambda lg, wk: [f"{lg} week"])
    assert bot.build_week_all() == ["a week", "b week"]


# --- waivers ----------------------------------------------------------------

def cand(**kw):
    from combine.pipeline.waivers import Candidate

    base = {"league": "rcl", "week": 3, "name": "D. Buckner", "pos": "DT",
            "team": "IND", "week_proj": 6.6, "season_proj": 90.0,
            "drop_name": "J. Bench", "drop_pos": "LB", "drop_season_proj": 70.0,
            "week_gain": 1.4, "displaces": "A. Starter"}
    return Candidate(**{**base, **kw})


def test_waivers_message_says_nothing_found_rather_than_going_silent():
    """An empty wire is an answer, not a missing report. Silence reads as a
    broken job."""
    from combine.discord_out import waivers_message

    out = waivers_message([], "The REAL Champions League", 3)
    assert len(out) == 1
    assert "Nobody on the wire" in out[0]
    assert "week 3" in out[0]


def test_waivers_message_reports_a_league_it_cannot_see():
    from combine.discord_out import waivers_message

    out = waivers_message([], "League of Degenerates", 3,
                          unavailable="no free agent pool without the Yahoo API")
    assert out == ["_League of Degenerates: no free agent pool without the Yahoo API_"]


def test_waivers_message_flags_a_candidate_the_correction_carries():
    """The whole point of the note: on ESPN's published number he is worse."""
    from combine.discord_out import waivers_message

    out = "\n".join(waivers_message([cand(correction=2.49, correction_n=40)],
                                    "RCL", 3))
    assert "⚠️" in out and "2.5" in out and "40 player-weeks" in out


def test_waivers_message_does_not_warn_when_the_correction_pushed_him_down():
    """A markdown he survived is the opposite of a caveat."""
    from combine.discord_out import waivers_message

    out = "\n".join(waivers_message([cand(correction=-1.2)], "RCL", 3))
    assert "⚠️" not in out
    assert "marked down 1.2" in out


def test_waivers_message_separates_the_week_from_the_season():
    from combine.discord_out import waivers_message

    cheap = "\n".join(waivers_message([cand(season_proj=90.0)], "RCL", 3))
    assert "gains 20 points" in cheap
    costly = "\n".join(waivers_message([cand(season_proj=40.0)], "RCL", 3))
    assert "costs 30 points" in costly and "pays later" in costly


def test_waivers_message_stays_under_the_discord_limit():
    from combine.discord_out import waivers_message

    many = [cand(name=f"Player {i}", correction=2.0, correction_n=40)
            for i in range(25)]
    for message in waivers_message(many, "RCL", 3):
        assert len(message) <= LIMIT


def test_waivers_only_interrupt_for_an_add_that_does_not_cost_the_season(monkeypatch):
    """A move that buys a week and pays for it later belongs in `/waivers` when
    you go looking, not in a Sunday ping."""
    from combine import bot

    found = [cand(season_proj=40.0)]                    # trades down
    monkeypatch.setattr(bot.config, "get_league",
                        lambda lg: type("C", (), {"platform": "espn", "name": "RCL"})())
    monkeypatch.setattr(bot, "_calibration", lambda lg: None)
    monkeypatch.setattr(bot, "_distribution", lambda: None)
    import combine.pipeline.waivers as w
    monkeypatch.setattr(w, "find", lambda *a, **k: found)
    monkeypatch.setattr(w, "season_values", lambda c: {})
    monkeypatch.setattr("combine.platforms.client_for",
                        lambda lg: type("C", (), {"week": 3})())

    messages, worth_telling = bot.build_waivers("rcl", 3)
    assert worth_telling is False
    assert "D. Buckner" in "\n".join(messages)

    found[:] = [cand(season_proj=90.0)]                 # gains season value too
    assert bot.build_waivers("rcl", 3)[1] is True


def test_waivers_for_a_league_without_a_free_agent_pool(monkeypatch):
    from combine import bot

    monkeypatch.setattr(
        bot.config, "get_league",
        lambda lg: type("C", (), {"platform": "manual", "name": "Degenerates"})())
    messages, worth_telling = bot.build_waivers("work", 3)
    assert worth_telling is False
    assert "Yahoo API" in messages[0]
