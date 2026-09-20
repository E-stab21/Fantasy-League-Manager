from league_manager.advise import (
    ir_stash_alerts,
    optimal_lineup,
    rank_waiver_claims,
    waiver_targets,
)
from league_manager.projections import attach_sleeper_projections, primary_projection
from league_manager.sleeper import normalize_name, projection_points
from league_manager.slots import parse_slot
from league_manager.value import LeagueContext


def test_optimal_lineup_benches_injured_and_promotes_better_rb():
    roster = [
        {
            "id": 1,
            "name": "Hurt Starter",
            "position": "RB",
            "lineup_slot": "RB",
            "lineup_slot_id": 2,
            "projected_points": 18,
            "injured": True,
        },
        {
            "id": 2,
            "name": "Backup",
            "position": "RB",
            "lineup_slot": "BE",
            "lineup_slot_id": 20,
            "projected_points": 12,
            "injured": False,
        },
        {
            "id": 3,
            "name": "QB1",
            "position": "QB",
            "lineup_slot": "QB",
            "lineup_slot_id": 0,
            "projected_points": 20,
            "injured": False,
        },
    ]
    advice = optimal_lineup(roster)
    recommended_ids = {player["id"] for player in advice["recommended"]}
    assert 2 in recommended_ids
    assert 1 not in recommended_ids
    assert any(move["player_id"] == 2 and move["to_slot"] == 2 for move in advice["moves"])
    assert advice["recommended_projected"] > 0


def test_waiver_targets_rank_upgrade():
    context = LeagueContext(
        current_week=2,
        season_end_week=17,
        wins=0,
        losses=1,
        standing=9,
        playoff_team_count=6,
        team_count=10,
    )
    roster = [
        {
            "id": 1,
            "name": "RB1",
            "position": "RB",
            "lineup_slot": "RB",
            "lineup_slot_id": 2,
            "projected_points": 16,
            "projected_total_points": 240,
            "points": 10,
        },
        {
            "id": 2,
            "name": "RB2",
            "position": "RB",
            "lineup_slot": "RB",
            "lineup_slot_id": 2,
            "projected_points": 14,
            "projected_total_points": 210,
            "points": 8,
        },
        {
            "id": 9,
            "name": "Handcuff",
            "position": "RB",
            "lineup_slot": "BE",
            "lineup_slot_id": 20,
            "projected_points": 10,
            "projected_total_points": 40,
            "points": 20,
        },
        {
            "id": 3,
            "name": "QB1",
            "position": "QB",
            "lineup_slot": "QB",
            "lineup_slot_id": 0,
            "projected_points": 20,
            "projected_total_points": 300,
            "points": 15,
        },
        {
            "id": 4,
            "name": "WR1",
            "position": "WR",
            "lineup_slot": "WR",
            "lineup_slot_id": 4,
            "projected_points": 18,
            "projected_total_points": 270,
            "points": 12,
        },
        {
            "id": 5,
            "name": "WR2",
            "position": "WR",
            "lineup_slot": "WR",
            "lineup_slot_id": 4,
            "projected_points": 13,
            "projected_total_points": 200,
            "points": 9,
        },
        {
            "id": 6,
            "name": "TE1",
            "position": "TE",
            "lineup_slot": "TE",
            "lineup_slot_id": 6,
            "projected_points": 10,
            "projected_total_points": 160,
            "points": 8,
        },
        {
            "id": 7,
            "name": "FLEX",
            "position": "WR",
            "lineup_slot": "FLEX",
            "lineup_slot_id": 23,
            "projected_points": 12,
            "projected_total_points": 180,
            "points": 7,
        },
        {
            "id": 8,
            "name": "DST",
            "position": "D/ST",
            "lineup_slot": "D/ST",
            "lineup_slot_id": 16,
            "projected_points": 7,
            "projected_total_points": 100,
            "points": 6,
        },
        {
            "id": 10,
            "name": "K",
            "position": "K",
            "lineup_slot": "K",
            "lineup_slot_id": 17,
            "projected_points": 8,
            "projected_total_points": 110,
            "points": 7,
        },
    ]
    free_agents = [
        {
            "id": 80,
            "name": "Waiver Star",
            "position": "RB",
            "projected_points": 8,
            "projected_total_points": 200,
            "points": 6,
            "percent_owned": 70,
        },
        {
            "id": 81,
            "name": "Streamer",
            "position": "RB",
            "projected_points": 12,
            "projected_total_points": 50,
            "points": 15,
            "percent_owned": 20,
        },
    ]
    baselines = {"RB": 8.0, "WR": 9.0, "QB": 14.0, "TE": 8.0, "K": 7.0, "D/ST": 6.0}
    ranked = rank_waiver_claims(
        roster,
        free_agents,
        context=context,
        baselines=baselines,
        window="auto",
        limit=2,
    )
    assert ranked["window"] == "rebuilder"
    assert ranked["weights"]["lt"] == 0.7
    assert ranked["weights"]["roster"] == 0.6
    targets = ranked["targets"]
    assert targets[0]["name"] == "Waiver Star"
    assert targets[0]["drop_candidate"] == "Handcuff"
    assert "roster" in targets[0]
    assert "lineup" in targets[0]
    assert targets[0]["blended"] > targets[1]["blended"]
    assert targets[0]["roster"]["delta_lt"] > 0
    assert targets[0]["lineup"]["delta_st"] == 0
    # Same helper still returns a list for older callers.
    listed = waiver_targets(
        roster, free_agents, context=context, baselines=baselines, window="auto", limit=2
    )
    assert listed[0]["name"] == "Waiver Star"


def test_sleeper_name_overlay():
    espn_players = [{"id": 1, "name": "Ja'Marr Chase Jr.", "projected_points": 16}]
    player_map = {
        "4016": {"full_name": "Ja'Marr Chase", "injury_status": None},
    }
    projection_map = {"4016": {"stats": {"pts_ppr": 21.4, "pts_std": 15.1}}}
    rows = attach_sleeper_projections(
        espn_players,
        season=2026,
        week=1,
        player_map=player_map,
        projection_map=projection_map,
    )
    assert rows[0]["sleeper_id"] == "4016"
    assert rows[0]["sleeper_projected_points"] == 21.4
    assert normalize_name("Ja'Marr Chase Jr.") == normalize_name("Ja'Marr Chase")
    assert projection_points(projection_map["4016"], "ppr") == 21.4
    assert primary_projection(rows[0]) == 18.7
    assert parse_slot("FLEX") == 23


def test_primary_projection_averages_when_both_present():
    assert primary_projection({"projected_points": 10, "sleeper_projected_points": 20}) == 15
    assert primary_projection({"projected_points": 10}) == 10
    assert primary_projection({"sleeper_projected_points": 20}) == 20
    assert primary_projection({}) == 0.0


def test_ir_stash_alerts_flags_out_bench_player_not_on_ir():
    roster = [
        {
            "id": 1,
            "name": "Starter",
            "position": "RB",
            "lineup_slot": "RB",
            "lineup_slot_id": 2,
            "injured": False,
            "injury_status": "ACTIVE",
        },
        {
            "id": 2,
            "name": "Hurt Bench Guy",
            "position": "WR",
            "lineup_slot": "BE",
            "lineup_slot_id": 20,
            "injured": True,
            "injury_status": "OUT",
        },
        {
            "id": 3,
            "name": "Already Stashed",
            "position": "WR",
            "lineup_slot": "IR",
            "lineup_slot_id": 21,
            "injured": True,
            "injury_status": "OUT",
        },
        {
            "id": 4,
            "name": "Just Questionable",
            "position": "RB",
            "lineup_slot": "BE",
            "lineup_slot_id": 20,
            "injured": False,
            "injury_status": "QUESTIONABLE",
        },
    ]
    alerts = ir_stash_alerts(roster)
    assert [a["id"] for a in alerts] == [2]
    assert alerts[0]["injury_status"] == "OUT"


def test_rank_waiver_claims_surfaces_ir_alert_and_flags_drop():
    context = LeagueContext(
        current_week=2,
        season_end_week=17,
        wins=0,
        losses=1,
        standing=9,
        playoff_team_count=6,
        team_count=10,
    )
    roster = [
        {
            "id": 1,
            "name": "RB1",
            "position": "RB",
            "lineup_slot": "RB",
            "lineup_slot_id": 2,
            "projected_points": 16,
            "projected_total_points": 240,
            "points": 10,
        },
        {
            "id": 2,
            "name": "Injured Bench WR",
            "position": "WR",
            "lineup_slot": "BE",
            "lineup_slot_id": 20,
            "projected_points": 2,
            "projected_total_points": 30,
            "points": 1,
            "injured": True,
            "injury_status": "OUT",
        },
    ]
    free_agents = [
        {
            "id": 80,
            "name": "Waiver Star",
            "position": "WR",
            "projected_points": 12,
            "projected_total_points": 200,
            "points": 10,
            "percent_owned": 70,
        },
    ]
    baselines = {"RB": 8.0, "WR": 9.0, "QB": 14.0, "TE": 8.0, "K": 7.0, "D/ST": 6.0}
    ranked = rank_waiver_claims(
        roster,
        free_agents,
        context=context,
        baselines=baselines,
        window="auto",
        limit=2,
    )
    assert [a["id"] for a in ranked["ir_stash_alerts"]] == [2]
    target = ranked["targets"][0]
    assert target["drop_candidate_id"] == 2
    assert target["drop_ir_eligible"] is True
    assert "IR-eligible" in target["why"]
