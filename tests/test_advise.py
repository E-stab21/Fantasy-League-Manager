from league_manager.advise import optimal_lineup, waiver_targets
from league_manager.projections import attach_sleeper_projections, primary_projection
from league_manager.sleeper import normalize_name, projection_points
from league_manager.slots import parse_slot


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
    roster = [
        {
            "id": 9,
            "name": "Handcuff",
            "position": "RB",
            "lineup_slot": "BE",
            "lineup_slot_id": 20,
            "projected_points": 4,
        }
    ]
    free_agents = [
        {"id": 80, "name": "Waiver Star", "position": "RB", "projected_points": 14},
        {"id": 81, "name": "Streamer", "position": "RB", "projected_points": 5},
    ]
    targets = waiver_targets(roster, free_agents, limit=2)
    assert targets[0]["name"] == "Waiver Star"
    assert targets[0]["drop_candidate"] == "Handcuff"
    assert targets[0]["delta"] == 10


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
