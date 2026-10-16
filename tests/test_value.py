from league_manager.value import (
    LeagueContext,
    availability,
    infer_window,
    replacement_baselines,
    ros_points,
    roster_cap_from_settings,
    value_player,
    weekly_projection_curve,
    weekly_rate,
)


def _ctx(**overrides) -> LeagueContext:
    data = dict(
        current_week=10,
        season_end_week=17,
        regular_season_end=14,
        playoff_team_count=4,
        team_count=10,
        short_term_weeks=3,
        wins=6,
        losses=3,
        standing=3,
    )
    data.update(overrides)
    return LeagueContext(**data)


def test_window_from_record():
    assert infer_window(_ctx(standing=1, wins=7, losses=2)) == "contender"
    assert infer_window(_ctx(standing=5, wins=5, losses=4, playoff_team_count=4)) == "bubble"
    assert infer_window(_ctx(standing=9, wins=2, losses=7, playoff_team_count=4)) == "rebuilder"
    assert infer_window(_ctx(), override="rebuilder") == "rebuilder"


def test_bye_and_injury_cut_short_term():
    baselines = {"RB": 8.0}
    healthy = value_player(
        {
            "id": 1,
            "name": "Workhorse",
            "position": "RB",
            "projected_points": 16,
            "bye_week": 11,
        },
        _ctx(),
        baselines,
        window="bubble",
    )
    assert healthy.st_games == 2  # weeks 10,12; 11 is bye
    assert healthy.lt_games == 7  # 10-17 minus bye
    assert healthy.ros_source == "weekly_times_games"
    # Weighted, so the split adds back to the whole instead of overlapping it.
    assert round(healthy.st_vorp + healthy.lt_vorp, 6) == round(healthy.blended, 6)

    no_bye = value_player(
        {"id": 1, "name": "Workhorse", "position": "RB", "projected_points": 16},
        _ctx(),
        baselines,
        window="bubble",
    )
    assert no_bye.st_vorp > healthy.st_vorp  # the bye costs a short-term week

    out = value_player(
        {
            "id": 2,
            "name": "Hurt",
            "position": "RB",
            "projected_points": 16,
            "injury_status": "OUT",
        },
        _ctx(),
        baselines,
        window="contender",
    )
    assert out.blended < no_bye.blended
    # OUT is worth nothing this week but ramps back, rather than taking one flat
    # discount across the whole season.
    assert availability(out_player := {"injury_status": "OUT"}, weeks_out=0) == 0.0
    assert 0.0 < availability(out_player, weeks_out=1) < 1.0
    assert availability(out_player, weeks_out=3) == 1.0
    assert out.lt_points > 0


def test_replacement_uses_third_best_fa():
    agents = [
        {"position": "WR", "projected_points": 14},
        {"position": "WR", "projected_points": 12},
        {"position": "WR", "projected_points": 9},
        {"position": "WR", "projected_points": 4},
        {"position": "QB", "projected_points": 18},
    ]
    baselines = replacement_baselines(agents)
    assert baselines["WR"] == 9
    assert baselines["QB"] == 18
    assert weekly_rate({"projected_points": 0, "projected_avg_points": 11.5}) == 11.5


def test_ros_prefers_fantasypros_then_sleeper_then_espn_remainder():
    player = {
        "projected_points": 16,
        "fantasypros_ros_points": 140,
        "sleeper_ros_points": 110,
        "projected_total_points": 200,
        "points": 80,
    }
    points, source = ros_points(player, lt_games=7, lt_factor=1.0)
    assert source == "fantasypros_ros"
    assert points == 140

    del player["fantasypros_ros_points"]
    points, source = ros_points(player, lt_games=7, lt_factor=1.0)
    assert source == "sleeper_ros"
    assert points == 110

    del player["sleeper_ros_points"]
    points, source = ros_points(player, lt_games=7, lt_factor=1.0)
    assert source == "espn_remainder"
    assert points == 120

    del player["projected_total_points"]
    points, source = ros_points(player, lt_games=7, lt_factor=1.0)
    assert source == "weekly_times_games"
    assert points == 16 * 7


def test_espn_remainder_beats_flattening_this_week():
    baselines = {"RB": 8.0}
    flat = value_player(
        {"id": 1, "name": "Cold", "position": "RB", "projected_points": 8},
        _ctx(),
        baselines,
        window="bubble",
    )
    remainder = value_player(
        {
            "id": 1,
            "name": "Cold",
            "position": "RB",
            "projected_points": 8,
            "projected_total_points": 200,
            "points": 40,
        },
        _ctx(),
        baselines,
        window="bubble",
    )
    assert remainder.ros_source == "espn_remainder"
    # 160 points spread over the remaining games, not this week's 8 flattened.
    curve, _ = weekly_projection_curve(
        {
            "id": 1,
            "position": "RB",
            "projected_points": 8,
            "projected_total_points": 200,
            "points": 40,
        },
        _ctx(),
    )
    ctx = _ctx()
    assert round(curve[ctx.current_week], 2) == 8.0  # this week keeps its own number
    assert round(curve[ctx.current_week + 1], 2) == 20.0  # later weeks get the ROS rate
    assert remainder.lt_vorp > flat.lt_vorp


def test_roster_cap_from_settings_skips_ir():
    class _Settings:
        position_slot_counts = {
            "QB": 1,
            "RB": 2,
            "WR": 2,
            "TE": 1,
            "FLEX": 1,
            "D/ST": 1,
            "K": 1,
            "BE": 7,
            "IR": 3,
        }

    assert roster_cap_from_settings(_Settings()) == 16
    assert roster_cap_from_settings(None) is None
    assert roster_cap_from_settings(object()) is None
