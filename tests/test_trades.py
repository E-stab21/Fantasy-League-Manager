import pytest

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


def test_losing_record_weights_the_games_in_front_of_you():
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
    # A losing team is unlikely to reach the playoff weeks, so the weight sits
    # on the games it still has to win. A contender weights those playoff weeks
    # instead. This is the inverse of the old record-keyed window weighting.
    assert rebuild["weights"]["st"] > contend["weights"]["st"]
    assert contend["weights"]["lt"] > rebuild["weights"]["lt"]
    assert contend["weights"]["playoff_odds"] > rebuild["weights"]["playoff_odds"]
    # Weights are a distribution over the remaining weeks, so the split is whole.
    for grade in (rebuild, contend):
        assert round(grade["weights"]["st"] + grade["weights"]["lt"], 6) == 1.0
        # Reported to 2dp, so the split adds back within rounding.
        assert abs(grade["delta_st"] + grade["delta_lt"] - grade["blended"]) <= 0.011


def test_one_for_two_full_roster_subtracts_forced_drop():
    baselines = {"RB": 8.0, "WR": 8.0, "QB": 12.0, "TE": 5.0, "K": 5.0, "D/ST": 5.0}
    players = _players()
    players[99] = {
        "id": 99,
        "name": "Cut Candidate",
        "position": "WR",
        "projected_points": 4,
        "lineup_slot_id": 20,
        "is_starter": False,
    }
    players[12] = {
        "id": 12,
        "name": "Incoming RB",
        "position": "RB",
        "projected_points": 15,
        "lineup_slot_id": 20,
        "is_starter": False,
    }
    players[13] = {
        "id": 13,
        "name": "Incoming WR",
        "position": "WR",
        "projected_points": 15,
        "lineup_slot_id": 20,
        "is_starter": False,
    }
    ctx = _ctx("bubble")
    roster_ids = [1, 4, 6, 7, 8, 9, 10, 11, 2, 99]
    result = grade_trade(
        send_ids=[2],
        receive_ids=[12, 13],
        players=players,
        context=ctx,
        baselines=baselines,
        window="bubble",
        our_roster=_roster(players, roster_ids),
    )
    assert result["dropped"][0]["id"] == 99
    assert "Cut Candidate" in result["summary"]
    send = value_player(players[2], ctx, baselines, window="bubble")
    recv = [
        value_player(players[12], ctx, baselines, window="bubble"),
        value_player(players[13], ctx, baselines, window="bubble"),
    ]
    dropped = value_player(players[99], ctx, baselines, window="bubble")
    expected_st = round(recv[0].st_vorp + recv[1].st_vorp - send.st_vorp - dropped.st_vorp, 2)
    expected_lt = round(recv[0].lt_vorp + recv[1].lt_vorp - send.lt_vorp - dropped.lt_vorp, 2)
    assert result["roster"]["delta_st"] == expected_st
    assert result["roster"]["delta_lt"] == expected_lt
    assert result["roster"]["cap"] == 10
    naive_st = round(recv[0].st_vorp + recv[1].st_vorp - send.st_vorp, 2)
    assert result["roster"]["delta_st"] != naive_st


def test_one_for_two_open_slot_does_not_drop():
    baselines = {"RB": 8.0, "WR": 8.0, "QB": 12.0, "TE": 5.0, "K": 5.0, "D/ST": 5.0}
    players = _players()
    players[12] = {
        "id": 12,
        "name": "Incoming RB",
        "position": "RB",
        "projected_points": 15,
        "lineup_slot_id": 20,
        "is_starter": False,
    }
    players[13] = {
        "id": 13,
        "name": "Incoming WR",
        "position": "WR",
        "projected_points": 15,
        "lineup_slot_id": 20,
        "is_starter": False,
    }
    ctx = LeagueContext(
        current_week=10,
        season_end_week=17,
        wins=7,
        losses=2,
        standing=2,
        playoff_team_count=4,
        team_count=10,
        roster_cap=16,
    )
    result = grade_trade(
        send_ids=[2],
        receive_ids=[12, 13],
        players=players,
        context=ctx,
        baselines=baselines,
        window="bubble",
        our_roster=_roster(players, [1, 4, 6, 7, 8, 9, 10, 11, 2, 3]),
    )
    assert result["dropped"] == []
    send = value_player(players[2], ctx, baselines, window="bubble")
    recv = [
        value_player(players[12], ctx, baselines, window="bubble"),
        value_player(players[13], ctx, baselines, window="bubble"),
    ]
    # Expected is summed from fields already rounded to 2dp; the grade sums the
    # unrounded values, so compare within that.
    assert result["roster"]["delta_st"] == pytest.approx(
        recv[0].st_vorp + recv[1].st_vorp - send.st_vorp, abs=0.011
    )
    assert result["roster"]["cap"] == 16


def test_two_for_one_empty_slot_is_replacement_level():
    baselines = {"RB": 8.0, "WR": 8.0, "QB": 12.0, "TE": 5.0, "K": 5.0, "D/ST": 5.0}
    players = _players()
    players[12] = {
        "id": 12,
        "name": "Incoming WR1",
        "position": "WR",
        "projected_points": 18,
        "lineup_slot_id": 20,
        "is_starter": False,
    }
    ctx = _ctx("bubble")
    result = grade_trade(
        send_ids=[2, 3],
        receive_ids=[12],
        players=players,
        context=ctx,
        baselines=baselines,
        window="bubble",
        our_roster=_roster(players, [1, 4, 6, 7, 8, 9, 10, 11, 2, 3]),
    )
    assert result["dropped"] == []
    send_a = value_player(players[2], ctx, baselines, window="bubble")
    send_b = value_player(players[3], ctx, baselines, window="bubble")
    recv = value_player(players[12], ctx, baselines, window="bubble")
    # Empty leftover spot is ~0 VORP, so after-before == recv - send.
    assert result["roster"]["delta_st"] == round(recv.st_vorp - send_a.st_vorp - send_b.st_vorp, 2)
    assert result["roster"]["delta_lt"] == round(recv.lt_vorp - send_a.lt_vorp - send_b.lt_vorp, 2)


def test_lineup_is_reoptimized_each_week():
    from league_manager.leverage import week_weights
    from league_manager.trades import lineup_curves, weekly_lineup_points

    ctx = LeagueContext(
        current_week=10, season_end_week=17, regular_season_end=14,
        playoff_team_count=6, team_count=10, wins=5, losses=4,
    )
    weeks = ctx.remaining_weeks
    # A star available only for the first two weeks, and a steady alternative.
    roster = [
        {
            "id": 1, "name": "Two Week Star", "position": "WR", "lineup_slot": "WR",
            "lineup_slot_id": 4,
            "weekly_projections": {week: 30.0 for week in weeks[:2]},
        },
        {
            "id": 2, "name": "Steady", "position": "WR", "lineup_slot": "BE",
            "lineup_slot_id": 20,
            "weekly_projections": {week: 10.0 for week in weeks},
        },
    ]
    weights = week_weights(ctx)
    curves = lineup_curves(roster, ctx)
    st, lt, picks = weekly_lineup_points(
        roster, context=ctx, weights=weights, curves=curves, slot_template=[4]
    )

    # The star starts while he is available, and is replaced once he is not --
    # a single lineup held across the whole horizon cannot do both.
    assert [p["id"] for p in picks[weeks[0]]] == [1]
    assert [p["id"] for p in picks[weeks[1]]] == [1]
    assert [p["id"] for p in picks[weeks[2]]] == [2]
    assert all(picks[week][0]["id"] == 2 for week in weeks[2:])

    # Never worse than benching the star outright.
    steady_only = sum(weights.weight(week) * 10.0 for week in weeks)
    assert st + lt > steady_only


def test_exact_drop_chooser_never_loses_to_the_margin_proxy():
    # choose_drops_exactly scores each candidate roster the way the trade itself
    # is scored, so by construction it cannot do worse than the fast proxy.
    from league_manager.leverage import week_weights
    from league_manager.roster_roles import (
        RoleScorer,
        assign_roles,
        choose_forced_drops,
        role_weight_set,
        roster_totals,
    )
    from league_manager.trades import (
        choose_drops_exactly,
        lineup_curves,
        weekly_lineup_points,
    )
    from league_manager.value import weekly_projection_curve

    ctx = LeagueContext(
        current_week=6, season_end_week=17, regular_season_end=14,
        playoff_team_count=6, team_count=10, wins=2, losses=3,
    )
    weeks = ctx.remaining_weeks
    baselines = {"RB": 8.0, "WR": 8.0, "K": 8.0}

    def row(pid, name, pos, curve, slot_id=20, slot="BE"):
        return {
            "id": pid, "name": name, "position": pos,
            "lineup_slot": slot, "lineup_slot_id": slot_id,
            "weekly_projections": curve,
        }

    roster = [
        row(1, "Starter RB", "RB", {w: 18.0 for w in weeks}, 2, "RB"),
        row(2, "Starter WR", "WR", {w: 17.0 for w in weeks}, 4, "WR"),
        row(3, "Only K", "K", {w: 8.3 for w in weeks}, 17, "K"),
        # Covers the WR slot in the weeks the starter is out.
        row(4, "Bye Cover WR", "WR", {w: 13.0 for w in weeks[:2]}),
        row(5, "Flat Spare", "WR", {w: 8.6 for w in weeks}),
        row(9, "Arrival", "RB", {w: 19.0 for w in weeks}),
    ]
    cache: dict = {}

    def curve_for(player):
        pid = player.get("id")
        if pid not in cache:
            cache[pid], _ = weekly_projection_curve(player, ctx)
        return cache[pid]

    weights = week_weights(ctx)
    scorer = RoleScorer(ctx, baselines, role_weight_set(weights, ctx), curve_for=curve_for)
    roles = assign_roles(roster, scorer=scorer)
    slots = [2, 4, 17]
    incoming = [roster[-1]]
    shared = dict(
        context=ctx, weights=weights, slots=slots, roster_w=0.6, lineup_w=0.4
    )

    def total_after(cut_id):
        after = [p for p in roster if p.get("id") != cut_id]
        rs, rl = roster_totals(after, scorer)
        ls, ll, _ = weekly_lineup_points(
            after, context=ctx, weights=weights,
            curves=lineup_curves(after, ctx, cache={}), slot_template=slots,
        )
        return 0.6 * (rs + rl) + 0.4 * (ls + ll)

    proxy = choose_forced_drops(
        roster, incoming=incoming, drops_needed=1, scorer=scorer, roles=roles
    )
    exact = choose_drops_exactly(
        roster, incoming=incoming, drops_needed=1, scorer=scorer, curve_cache={}, **shared
    )
    assert len(exact) == 1
    assert exact[0]["id"] != 3  # the only kicker stays
    assert total_after(exact[0]["id"]) >= total_after(proxy[0]["id"]) - 1e-9
