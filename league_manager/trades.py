"""Grade redraft trades with roster surplus and lineup surplus.

Roster surplus = after-trade VORP minus before-trade VORP (asset pile).
On a full roster, extra incoming players force a drop of the lowest remaining
VORP (not the players just received); that cut is priced like part of the trade.
Empty spots after a net-loss deal are replacement-level (~0 VORP).
Lineup surplus = change in starting-lineup points (what you actually start),
re-optimized every week, because lineups are reset weekly. Each remaining week
is filled on its own projections and the weeks are then weighted, so a player
available for only part of the stretch counts in the weeks he plays and is
replaced in the rest.

Both are split into the short-term window and everything after, then mixed with
structure weights (roster vs lineup). Stud tax and lineup-hole hacks are
replaced by lineup surplus.

When the full roster is unknown (public accept calibration), lineup surplus
uses a position's typical starter floor instead of an optimal lineup delta.
Their acceptance distance still uses ESPN face outside this module.
"""

from __future__ import annotations

from typing import Any

from league_manager.slots import (
    IR_SLOT,
    is_starter_slot,
    normalize_position,
    player_fits_slot,
    slot_eligibility_size,
    slot_name,
)
from league_manager.leverage import ROLE_REGULAR, WeekWeights, week_weights
from league_manager.roster_roles import (
    RoleScorer,
    _position_counts,
    assign_roles,
    choose_forced_drops,
    role_weight_set,
    roster_totals,
)
from league_manager.value import (
    LeagueContext,
    PlayerValue,
    infer_window,
    structure_weights,
    value_player,
    weekly_projection_curve,
)

# Typical ESPN 10-team starter set when roster slots are missing.
DEFAULT_STARTER_SLOTS = [0, 2, 2, 4, 4, 6, 23, 16, 17]


def _lookup(players: dict[Any, dict[str, Any]], player_id: int) -> dict[str, Any]:
    if player_id in players:
        return players[player_id]
    for key, player in players.items():
        if str(key) == str(player_id) or str(player.get("id")) == str(player_id):
            return player
    raise KeyError(f"Player {player_id} is not on any roster or the free-agent sample")


def _slot_id(player: dict[str, Any]) -> int | None:
    raw = player.get("lineup_slot_id")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def starter_slot_template(roster: list[dict[str, Any]] | None) -> list[int]:
    """Starter slots from the current roster layout, else a standard template."""
    if not roster:
        return list(DEFAULT_STARTER_SLOTS)
    slots = [
        slot
        for slot in (_slot_id(player) for player in roster)
        if slot is not None and is_starter_slot(slot)
    ]
    return slots or list(DEFAULT_STARTER_SLOTS)


def apply_trade_to_roster(
    roster: list[dict[str, Any]],
    send_players: list[dict[str, Any]],
    recv_players: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    send_ids = {player.get("id") for player in send_players}
    after = [player for player in roster if player.get("id") not in send_ids]
    # Received players sit on bench until lineup re-optimized.
    for player in recv_players:
        row = dict(player)
        row.setdefault("lineup_slot_id", 20)
        row.setdefault("is_starter", False)
        after.append(row)
    return after


def _is_ir(player: dict[str, Any]) -> bool:
    return _slot_id(player) == IR_SLOT


def _active_players(roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [player for player in roster if not _is_ir(player)]


def resolve_roster_cap(roster: list[dict[str, Any]], context: LeagueContext) -> int:
    """Active spots. League setting if known, else current occupancy (assume full)."""
    if context.roster_cap is not None and int(context.roster_cap) > 0:
        return int(context.roster_cap)
    return len(_active_players(roster))


def _drop_row(
    player: dict[str, Any],
    role: str,
    scorer: RoleScorer,
) -> dict[str, Any]:
    short, long = scorer.split(player, role)
    return {
        "id": player.get("id"),
        "name": player.get("name"),
        "position": normalize_position(str(player.get("position") or "")),
        "role": role,
        "st_vorp": round(short, 2),
        "lt_vorp": round(long, 2),
        "blended": round(short + long, 2),
    }


def choose_drops_exactly(
    roster: list[dict[str, Any]],
    *,
    incoming: list[dict[str, Any]],
    drops_needed: int,
    scorer: RoleScorer,
    context: LeagueContext,
    weights: WeekWeights,
    slots: list[int],
    roster_w: float,
    lineup_w: float,
    curve_cache: dict[Any, dict[int, float | None]] | None = None,
) -> list[dict[str, Any]]:
    """Try every legal cut and keep whichever leaves the most total value.

    The margin rule in `roster_roles.choose_forced_drops` is a fast proxy; this
    is the thing the proxy approximates, scoring each candidate roster the same
    way the trade itself is scored (roster VORP plus the weekly-reoptimized
    lineup). Measured at ~12ms a trade against ~0.08ms for the proxy, and the
    two agree on roughly 87% of real packages -- affordable for grading one
    trade, not for ranking fifty thousand.
    """
    if drops_needed <= 0:
        return []
    incoming_ids = {player.get("id") for player in incoming}
    remaining = list(roster)
    dropped: list[dict[str, Any]] = []
    for _ in range(drops_needed):
        counts = _position_counts(remaining)
        best: tuple[float, dict[str, Any]] | None = None
        fallback: tuple[float, dict[str, Any]] | None = None
        for candidate in _active_players(remaining):
            pid = candidate.get("id")
            if pid is None or pid in incoming_ids:
                continue
            after = [player for player in remaining if player.get("id") != pid]
            roster_st, roster_lt = roster_totals(after, scorer)
            lineup_st, lineup_lt, _picks = weekly_lineup_points(
                after,
                context=context,
                weights=weights,
                curves=lineup_curves(after, context, cache=curve_cache),
                slot_template=slots,
            )
            total = roster_w * (roster_st + roster_lt) + lineup_w * (lineup_st + lineup_lt)
            pos = normalize_position(str(candidate.get("position") or ""))
            if counts.get(pos, 0) <= 1:
                # Last at the position: only cut them if nothing else is legal.
                if fallback is None or total > fallback[0]:
                    fallback = (total, candidate)
                continue
            if best is None or total > best[0]:
                best = (total, candidate)
        pick = best or fallback
        if pick is None:
            break
        cut = pick[1]
        dropped.append(cut)
        cut_id = cut.get("id")
        remaining = [player for player in remaining if player.get("id") != cut_id]
    return dropped


def fit_roster_to_cap(
    roster: list[dict[str, Any]],
    *,
    cap: int,
    incoming: list[dict[str, Any]],
    scorer: RoleScorer,
    roles: dict[Any, str],
    exact: bool = False,
    exact_kwargs: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cut down to the cap, choosing by who the arrivals make redundant.

    See `roster_roles.choose_forced_drops`. IR does not count toward the cap,
    and an empty leftover spot after a net-loss deal is simply absent -- no
    phantom replacement-level player is added.
    """
    after = list(roster)
    if cap <= 0:
        return after, []
    drops_needed = max(0, len(_active_players(after)) - cap)
    if drops_needed <= 0:
        return after, []
    if exact:
        dropped = choose_drops_exactly(
            after,
            incoming=incoming,
            drops_needed=drops_needed,
            scorer=scorer,
            **(exact_kwargs or {}),
        )
    else:
        dropped = choose_forced_drops(
            after,
            incoming=incoming,
            drops_needed=drops_needed,
            scorer=scorer,
            roles=roles,
        )
    dropped_ids = {player.get("id") for player in dropped}
    after = [player for player in after if player.get("id") not in dropped_ids]
    return after, dropped


def lineup_curves(
    roster: list[dict[str, Any]],
    context: LeagueContext,
    *,
    cache: dict[Any, dict[int, float | None]] | None = None,
) -> dict[Any, dict[int, float | None]]:
    """Per-week projections for everyone who could start, keyed by player id.

    Built once and reused across lineups: a trade only changes who is on the
    roster, never what a given player is projected for in a given week.
    """
    curves: dict[Any, dict[int, float | None]] = {}
    for player in roster:
        pid = player.get("id")
        if pid is None or _slot_id(player) == IR_SLOT:
            continue
        if cache is not None and pid in cache:
            curves[pid] = cache[pid]
            continue
        curve, _ = weekly_projection_curve(player, context)
        if cache is not None:
            cache[pid] = curve
        curves[pid] = curve
    return curves


def fill_lineup(
    roster: list[dict[str, Any]],
    slots: list[int],
    curves: dict[Any, dict[int, float | None]],
    week: int,
) -> tuple[float, list[dict[str, Any]]]:
    """Best lineup for one week. Slots must already be restrictive-first."""
    assigned: set[Any] = set()
    picks: list[dict[str, Any]] = []
    total = 0.0
    for slot in slots:
        best: dict[str, Any] | None = None
        best_points = 0.0
        for player in roster:
            pid = player.get("id")
            if pid is None or pid in assigned or pid not in curves:
                continue
            points = curves[pid].get(week)
            # None means the player is not playing that week, so they are not a
            # candidate and whoever is next fills the slot instead.
            if points is None:
                continue
            if not player_fits_slot(str(player.get("position") or ""), slot):
                continue
            if best is None or points > best_points:
                best, best_points = player, points
        if best is None:
            continue
        assigned.add(best.get("id"))
        total += best_points
        picks.append(
            {
                "id": best.get("id"),
                "name": best.get("name"),
                "position": best.get("position"),
                "slot": slot_name(slot),
                "points": round(best_points, 2),
            }
        )
    return total, picks


def weekly_lineup_points(
    roster: list[dict[str, Any]],
    *,
    context: LeagueContext,
    weights: WeekWeights,
    curves: dict[Any, dict[int, float | None]],
    slot_template: list[int] | None = None,
) -> tuple[float, float, dict[int, list[dict[str, Any]]]]:
    """Weighted starting-lineup points, re-optimized every week.

    Lineups are reset weekly, so each week is filled on its own projections and
    the weeks are then weighted. A player available for only part of the stretch
    counts in the weeks he plays and is simply replaced in the rest, which one
    lineup held fixed across the whole horizon cannot express.

    Returns (short_term, long_term, picks per week).
    """
    slots = sorted(
        slot_template if slot_template is not None else starter_slot_template(roster),
        key=slot_eligibility_size,
    )
    short_weeks = set(context.short_term_week_list)
    short_total = long_total = 0.0
    picks_by_week: dict[int, list[dict[str, Any]]] = {}
    for week in context.remaining_weeks:
        weight = weights.weight(week)
        if weight <= 0:
            continue
        total, picks = fill_lineup(roster, slots, curves, week)
        picks_by_week[week] = picks
        if week in short_weeks:
            short_total += weight * total
        else:
            long_total += weight * total
    return short_total, long_total, picks_by_week


def starter_floor_baselines(
    pool: list[dict[str, Any]],
    *,
    team_count: int = 10,
) -> dict[str, float]:
    """Weekly rate of a typical worst starter by position (for roster-less grading)."""
    by_pos: dict[str, list[float]] = {}
    for player in pool:
        pos = normalize_position(str(player.get("position") or ""))
        if not pos:
            continue
        rate = float(player.get("projected_points") or player.get("sleeper_projected_points") or 0.0)
        if rate <= 0:
            continue
        by_pos.setdefault(pos, []).append(rate)
    floors = {
        "QB": max(team_count - 1, 0),
        "RB": max(team_count * 2 + 1, 0),
        "WR": max(team_count * 3 - 1, 0),
        "TE": max(team_count + 1, 0),
        "K": max(team_count - 1, 0),
        "D/ST": max(team_count - 1, 0),
    }
    out: dict[str, float] = {}
    for pos, rates in by_pos.items():
        rates.sort(reverse=True)
        idx = floors.get(pos, max(team_count - 1, 0))
        idx = min(idx, len(rates) - 1)
        out[pos] = max(0.0, float(rates[idx]))
    return out


def lineup_vorp_vs_floor(
    value: PlayerValue,
    floor_weekly: dict[str, float],
) -> tuple[float, float]:
    """Lineup-shaped VORP when we cannot run an optimal lineup delta.

    `st_points` / `lt_points` are week-weighted, so the floor is charged against
    each split's share of the season's weight, not its raw game count.
    """
    floor = float(floor_weekly.get(value.position) or 0.0)
    st_weight = float(value.short_term_weight)
    st = value.st_points - floor * st_weight
    lt = value.lt_points - floor * max(0.0, 1.0 - st_weight)
    return st, lt


def letter_grade(blended: float) -> str:
    if blended >= 12:
        return "A+"
    if blended >= 8:
        return "A"
    if blended >= 4:
        return "B+"
    if blended >= 1.5:
        return "B"
    if blended >= -1.5:
        return "C"
    if blended >= -4:
        return "C-"
    if blended >= -8:
        return "D"
    return "F"


def verdict(blended: float) -> str:
    if blended >= 4:
        return "accept"
    if blended >= 1.5:
        return "lean_accept"
    if blended >= -1.5:
        return "even"
    if blended >= -4:
        return "lean_reject"
    return "reject"


def grade_valued_trade(
    *,
    send_values: list[PlayerValue],
    recv_values: list[PlayerValue],
    send_players: list[dict[str, Any]],
    recv_players: list[dict[str, Any]],
    context: LeagueContext,
    window: str,
    our_roster: list[dict[str, Any]] | None = None,
    baselines: dict[str, float] | None = None,
    starter_floors: dict[str, float] | None = None,
    weights: WeekWeights | None = None,
    curve_cache: dict[Any, dict[int, float | None]] | None = None,
    exact_drop: bool = False,
) -> dict[str, Any]:
    weights = weights or week_weights(context)
    short_weeks = set(context.short_term_week_list)
    st_w = weights.mass(short_weeks)
    lt_w = max(0.0, 1.0 - st_w)
    roster_w, lineup_w = structure_weights(window)

    send_st = sum(item.st_vorp for item in send_values)
    send_lt = sum(item.lt_vorp for item in send_values)
    recv_st = sum(item.st_vorp for item in recv_values)
    recv_lt = sum(item.lt_vorp for item in recv_values)
    roster_delta_st = recv_st - send_st
    roster_delta_lt = recv_lt - send_lt
    dropped_values: list[dict[str, Any]] = []
    roster_before_st = roster_before_lt = None
    roster_after_st = roster_after_lt = None
    roster_cap: int | None = None

    lineup_mode = "optimal"
    lineup_delta_st = 0.0
    lineup_delta_lt = 0.0
    lineup_before_st: list[dict[str, Any]] = []
    lineup_after_st: list[dict[str, Any]] = []
    notes = [
        f"Weeks are weighted by {weights.scheme} ({st_w:.0%} of the season's weight "
        f"sits in the next {len(short_weeks)} weeks, {lt_w:.0%} after); "
        f"roster {roster_w:.0%}/lineup {lineup_w:.0%}.",
        "Roster surplus is after minus before (waiver-replacement VORP); "
        "lineup surplus uses starting XI points.",
        "Values are points per weighted game above replacement.",
    ]
    notes.extend(weights.notes)

    if our_roster and baselines is not None:
        if curve_cache is None:
            curve_cache = {}

        def curve_for(player: dict[str, Any]) -> dict[int, float | None]:
            pid = player.get("id")
            cached = curve_cache.get(pid)
            if cached is None:
                cached, _ = weekly_projection_curve(player, context)
                curve_cache[pid] = cached
            return cached

        role_sets = role_weight_set(weights, context)
        scorer = RoleScorer(context, baselines, role_sets, curve_for=curve_for)
        slot_plan = {
            "stash_slots": context.stash_slots,
            "stream_slots": context.stream_slots,
            "gain_threshold": context.role_gain_threshold,
        }

        slots = starter_slot_template(our_roster)
        before = list(our_roster)
        raw_after = apply_trade_to_roster(before, send_players, recv_players)
        roster_cap = resolve_roster_cap(before, context)
        roles_before = assign_roles(before, scorer=scorer, **slot_plan)
        # Roles are reassigned on the post-trade roster: an arrival can take a
        # reserved slot, and the drop is judged against whoever holds what.
        roles_raw_after = assign_roles(raw_after, scorer=scorer, **slot_plan)
        after, dropped_players = fit_roster_to_cap(
            raw_after,
            cap=roster_cap,
            incoming=recv_players,
            scorer=scorer,
            roles=roles_raw_after,
            exact=exact_drop,
            exact_kwargs={
                "context": context,
                "weights": weights,
                "slots": slots,
                "roster_w": roster_w,
                "lineup_w": lineup_w,
                "curve_cache": {},
            },
        )
        roles_after = (
            roles_raw_after
            if not dropped_players
            else assign_roles(after, scorer=scorer, **slot_plan)
        )
        dropped_values = [
            _drop_row(player, roles_raw_after.get(player.get("id"), ROLE_REGULAR), scorer)
            for player in dropped_players
        ]
        roster_before_st, roster_before_lt = roster_totals(before, scorer)
        roster_after_st, roster_after_lt = roster_totals(after, scorer)
        roster_delta_st = roster_after_st - roster_before_st
        roster_delta_lt = roster_after_lt - roster_before_lt
        if dropped_values:
            drop_names = ", ".join(
                f"{item['name'] or item['id']} ({item['position']})" for item in dropped_values
            )
            notes.append(
                f"Roster is full ({roster_cap} active spots); dropping {drop_names} "
                "so the extra incoming player(s) fit. Their VORP is in roster surplus "
                "(after minus before), as if they were on the send side."
            )
        curves = lineup_curves(before, context, cache=curve_cache)
        curves.update(lineup_curves(after, context, cache=curve_cache))
        before_st, before_lt, before_picks = weekly_lineup_points(
            before, context=context, weights=weights, curves=curves, slot_template=slots
        )
        after_st, after_lt, after_picks = weekly_lineup_points(
            after, context=context, weights=weights, curves=curves, slot_template=slots
        )
        current = int(context.current_week)
        lineup_before_st = before_picks.get(current, [])
        lineup_after_st = after_picks.get(current, [])
        lineup_delta_st = after_st - before_st
        lineup_delta_lt = after_lt - before_lt
        notes.append(
            f"Lineup points ST {before_st:.1f}->{after_st:.1f} ({lineup_delta_st:+.1f}); "
            f"LT {before_lt:.1f}->{after_lt:.1f} ({lineup_delta_lt:+.1f})."
        )
    else:
        lineup_mode = "starter_floor"
        floors = starter_floors or {}
        send_lu_st = send_lu_lt = recv_lu_st = recv_lu_lt = 0.0
        for item in send_values:
            st, lt = lineup_vorp_vs_floor(item, floors)
            send_lu_st += st
            send_lu_lt += lt
        for item in recv_values:
            st, lt = lineup_vorp_vs_floor(item, floors)
            recv_lu_st += st
            recv_lu_lt += lt
        lineup_delta_st = recv_lu_st - send_lu_st
        lineup_delta_lt = recv_lu_lt - send_lu_lt
        notes.append(
            "Lineup surplus uses typical starter-floor VORP (no full roster available)."
        )

    delta_st = roster_w * roster_delta_st + lineup_w * lineup_delta_st
    delta_lt = roster_w * roster_delta_lt + lineup_w * lineup_delta_lt
    # The splits are already week-weighted and do not overlap, so they add
    # rather than needing a second round of short-term / long-term weighting.
    blended = delta_st + delta_lt

    if lineup_delta_st > 1 or lineup_delta_lt > 1:
        notes.append("Lineup improves — consolidation / upgrade shows up without a stud tax.")
    if lineup_delta_st < -1 or lineup_delta_lt < -1:
        notes.append("Lineup worsens — depth for a starter (or a hole) is priced in lineup surplus.")
    if context.current_week >= context.regular_season_end:
        notes.append("Regular season is over or nearly over; playoff schedule dominates.")

    return {
        "window": window,
        "weights": {
            "st": round(st_w, 4),
            "lt": round(lt_w, 4),
            "roster": roster_w,
            "lineup": lineup_w,
            "scheme": weights.scheme,
            "playoff_odds": round(weights.playoff_odds, 3),
        },
        "grade": letter_grade(blended),
        "verdict": verdict(blended),
        "blended": round(blended, 2),
        "delta_st": round(delta_st, 2),
        "delta_lt": round(delta_lt, 2),
        "dropped": dropped_values,
        "roster": {
            "delta_st": round(roster_delta_st, 2),
            "delta_lt": round(roster_delta_lt, 2),
            "before_st": None if roster_before_st is None else round(roster_before_st, 2),
            "before_lt": None if roster_before_lt is None else round(roster_before_lt, 2),
            "after_st": None if roster_after_st is None else round(roster_after_st, 2),
            "after_lt": None if roster_after_lt is None else round(roster_after_lt, 2),
            "cap": roster_cap,
        },
        "lineup": {
            "mode": lineup_mode,
            "delta_st": round(lineup_delta_st, 2),
            "delta_lt": round(lineup_delta_lt, 2),
            "before_st": lineup_before_st,
            "after_st": lineup_after_st,
        },
        # Removed from the blend; kept at 0 so older callers do not break.
        "stud_tax": 0.0,
        "lineup_hole_penalty": 0.0,
        "send": [item.to_dict() for item in send_values],
        "receive": [item.to_dict() for item in recv_values],
        "send_totals": {"st_vorp": round(send_st, 2), "lt_vorp": round(send_lt, 2)},
        "receive_totals": {"st_vorp": round(recv_st, 2), "lt_vorp": round(recv_lt, 2)},
        "notes": notes,
        "summary": _summary(
            window,
            roster_delta_st,
            roster_delta_lt,
            lineup_delta_st,
            lineup_delta_lt,
            blended,
            dropped_values,
        ),
    }


def grade_trade(
    *,
    send_ids: list[int],
    receive_ids: list[int],
    players: dict[Any, dict[str, Any]],
    context: LeagueContext,
    baselines: dict[str, float],
    window: str | None = None,
    our_roster: list[dict[str, Any]] | None = None,
    starter_floors: dict[str, float] | None = None,
    weights: WeekWeights | None = None,
    curve_cache: dict[Any, dict[int, float | None]] | None = None,
    exact_drop: bool = True,
) -> dict[str, Any]:
    if not send_ids or not receive_ids:
        raise ValueError("Trade grade needs at least one send id and one receive id")
    send_players = [_lookup(players, player_id) for player_id in send_ids]
    recv_players = [_lookup(players, player_id) for player_id in receive_ids]
    resolved_window = infer_window(context, window)
    weights = weights or week_weights(context)
    send_values = [
        value_player(player, context, baselines, window=resolved_window, weights=weights)
        for player in send_players
    ]
    recv_values = [
        value_player(player, context, baselines, window=resolved_window, weights=weights)
        for player in recv_players
    ]
    roster = our_roster
    if roster is None:
        # Prefer full roster if callers stuffed team_id onto players.
        team_ids = {p.get("team_id") for p in send_players if p.get("team_id") is not None}
        if len(team_ids) == 1:
            tid = next(iter(team_ids))
            roster = [p for p in players.values() if p.get("team_id") == tid]
    return grade_valued_trade(
        send_values=send_values,
        recv_values=recv_values,
        send_players=send_players,
        recv_players=recv_players,
        context=context,
        window=resolved_window,
        our_roster=roster,
        baselines=baselines,
        starter_floors=starter_floors,
        weights=weights,
        curve_cache=curve_cache,
        exact_drop=exact_drop,
    )


def _summary(
    window: str,
    roster_st: float,
    roster_lt: float,
    lineup_st: float,
    lineup_lt: float,
    blended: float,
    dropped: list[dict[str, Any]] | None = None,
) -> str:
    drop_bit = ""
    if dropped:
        names = ", ".join(str(item["name"] or item["id"]) for item in dropped)
        drop_bit = f"; drop {names} to stay at cap"
    return (
        f"As a {window}: roster ST {roster_st:+.1f} / LT {roster_lt:+.1f}; "
        f"lineup ST {lineup_st:+.1f} / LT {lineup_lt:+.1f} "
        f"(blended {blended:+.1f}){drop_bit}."
    )
