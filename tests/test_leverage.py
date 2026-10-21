import pytest

from league_manager.leverage import (
    game_swing,
    p_reach,
    playoff_cut_wins,
    team_strength,
    week_weights,
)
from league_manager.value import LeagueContext


def _ctx(*, week=5, wins=1, losses=3, ties=0) -> LeagueContext:
    return LeagueContext(
        current_week=week,
        season_end_week=17,
        regular_season_end=14,
        playoff_team_count=6,
        team_count=10,
        wins=wins,
        losses=losses,
        ties=ties,
    )


def _regular(ww):
    return [w for w in ww.weeks if w <= 14]


def _playoff(ww):
    return [w for w in ww.weeks if w > 14]


def test_team_strength_shrinks_toward_a_coin_flip():
    # 1-3 is 25% raw, but four games is not enough to believe that.
    assert 0.3 < team_strength(1, 3) < 0.45
    assert team_strength(0, 0) == 0.5
    # Clamped so no record implies certainty either way.
    assert team_strength(0, 12) >= 0.2
    assert team_strength(12, 0) <= 0.8


def test_playoff_cut_sits_below_500_when_most_teams_qualify():
    six_of_ten = playoff_cut_wins(regular_season_games=14, playoff_team_count=6, team_count=10)
    four_of_ten = playoff_cut_wins(regular_season_games=14, playoff_team_count=4, team_count=10)
    assert six_of_ten < 7.0 < four_of_ten
    assert four_of_ten > six_of_ten


def test_p_reach_bounds():
    assert p_reach(wins_now=10, games_left=0, cut=6.5, p=0.5) == 1.0  # already there
    assert p_reach(wins_now=0, games_left=2, cut=6.5, p=0.5) == 0.0  # cannot get there
    assert 0.0 < p_reach(wins_now=3, games_left=8, cut=6.5, p=0.5) < 1.0


def test_game_swing_vanishes_once_the_outcome_is_settled():
    assert game_swing(wins_now=12, games_after=2, cut=6.5, p=0.5) == 0.0  # clinched
    assert game_swing(wins_now=0, games_after=2, cut=6.5, p=0.5) == 0.0  # eliminated
    assert game_swing(wins_now=6, games_after=2, cut=6.5, p=0.5) > 0.0  # on the bubble


def test_weights_are_a_distribution_over_remaining_weeks():
    ww = week_weights(_ctx())
    assert ww.weeks == list(range(5, 18))
    assert round(sum(ww.weights.values()), 9) == 1.0
    assert all(value >= 0 for value in ww.weights.values())
    assert round(ww.mass(range(5, 15)) + ww.mass(range(15, 18)), 9) == 1.0


def test_losing_record_weights_the_games_in_front_of_it():
    losing = week_weights(_ctx(wins=1, losses=3))
    winning = week_weights(_ctx(wins=4, losses=0))
    # A 1-3 team probably never plays weeks 15-17, so they barely count.
    assert losing.mass(_playoff(losing)) < 0.15
    assert winning.mass(_playoff(winning)) > losing.mass(_playoff(losing))
    # Per week, the team that is going weights a playoff week far higher.
    assert winning.weight(15) > losing.weight(15)
    assert losing.playoff_odds < winning.playoff_odds


def test_regular_season_weeks_are_weighted_flat():
    # Without a schedule the remaining games are exchangeable, so nothing
    # distinguishes them and the weights must not pretend otherwise.
    ww = week_weights(_ctx())
    regular = [ww.weight(week) for week in _regular(ww)]
    assert len(set(round(value, 9) for value in regular)) == 1


def test_playoff_weeks_decline_across_the_rounds():
    ww = week_weights(_ctx(wins=4, losses=0))
    assert ww.weight(15) > ww.weight(16) > ww.weight(17)


def test_eliminated_team_still_weights_the_games_it_has_left():
    ww = week_weights(_ctx(week=13, wins=0, losses=12))
    assert ww.playoff_odds == 0.0
    # Weeks 13 and 14 are still played, so they carry all of the weight.
    assert ww.weight(13) == ww.weight(14) > 0
    assert ww.mass([15, 16, 17]) == 0.0
    assert round(sum(ww.weights.values()), 9) == 1.0


def test_missing_the_playoffs_entirely_falls_back_to_flat():
    # No regular-season weeks left and no chance of playing a playoff week, so
    # every week would score zero. Flat keeps values comparable instead of
    # collapsing every player to 0 at once.
    ww = week_weights(_ctx(week=15, wins=6, losses=8))
    assert ww.playoff_odds == 0.0
    assert ww.degenerate is True
    assert round(sum(ww.weights.values()), 9) == 1.0
    assert len(set(round(v, 9) for v in ww.weights.values())) == 1
    assert any("eliminated" in note for note in ww.notes)


def test_in_the_playoffs_only_playoff_weeks_carry_weight():
    ww = week_weights(_ctx(week=15, wins=9, losses=5))
    assert ww.weeks == [15, 16, 17]
    assert round(ww.mass([15, 16, 17]), 9) == 1.0


def test_playoff_stakes_shifts_the_balance():
    low = week_weights(_ctx(wins=4, losses=0), playoff_stakes=1.0)
    high = week_weights(_ctx(wins=4, losses=0), playoff_stakes=6.0)
    assert high.mass(_playoff(high)) > low.mass(_playoff(low))


def test_alternate_schemes():
    ctx = _ctx()
    uniform = week_weights(ctx, scheme="uniform")
    assert len(set(round(v, 9) for v in uniform.weights.values())) == 1

    decay = week_weights(ctx, scheme="decay")
    assert decay.weight(5) > decay.weight(10) > decay.weight(17)
    assert round(sum(decay.weights.values()), 9) == 1.0

    manual = week_weights(ctx, scheme="manual", manual={5: 3.0, 6: 1.0})
    assert round(manual.weight(5), 6) == 0.75
    assert round(manual.weight(6), 6) == 0.25
    assert manual.weight(7) == 0.0

    with pytest.raises(ValueError):
        week_weights(ctx, scheme="manual")
    with pytest.raises(ValueError):
        week_weights(ctx, scheme="nonsense")
