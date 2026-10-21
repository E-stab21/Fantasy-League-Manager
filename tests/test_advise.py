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


def _lineup_ctx() -> LeagueContext:
    return LeagueContext(
        current_week=10,
        season_end_week=17,
        regular_season_end=14,
        playoff_team_count=6,
        team_count=10,
        wins=5,
        losses=4,
    )


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
    advice = optimal_lineup(roster, context=_lineup_ctx())
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
    assert ranked["weights"]["roster"] == 0.6
    assert ranked["weights"]["scheme"] == "leverage"
    # Week weights are a distribution over the remaining weeks.
    assert round(ranked["weights"]["st"] + ranked["weights"]["lt"], 6) == 1.0
    targets = ranked["targets"]
    assert targets[0]["name"] == "Waiver Star"
    assert targets[0]["drop_candidate"] == "Handcuff"
    assert "roster" in targets[0]
    assert "lineup" in targets[0]
    assert targets[0]["blended"] > targets[1]["blended"]
    assert targets[0]["roster"]["delta_lt"] > 0
    # Lineups are re-set weekly, so an add can enter the lineup in some weeks of
    # the short-term window and not others. It must not make the lineup worse.
    assert targets[0]["lineup"]["delta_st"] >= 0
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


def test_flex_cannot_outbid_a_slot_filled_later():
    # Slot order comes from however ESPN lists the roster, so a FLEX can appear
    # before a WR slot. Filling in that order lets FLEX take the best WR and
    # strands points; restrictive slots must be filled first.
    roster = [
        {
            "id": 1, "name": "FLEX seat", "position": "WR", "lineup_slot": "FLEX",
            "lineup_slot_id": 23, "projected_points": 20,
        },
        {
            "id": 2, "name": "WR seat", "position": "WR", "lineup_slot": "WR",
            "lineup_slot_id": 4, "projected_points": 15,
        },
        {
            "id": 3, "name": "Bench RB", "position": "RB", "lineup_slot": "BE",
            "lineup_slot_id": 20, "projected_points": 18,
        },
    ]
    advice = optimal_lineup(roster, context=_lineup_ctx())
    # WR slot takes the 20, FLEX takes the 18 RB: 38, not 20 + 15.
    assert round(advice["recommended_projected"], 2) == 38.0
    assert {p["id"] for p in advice["recommended"]} == {1, 3}


def test_start_sit_trusts_the_week_projection_over_a_stale_injured_flag():
    # Players carrying an injury flag are routinely still projected to play.
    ctx = _lineup_ctx()
    weeks = ctx.remaining_weeks
    roster = [
        {
            "id": 1, "name": "Stale Flag", "position": "WR", "lineup_slot": "BE",
            "lineup_slot_id": 20, "injured": True,
            "injury_status": "INJURY_RESERVE",
            "weekly_projections": {week: 18.0 for week in weeks},
        },
        {
            "id": 2, "name": "Really Out", "position": "WR", "lineup_slot": "WR",
            "lineup_slot_id": 4, "injured": True, "injury_status": "OUT",
            # The source leaves this week out entirely.
            "weekly_projections": {week: 18.0 for week in weeks[1:]},
        },
        {
            "id": 3, "name": "Plodder", "position": "WR", "lineup_slot": "BE",
            "lineup_slot_id": 20,
            "weekly_projections": {week: 8.0 for week in weeks},
        },
    ]
    advice = optimal_lineup(roster, context=ctx)
    started = {p["id"] for p in advice["recommended"]}
    assert 1 in started  # flagged but projected: starts
    assert 2 not in started  # genuinely out this week


def test_bye_week_player_is_not_started_or_blamed_on_a_projection():
    ctx = _lineup_ctx()
    week = ctx.current_week
    roster = [
        {
            "id": 1, "name": "On Bye", "position": "WR", "lineup_slot": "WR",
            "lineup_slot_id": 4, "projected_points": 20.0,
            "projected_avg_points": 20.0,
            "weekly_stats": {str(week): {"projected_points": 0.0}},
        },
        {
            "id": 2, "name": "Playing", "position": "WR", "lineup_slot": "BE",
            "lineup_slot_id": 20, "projected_points": 9.0,
            "weekly_stats": {str(week): {"projected_points": 9.0}},
        },
    ]
    advice = optimal_lineup(roster, context=ctx)
    assert {p["id"] for p in advice["recommended"]} == {2}
    sat = {row["id"]: row for row in advice["sit"]}
    assert sat[1]["projection"] == 0.0
    assert "not playing" in sat[1]["reason"]


def test_kicker_on_bye_with_no_backup_raises_an_empty_slot_alert():
    # The live miss this exists for: the only kicker was on bye, the K slot went
    # unfilled, and the lineup total looked like any other week.
    ctx = _lineup_ctx()
    week = ctx.current_week
    roster = [
        {
            "id": 1, "name": "Bye Kicker", "position": "K", "lineup_slot": "K",
            "lineup_slot_id": 17, "projected_points": 8.9,
            "projected_avg_points": 8.9,
            "weekly_stats": {str(week): {"projected_points": 0.0}},
        },
        {
            "id": 2, "name": "WR1", "position": "WR", "lineup_slot": "WR",
            "lineup_slot_id": 4,
            "weekly_stats": {str(week): {"projected_points": 14.0}},
        },
    ]
    free_agents = [
        {
            "id": 9, "name": "Streamer K", "position": "K", "pro_team": "WSH",
            "weekly_stats": {str(week): {"projected_points": 7.5}},
        },
    ]
    advice = optimal_lineup(
        roster, context=ctx, free_agents=free_agents, baselines={"K": 10.7, "WR": 9.0}
    )
    # Nobody covers K, so it is absent from the lineup entirely.
    assert not any(p["recommended_slot"] == "K" for p in advice["recommended"])
    empty = [row for row in advice["alerts"] if row["kind"] == "empty"]
    assert len(empty) == 1
    assert empty[0]["slot"] == "K"
    assert empty[0]["player"] is None
    assert empty[0]["best_available"]["name"] == "Streamer K"
    assert "scores 0" in empty[0]["why"]


def test_an_empty_slot_is_reported_without_a_free_agent_pool():
    # The check that needs nothing extra must still fire; it just cannot name
    # a replacement.
    ctx = _lineup_ctx()
    week = ctx.current_week
    roster = [
        {
            "id": 1, "name": "Bye Kicker", "position": "K", "lineup_slot": "K",
            "lineup_slot_id": 17, "projected_avg_points": 8.9,
            "weekly_stats": {str(week): {"projected_points": 0.0}},
        },
    ]
    advice = optimal_lineup(roster, context=ctx)
    assert [row["kind"] for row in advice["alerts"]] == ["empty"]
    assert advice["alerts"][0]["best_available"] is None


def test_a_starter_under_the_replacement_rate_is_flagged_weak():
    ctx = _lineup_ctx()
    week = ctx.current_week
    roster = [
        {
            "id": 1, "name": "Fading RB", "position": "RB", "lineup_slot": "RB",
            "lineup_slot_id": 2,
            "weekly_stats": {str(week): {"projected_points": 7.0}},
        },
        {
            "id": 2, "name": "Fine WR", "position": "WR", "lineup_slot": "WR",
            "lineup_slot_id": 4,
            "weekly_stats": {str(week): {"projected_points": 14.0}},
        },
    ]
    free_agents = [
        {
            "id": 9, "name": "Hot Hand", "position": "RB", "pro_team": "ARI",
            "weekly_stats": {str(week): {"projected_points": 12.5}},
        },
    ]
    advice = optimal_lineup(
        roster, context=ctx, free_agents=free_agents, baselines={"RB": 10.5, "WR": 9.0}
    )
    kinds = {row["slot"]: row for row in advice["alerts"]}
    assert set(kinds) == {"RB"}  # the WR clears his own replacement rate
    assert kinds["RB"]["kind"] == "weak"
    assert kinds["RB"]["player"]["projection"] == 7.0
    assert kinds["RB"]["replacement_weekly"] == 10.5
    assert kinds["RB"]["best_available"]["name"] == "Hot Hand"


def test_one_free_agent_is_not_offered_for_two_holes():
    ctx = _lineup_ctx()
    week = ctx.current_week
    roster = [
        {
            "id": 1, "name": "Weak RB", "position": "RB", "lineup_slot": "RB",
            "lineup_slot_id": 2,
            "weekly_stats": {str(week): {"projected_points": 6.0}},
        },
        {
            "id": 2, "name": "Weak RB Two", "position": "RB", "lineup_slot": "RB",
            "lineup_slot_id": 2,
            "weekly_stats": {str(week): {"projected_points": 6.5}},
        },
    ]
    free_agents = [
        {
            "id": 9, "name": "Best FA", "position": "RB", "pro_team": "ARI",
            "weekly_stats": {str(week): {"projected_points": 13.0}},
        },
        {
            "id": 10, "name": "Next FA", "position": "RB", "pro_team": "LV",
            "weekly_stats": {str(week): {"projected_points": 11.0}},
        },
    ]
    advice = optimal_lineup(
        roster, context=ctx, free_agents=free_agents, baselines={"RB": 10.5}
    )
    suggested = [row["best_available"]["name"] for row in advice["alerts"]]
    assert sorted(suggested) == ["Best FA", "Next FA"]


def test_a_free_agent_not_playing_this_week_is_never_suggested():
    ctx = _lineup_ctx()
    week = ctx.current_week
    weeks = ctx.remaining_weeks
    roster = [
        {
            "id": 1, "name": "Weak RB", "position": "RB", "lineup_slot": "RB",
            "lineup_slot_id": 2,
            "weekly_stats": {str(week): {"projected_points": 6.0}},
        },
    ]
    free_agents = [
        {
            "id": 9, "name": "On Bye FA", "position": "RB", "pro_team": "ARI",
            # Published zero for this week: a bye, not a gap.
            "weekly_stats": {str(week): {"projected_points": 0.0}},
            "projected_avg_points": 15.0,
        },
        {
            "id": 10, "name": "Out For Weeks", "position": "RB", "pro_team": "LV",
            # The source omits this week entirely.
            "weekly_projections": {w: 14.0 for w in weeks[3:]},
        },
    ]
    advice = optimal_lineup(
        roster, context=ctx, free_agents=free_agents, baselines={"RB": 10.5}
    )
    assert advice["alerts"][0]["best_available"] is None
