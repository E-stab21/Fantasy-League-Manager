from league_manager.search import search_trades
from league_manager.value import LeagueContext


def _ctx() -> LeagueContext:
    return LeagueContext(
        current_week=10,
        season_end_week=17,
        wins=6,
        losses=3,
        standing=3,
        playoff_team_count=4,
        team_count=10,
    )


def test_search_keeps_modest_plus_ev_close_face_one_for_one():
    # Same ESPN weekly face. Slight ROS edge for us — still in the "likely" band.
    ours = {
        "id": 1,
        "name": "Our Spent RB",
        "position": "RB",
        "projected_points": 12,
        "projected_total_points": 150,
        "points": 70,
        "is_starter": True,
        "team_id": 7,
    }
    theirs = {
        "id": 2,
        "name": "Their ROS WR",
        "position": "WR",
        "projected_points": 12,
        "projected_total_points": 160,
        "points": 70,
        "is_starter": True,
        "team_id": 8,
    }
    filler = {
        "id": 3,
        "name": "Their Bench",
        "position": "WR",
        "projected_points": 6,
        "is_starter": False,
        "team_id": 8,
    }
    result = search_trades(
        our_team_id=7,
        teams=[
            {"id": 7, "name": "Us", "roster": [ours]},
            {"id": 8, "name": "Them", "roster": [theirs, filler]},
        ],
        players={1: ours, 2: theirs, 3: filler},
        context=_ctx(),
        baselines={"RB": 8.0, "WR": 8.0},
        window="bubble",
        kinds=("1:1",),
        limit=10,
        min_surplus=1.0,
        max_surplus=40.0,
    )
    assert result["considered"] >= 1
    assert result["trades"]
    top = result["trades"][0]
    assert top["kind"] == "1:1"
    assert top["send_ids"] == [1]
    assert top["receive_ids"] == [2]
    assert 1.0 <= top["blended"] <= 40.0
    assert abs(top["their_espn_face_net"]) < 1
    assert top["receive"][0]["ros_source"] == "espn_remainder"


def test_search_keeps_a_lopsided_espn_face_and_only_reports_it():
    cheap = {
        "id": 1,
        "name": "Streamer",
        "position": "RB",
        "projected_points": 8,
        "is_starter": False,
        "team_id": 7,
    }
    star = {
        "id": 2,
        "name": "WR1",
        "position": "WR",
        "projected_points": 20,
        "is_starter": True,
        "team_id": 8,
    }
    result = search_trades(
        our_team_id=7,
        teams=[
            {"id": 7, "name": "Us", "roster": [cheap]},
            {"id": 8, "name": "Them", "roster": [star]},
        ],
        players={1: cheap, 2: star},
        context=_ctx(),
        baselines={"RB": 8.0, "WR": 8.0},
        window="bubble",
        kinds=("1:1",),
        # Explicit, so this stays about face and not about the band ceiling.
        max_surplus=40.0,
    )
    # A streamer for their WR1 is never getting accepted, and that is the
    # manager's read to make. Nothing filters on face any more, so the package
    # survives and carries the (badly negative) face number for them to see.
    assert result["considered"] == 1
    assert result["trades"]
    top = result["trades"][0]
    assert top["send_ids"] == [1] and top["receive_ids"] == [2]
    assert top["their_espn_face_net"] < -50
    assert "ESPN face for them" in top["why"]


def test_search_supports_two_for_two():
    our_a = {
        "id": 1,
        "name": "Our RB",
        "position": "RB",
        "projected_points": 11,
        "projected_total_points": 148,
        "points": 70,
        "is_starter": True,
        "team_id": 7,
    }
    our_b = {
        "id": 2,
        "name": "Our WR",
        "position": "WR",
        "projected_points": 10,
        "projected_total_points": 140,
        "points": 70,
        "is_starter": True,
        "team_id": 7,
    }
    their_a = {
        "id": 3,
        "name": "Their RB",
        "position": "RB",
        "projected_points": 11,
        "projected_total_points": 155,
        "points": 70,
        "is_starter": True,
        "team_id": 8,
    }
    their_b = {
        "id": 4,
        "name": "Their WR",
        "position": "WR",
        "projected_points": 10,
        "projected_total_points": 148,
        "points": 70,
        "is_starter": True,
        "team_id": 8,
    }
    result = search_trades(
        our_team_id=7,
        teams=[
            {"id": 7, "name": "Us", "roster": [our_a, our_b]},
            {"id": 8, "name": "Them", "roster": [their_a, their_b]},
        ],
        players={1: our_a, 2: our_b, 3: their_a, 4: their_b},
        context=_ctx(),
        baselines={"RB": 8.0, "WR": 8.0},
        window="bubble",
        kinds=("2:2",),
        limit=5,
        min_surplus=0.5,
        max_surplus=40.0,
    )
    assert result["considered"] == 1
    assert result["trades"]
    top = result["trades"][0]
    assert top["kind"] == "2:2"
    assert set(top["send_ids"]) == {1, 2}
    assert set(top["receive_ids"]) == {3, 4}


def test_search_keeps_a_smash_spot_and_only_reports_their_ros_net():
    # Same weekly ESPN face, enormous ROS gap. That used to be filtered out as
    # undealable; judging that is the manager's call, so it is reported instead.
    ours = {
        "id": 1,
        "name": "QB",
        "position": "QB",
        "projected_points": 19,
        "projected_total_points": 300,
        "points": 20,
        "is_starter": True,
        "team_id": 7,
        "sleeper_ros_points": 280,
    }
    theirs = {
        "id": 2,
        "name": "Elite RB",
        "position": "RB",
        "projected_points": 19,
        "projected_total_points": 320,
        "points": 20,
        "is_starter": True,
        "team_id": 8,
        "sleeper_ros_points": 380,
    }
    result = search_trades(
        our_team_id=7,
        teams=[
            {"id": 7, "name": "Us", "roster": [ours]},
            {"id": 8, "name": "Them", "roster": [theirs]},
        ],
        players={1: ours, 2: theirs},
        context=_ctx(),
        baselines={"QB": 15.0, "RB": 8.0},
        window="bubble",
        kinds=("1:1",),
        max_surplus=16.0,
    )
    assert result["considered"] == 1
    assert result["trades"]
    top = result["trades"][0]
    assert top["their_ros_vorp_net"] < -10  # the gap that used to disqualify it
    assert top["blended"] <= 16.0  # the one bound that still applies
