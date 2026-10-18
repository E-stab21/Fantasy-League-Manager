from league_manager.leverage import ROLE_REGULAR, ROLE_STASH, ROLE_STREAM, week_weights
from league_manager.roster_roles import (
    RoleScorer,
    assign_roles,
    choose_forced_drops,
    marginal_arrival,
    role_weight_set,
    roster_totals,
)
from league_manager.value import LeagueContext, weekly_projection_curve


def _ctx(**overrides) -> LeagueContext:
    data = dict(
        current_week=6,
        season_end_week=17,
        regular_season_end=14,
        playoff_team_count=6,
        team_count=10,
        wins=2,
        losses=3,
    )
    data.update(overrides)
    return LeagueContext(**data)


def _scorer(ctx, baselines):
    cache: dict = {}

    def curve_for(player):
        pid = player.get("id")
        if pid not in cache:
            cache[pid], _ = weekly_projection_curve(player, ctx)
        return cache[pid]

    return RoleScorer(ctx, baselines, role_weight_set(week_weights(ctx), ctx), curve_for=curve_for)


def _bench(pid, name, position, curve, **extra):
    row = {
        "id": pid,
        "name": name,
        "position": position,
        "lineup_slot": "BE",
        "lineup_slot_id": 20,
        "weekly_projections": curve,
    }
    row.update(extra)
    return row


def test_role_curves_cover_the_weeks_their_slot_is_for():
    ctx = _ctx()
    sets = role_weight_set(week_weights(ctx), ctx)
    stream = [week for week, value in sets[ROLE_STREAM].weights.items() if value > 0]
    stash = [week for week, value in sets[ROLE_STASH].weights.items() if value > 0]
    assert stream == [6, 7]  # the next couple of weeks only
    assert min(stash) > max(ctx.short_term_week_list)  # nothing in the near window
    for role_set in sets.values():
        assert round(sum(role_set.weights.values()), 9) == 1.0


def test_back_loaded_player_takes_a_stash_slot_and_front_loaded_a_stream_slot():
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    baselines = {"WR": 8.0}
    # Nothing now, strong later: the shape of an injury stash.
    back = _bench(1, "Back Later", "WR", {week: 18.0 for week in weeks[3:]})
    # Strong now, fading: the shape of a streamer.
    front = _bench(2, "Hot Now", "WR", {week: 18.0 for week in weeks[:2]} | {week: 8.5 for week in weeks[2:]})
    flat = _bench(3, "Same Every Week", "WR", {week: 12.0 for week in weeks})
    scorer = _scorer(ctx, baselines)
    roles = assign_roles([back, front, flat], scorer=scorer)
    assert roles[1] == ROLE_STASH
    assert roles[2] == ROLE_STREAM
    # A flat curve scores the same under any normalized weighting, so there is
    # nothing for a role to add and it stays ordinary depth.
    assert roles[3] == ROLE_REGULAR


def test_reserved_slot_counts_are_respected():
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    backs = [
        _bench(i, f"Back {i}", "WR", {week: 18.0 for week in weeks[3:]}) for i in range(1, 5)
    ]
    scorer = _scorer(ctx, {"WR": 8.0})
    roles = assign_roles(backs, scorer=scorer, stash_slots=2, stream_slots=0)
    assert sum(1 for role in roles.values() if role == ROLE_STASH) == 2
    assert sum(1 for role in roles.values() if role == ROLE_REGULAR) == 2


def test_roster_totals_do_not_reweight_a_player_by_their_slot():
    # A role curve is a yardstick for a head-to-head, not a discount on what a
    # player is worth to own: a season-long asset parked in a streaming slot
    # must not lose its rest-of-season value.
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    asset = _bench(1, "Season Long", "WR", {week: 15.0 for week in weeks[:2]} | {week: 14.0 for week in weeks[2:]})
    scorer = _scorer(ctx, {"WR": 8.0})
    roles = assign_roles([asset], scorer=scorer)
    assert roles[1] == ROLE_STREAM  # front-loaded enough to take the slot
    short, long = roster_totals([asset], scorer)
    assert long > 0  # rest-of-season value survives the label


def test_drop_goes_to_whoever_the_arrival_beats_worst_in_their_own_role():
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    baselines = {"WR": 8.0, "RB": 8.0}
    strong = _bench(1, "Keeper", "WR", {week: 17.0 for week in weeks})
    weak = _bench(2, "Redundant", "WR", {week: 8.5 for week in weeks})
    other = _bench(3, "Also Fine", "RB", {week: 15.0 for week in weeks})
    incoming = _bench(9, "Arrival", "WR", {week: 20.0 for week in weeks})
    roster = [strong, weak, other, incoming]
    scorer = _scorer(ctx, baselines)
    roles = assign_roles(roster, scorer=scorer)
    dropped = choose_forced_drops(
        roster, incoming=[incoming], drops_needed=1, scorer=scorer, roles=roles
    )
    assert [player["id"] for player in dropped] == [2]


def test_last_player_at_a_position_is_protected_unless_the_trade_covers_it():
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    baselines = {"WR": 8.0, "K": 8.0}
    # A kicker grades near replacement as an asset but is worth a slot a week.
    kicker = _bench(1, "Only Kicker", "K", {week: 8.2 for week in weeks})
    spare = _bench(2, "Spare WR", "WR", {week: 9.5 for week in weeks})
    incoming = _bench(9, "Arriving WR", "WR", {week: 20.0 for week in weeks})
    roster = [kicker, spare, incoming]
    scorer = _scorer(ctx, baselines)
    roles = assign_roles(roster, scorer=scorer)
    dropped = choose_forced_drops(
        roster, incoming=[incoming], drops_needed=1, scorer=scorer, roles=roles
    )
    assert [player["id"] for player in dropped] == [2]  # kicker protected

    # Trade brings a kicker, so the position is covered and the incumbent is
    # cuttable again.
    new_kicker = _bench(10, "Arriving K", "K", {week: 9.0 for week in weeks})
    roster = [kicker, spare, new_kicker]
    roles = assign_roles(roster, scorer=scorer)
    dropped = choose_forced_drops(
        roster, incoming=[new_kicker], drops_needed=1, scorer=scorer, roles=roles
    )
    assert [player["id"] for player in dropped] == [1]


def test_the_weakest_arrival_is_the_yardstick_not_the_best_one():
    # On a full roster the player who causes the squeeze is the last man in, so
    # a stud arriving alongside a spare part must not make every incumbent look
    # redundant.
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    baselines = {"WR": 8.0}
    stashed = _bench(1, "Stashed", "WR", {week: 16.0 for week in weeks[3:]})
    spare = _bench(2, "Spare", "WR", {week: 9.0 for week in weeks})
    near_only = _bench(9, "Two Week Spike", "WR", {week: 30.0 for week in weeks[:2]})
    late_monster = _bench(10, "Late Monster", "WR", {week: 40.0 for week in weeks[3:]})
    roster = [stashed, spare, near_only, late_monster]
    scorer = _scorer(ctx, baselines)
    roles = assign_roles(roster, scorer=scorer)
    assert roles[1] == ROLE_STASH

    # Each arrival is judged on the role it would occupy, and the weaker of the
    # two at its own best use is the marginal one.
    marginal = marginal_arrival([near_only, late_monster], scorer=scorer, roles=roles)
    assert marginal["id"] == 9

    dropped = choose_forced_drops(
        roster,
        incoming=[near_only, late_monster],
        drops_needed=1,
        scorer=scorer,
        roles=roles,
    )
    # The marginal arrival is a two-week spike, so it does not clear the stash
    # on the stash curve at all. The spare is the one it beats.
    assert [player["id"] for player in dropped] == [2]
    assert scorer.value(near_only, ROLE_STASH) < scorer.value(stashed, ROLE_STASH)
    assert scorer.value(near_only, ROLE_REGULAR) > scorer.value(spare, ROLE_REGULAR)


def test_best_arrival_as_yardstick_would_have_cut_the_stash():
    # Guard against regressing to the best-arrival rule: with the stud as the
    # yardstick the stash looks the most beaten, which is the wrong answer.
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    scorer = _scorer(ctx, {"WR": 8.0})
    stashed = _bench(1, "Stashed", "WR", {week: 16.0 for week in weeks[3:]})
    spare = _bench(2, "Spare", "WR", {week: 9.0 for week in weeks})
    late_monster = _bench(10, "Late Monster", "WR", {week: 40.0 for week in weeks[3:]})
    roles = assign_roles([stashed, spare, late_monster], scorer=scorer)
    stash_margin = scorer.value(late_monster, ROLE_STASH) - scorer.value(stashed, ROLE_STASH)
    spare_margin = scorer.value(late_monster, ROLE_REGULAR) - scorer.value(spare, ROLE_REGULAR)
    assert stash_margin > spare_margin  # why the best-arrival rule picked wrong


def test_no_drop_when_the_roster_already_fits():
    ctx = _ctx()
    scorer = _scorer(ctx, {"WR": 8.0})
    assert choose_forced_drops([], incoming=[], drops_needed=0, scorer=scorer, roles={}) == []


def test_a_slight_lean_is_not_a_specialism():
    # Qualification is a real threshold, not an epsilon: a player whose role
    # curve is only marginally better is ordinary depth.
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    scorer = _scorer(ctx, {"WR": 8.0})
    # 9.2 now, 9.0 later: leans front, nowhere near half a point a game.
    slight = _bench(1, "Slight Lean", "WR", {w: 9.2 for w in weeks[:2]} | {w: 9.0 for w in weeks[2:]})
    regular_value = scorer.value(slight, ROLE_REGULAR)
    assert 0 < scorer.value(slight, ROLE_STREAM) - regular_value < 0.5
    assert assign_roles([slight], scorer=scorer)[1] == ROLE_REGULAR

    # Same shape, far bigger gap: now it is a streamer.
    real = _bench(2, "Real Streamer", "WR", {w: 24.0 for w in weeks[:2]} | {w: 8.2 for w in weeks[2:]})
    assert scorer.value(real, ROLE_STREAM) - scorer.value(real, ROLE_REGULAR) >= 0.5
    assert assign_roles([real], scorer=scorer)[2] == ROLE_STREAM


def test_a_third_qualifier_worth_more_takes_the_slot():
    # Cap of two, and the slot goes to the highest value in the role -- not the
    # biggest gain, and not whoever was there first.
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    scorer = _scorer(ctx, {"WR": 8.0})
    weak_a = _bench(1, "Weak Stash A", "WR", {w: 14.0 for w in weeks[3:]})
    weak_b = _bench(2, "Weak Stash B", "WR", {w: 13.5 for w in weeks[3:]})
    strong = _bench(3, "Strong Stash", "WR", {w: 30.0 for w in weeks[3:]})

    two_only = assign_roles([weak_a, weak_b], scorer=scorer, stream_slots=0)
    assert two_only[1] == two_only[2] == ROLE_STASH  # both fit while there is room

    with_third = assign_roles([weak_a, weak_b, strong], scorer=scorer, stream_slots=0)
    held = {pid for pid, role in with_third.items() if role == ROLE_STASH}
    assert held == {1, 3}  # the strongest two by stash value
    assert with_third[2] == ROLE_REGULAR  # lowest-valued incumbent swapped out
    assert scorer.value(weak_b, ROLE_STASH) < scorer.value(weak_a, ROLE_STASH)


def test_marginal_arrival_averages_across_all_three_roles():
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    scorer = _scorer(ctx, {"WR": 8.0})
    # Useful only in a narrow window, so strong on one curve and thin overall.
    spike = _bench(9, "Spike", "WR", {w: 20.0 for w in weeks[:2]})
    allround = _bench(10, "All Round", "WR", {w: 15.0 for w in weeks})
    picked = marginal_arrival([spike, allround], scorer=scorer)

    def avg(player):
        return sum(scorer.value(player, r) for r in (ROLE_REGULAR, ROLE_STASH, ROLE_STREAM)) / 3

    assert avg(spike) < avg(allround)
    assert picked["id"] == 9
    # Judged on its single best curve instead, the spike would look like the
    # stronger piece and the yardstick would be wrong.
    assert scorer.value(spike, ROLE_STREAM) > scorer.value(allround, ROLE_STREAM)


def test_an_arrival_does_not_inherit_the_dropped_players_role():
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    scorer = _scorer(ctx, {"WR": 8.0, "RB": 8.0})
    # Holds the only stash slot.
    stashed = _bench(1, "Stashed", "WR", {w: 20.0 for w in weeks[3:]})
    spare = _bench(2, "Spare", "RB", {w: 9.0 for w in weeks})
    # Flat, so it qualifies for nothing however the roster shakes out.
    flat_arrival = _bench(9, "Flat Arrival", "WR", {w: 15.0 for w in weeks})
    roster = [stashed, spare, flat_arrival]
    roles = assign_roles(roster, scorer=scorer, stash_slots=1, stream_slots=1)
    assert roles[1] == ROLE_STASH
    assert roles[9] == ROLE_REGULAR

    dropped = choose_forced_drops(
        roster, incoming=[flat_arrival], drops_needed=1, scorer=scorer, roles=roles
    )
    after = [p for p in roster if p["id"] != dropped[0]["id"]]
    after_roles = assign_roles(after, scorer=scorer, stash_slots=1, stream_slots=1)
    # Whoever was cut, the arrival stays ordinary depth: roles are re-derived
    # from the criteria, never handed down from the player who left.
    assert after_roles[9] == ROLE_REGULAR


def test_a_defense_is_always_a_streaming_slot_and_eats_its_capacity():
    # Defenses are churned on matchup, never held for a season, so the label is
    # unconditional -- and it occupies a stream slot like any other tenant.
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    scorer = _scorer(ctx, {"D/ST": 5.0, "WR": 8.0})
    # Flat curve, starting slot: qualifies for nothing by the normal criteria.
    defense = _bench(1, "Some D/ST", "D/ST", {w: 7.0 for w in weeks}, lineup_slot="D/ST", lineup_slot_id=16)
    strong_streamer = _bench(2, "Hot Now", "WR", {w: 26.0 for w in weeks[:2]} | {w: 8.1 for w in weeks[2:]})
    other_streamer = _bench(3, "Also Hot", "WR", {w: 22.0 for w in weeks[:2]} | {w: 8.1 for w in weeks[2:]})

    roles = assign_roles([defense, strong_streamer, other_streamer], scorer=scorer, stream_slots=2, stash_slots=0)
    assert roles[1] == ROLE_STREAM  # unconditional, despite a flat curve
    # One stream slot is left, so only the more valuable streamer gets it.
    assert roles[2] == ROLE_STREAM
    assert roles[3] == ROLE_REGULAR
    assert scorer.value(strong_streamer, ROLE_STREAM) > scorer.value(other_streamer, ROLE_STREAM)


def test_two_defenses_consume_the_whole_stream_allowance():
    ctx = _ctx()
    weeks = ctx.remaining_weeks
    scorer = _scorer(ctx, {"D/ST": 5.0, "WR": 8.0})
    d1 = _bench(1, "D One", "D/ST", {w: 7.0 for w in weeks}, lineup_slot="D/ST", lineup_slot_id=16)
    d2 = _bench(2, "D Two", "D/ST", {w: 6.0 for w in weeks})
    streamer = _bench(3, "Hot Now", "WR", {w: 26.0 for w in weeks[:2]} | {w: 8.1 for w in weeks[2:]})
    roles = assign_roles([d1, d2, streamer], scorer=scorer, stream_slots=2, stash_slots=0)
    assert roles[1] == ROLE_STREAM and roles[2] == ROLE_STREAM
    assert roles[3] == ROLE_REGULAR  # no stream capacity left for a real streamer
