from league_manager.trades import grade_trade, grade_valued_trade, letter_grade
from league_manager.value import LeagueContext, PlayerValue, value_player


def _ctx(window_team="contender") -> LeagueContext:
    if window_team == "contender":
        return LeagueContext(
            current_week=10,
            season_end_week=17,
            wins=7,
            losses=2,
            standing=2,
            playoff_team_count=4,
            team_count=10,
        )
    return LeagueContext(
        current_week=10,
        season_end_week=17,
        wins=2,
        losses=7,
        standing=9,
        playoff_team_count=4,
        team_count=10,
    )


def _players() -> dict[int, dict]:
    return {
        1: {
            "id": 1,
            "name": "Stud RB",
            "position": "RB",
            "projected_points": 20,
            "lineup_slot_id": 2,
            "is_starter": True,
        },
        2: {
            "id": 2,
            "name": "Mid WR",
            "position": "WR",
            "projected_points": 11,
            "lineup_slot_id": 20,
            "is_starter": False,
        },
        3: {
            "id": 3,
            "name": "Streamer WR",
            "position": "WR",
            "projected_points": 10,
            "lineup_slot_id": 20,
            "is_starter": False,
        },
        4: {
            "id": 4,
            "name": "WR1",
            "position": "WR",
            "projected_points": 19,
            "lineup_slot_id": 4,
            "is_starter": True,
        },
        5: {
            "id": 5,
            "name": "RB1b",
            "position": "RB",
            "projected_points": 20,
            "lineup_slot_id": 2,
            "is_starter": True,
        },
        6: {
            "id": 6,
            "name": "QB1",
            "position": "QB",
            "projected_points": 18,
            "lineup_slot_id": 0,
            "is_starter": True,
        },
        7: {
            "id": 7,
            "name": "WR2",
            "position": "WR",
            "projected_points": 14,
            "lineup_slot_id": 4,
            "is_starter": True,
        },
        8: {
            "id": 8,
            "name": "TE1",
            "position": "TE",
            "projected_points": 9,
            "lineup_slot_id": 6,
            "is_starter": True,
        },
        9: {
            "id": 9,
            "name": "FLEX RB",
            "position": "RB",
            "projected_points": 12,
            "lineup_slot_id": 23,
            "is_starter": True,
        },
        10: {
            "id": 10,
            "name": "DST",
            "position": "D/ST",
            "projected_points": 7,
            "lineup_slot_id": 16,
            "is_starter": True,
        },
        11: {
            "id": 11,
            "name": "K",
            "position": "K",
            "projected_points": 8,
            "lineup_slot_id": 17,
            "is_starter": True,
        },
    }


def _roster(players: dict[int, dict], ids: list[int]) -> list[dict]:
    return [players[i] for i in ids]


def test_even_one_for_one_is_near_even():
    baselines = {"RB": 8.0, "WR": 8.0, "QB": 12.0, "TE": 5.0, "K": 5.0, "D/ST": 5.0}
    players = _players()
    # Equal RB not already on our roster.
    players[12] = {
        "id": 12,
        "name": "Equal RB",
        "position": "RB",
        "projected_points": 20,
        "lineup_slot_id": 20,
        "is_starter": False,
    }
    result = grade_trade(
        send_ids=[1],
        receive_ids=[12],
        players=players,
        context=_ctx(),
        baselines=baselines,
        window="bubble",
        our_roster=_roster(players, [1, 4, 6, 7, 8, 9, 10, 11, 2, 3]),
    )
    assert result["verdict"] == "even"
    assert result["roster"]["delta_st"] == 0
    assert result["roster"]["delta_lt"] == 0
    assert abs(result["lineup"]["delta_st"]) < 1
    assert result["stud_tax"] == 0
    assert result["lineup_hole_penalty"] == 0
    assert "summary" in result
    assert "roster" in result["weights"]
    assert "lineup" in result["weights"]


def test_selling_stud_for_bench_parts_hurts_lineup():
    baselines = {"RB": 8.0, "WR": 8.0, "QB": 12.0, "TE": 5.0, "K": 5.0, "D/ST": 5.0}
    players = _players()
    parts = grade_trade(
        send_ids=[1],
        receive_ids=[2, 3],
        players=players,
        context=_ctx("contender"),
        baselines=baselines,
        window="contender",
        our_roster=_roster(players, [1, 4, 6, 7, 8, 9, 10, 11]),
    )
    # Roster may look ok; lineup should drop without an RB to replace the stud.
    assert parts["lineup"]["delta_st"] < 0 or parts["lineup"]["delta_lt"] < 0
    assert parts["blended"] < 0
    assert parts["grade"] in {"C", "C-", "D", "F"}
    assert any("Lineup" in note or "lineup" in note for note in parts["notes"])


def test_consolidating_depth_improves_lineup():
    baselines = {"RB": 8.0, "WR": 8.0, "QB": 12.0, "TE": 5.0, "K": 5.0, "D/ST": 5.0}
    players = _players()
    # Bench WRs for a WR1 while starting a weak WR2
    players[7] = {
        "id": 7,
        "name": "Weak WR2",
        "position": "WR",
        "projected_points": 8,
        "lineup_slot_id": 4,
        "is_starter": True,
    }
    result = grade_trade(
        send_ids=[2, 3],
        receive_ids=[4],
        players=players,
        context=_ctx("contender"),
        baselines=baselines,
        window="contender",
        our_roster=_roster(players, [1, 2, 3, 6, 7, 8, 9, 10, 11]),
    )
    assert result["lineup"]["delta_st"] > 0
    assert result["stud_tax"] == 0
    assert letter_grade(12) == "A+"
    assert letter_grade(-9) == "F"


def test_starter_floor_mode_without_roster():
    baselines = {"RB": 8.0, "WR": 8.0}
    floors = {"RB": 12.0, "WR": 11.0}
    ctx = _ctx("bubble")
    send = [value_player(_players()[1], ctx, baselines, window="bubble")]
    recv = [value_player(_players()[2], ctx, baselines, window="bubble")]
    grade = grade_valued_trade(
        send_values=send,
        recv_values=recv,
        send_players=[_players()[1]],
        recv_players=[_players()[2]],
        context=ctx,
        window="bubble",
        starter_floors=floors,
    )
    assert grade["lineup"]["mode"] == "starter_floor"
    assert grade["roster"]["delta_st"] != 0 or grade["lineup"]["delta_st"] != 0


def test_rebuilder_weights_rest_of_season_more():
    baselines = {"RB": 8.0, "WR": 8.0, "QB": 12.0, "TE": 5.0, "K": 5.0, "D/ST": 5.0}
    players = _players()
    players[4] = {
        "id": 4,
        "name": "Injured WR1",
        "position": "WR",
        "projected_points": 19,
        "injury_status": "OUT",
        "lineup_slot_id": 4,
        "is_starter": True,
    }
    roster_ids = [1, 2, 6, 7, 8, 9, 10, 11]
    rebuild = grade_trade(
        send_ids=[2],
        receive_ids=[4],
        players=players,
        context=_ctx("rebuilder"),
        baselines=baselines,
        window="rebuilder",
        our_roster=_roster(players, roster_ids),
    )
    contend = grade_trade(
        send_ids=[2],
        receive_ids=[4],
        players=players,
        context=_ctx("contender"),
        baselines=baselines,
        window="contender",
        our_roster=_roster(players, roster_ids),
    )
    assert rebuild["weights"]["lt"] > contend["weights"]["lt"]
    assert rebuild["blended"] > contend["blended"]
