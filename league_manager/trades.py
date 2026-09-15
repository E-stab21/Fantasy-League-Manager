"""Grade redraft trades with roster surplus and lineup surplus.

Roster surplus = change in waiver-replacement VORP (asset pile).
Lineup surplus = change in optimal starting-lineup points (what you start).

Both are computed for ST and LT, then mixed with horizon weights (contender /
bubble / rebuilder) and structure weights (roster vs lineup). Stud tax and
lineup-hole hacks are replaced by lineup surplus.

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
    slot_name,
)
from league_manager.value import (
    LeagueContext,
    PlayerValue,
    infer_window,
    structure_weights,
    value_player,
    window_weights,
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


def _horizon_points(value: PlayerValue, horizon: str) -> float:
    return float(value.st_points if horizon == "st" else value.lt_points)


def optimal_lineup_points(
    roster: list[dict[str, Any]],
    *,
    context: LeagueContext,
    baselines: dict[str, float],
    window: str,
    horizon: str,
    slot_template: list[int] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Greedy fill of starter slots maximizing ST or LT points."""
    slots = slot_template or starter_slot_template(roster)
    valued = {
        player.get("id"): value_player(player, context, baselines, window=window)
        for player in roster
        if player.get("id") is not None and _slot_id(player) != IR_SLOT
    }
    available = [player for player in roster if player.get("id") in valued]
    assigned: set[Any] = set()
    picks: list[dict[str, Any]] = []
    total = 0.0
    for slot in slots:
        candidates = [
            player
            for player in available
            if player.get("id") not in assigned
            and player_fits_slot(str(player.get("position") or ""), slot)
        ]
        candidates.sort(
            key=lambda player: _horizon_points(valued[player["id"]], horizon),
            reverse=True,
        )
        if not candidates:
            continue
        pick = candidates[0]
        pid = pick.get("id")
        assigned.add(pid)
        pts = _horizon_points(valued[pid], horizon)
        total += pts
        picks.append(
            {
                "id": pid,
                "name": pick.get("name"),
                "position": pick.get("position"),
                "slot": slot_name(slot),
                "points": round(pts, 2),
            }
        )
    return round(total, 2), picks


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
    """Lineup-shaped VORP when we cannot run an optimal lineup delta."""
    floor = float(floor_weekly.get(value.position) or 0.0)
    st = value.st_points - floor * value.st_games
    lt = value.lt_points - floor * value.lt_games
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
) -> dict[str, Any]:
    st_w, lt_w = window_weights(window)
    roster_w, lineup_w = structure_weights(window)

    send_st = sum(item.st_vorp for item in send_values)
    send_lt = sum(item.lt_vorp for item in send_values)
    recv_st = sum(item.st_vorp for item in recv_values)
    recv_lt = sum(item.lt_vorp for item in recv_values)
    roster_delta_st = recv_st - send_st
    roster_delta_lt = recv_lt - send_lt

    lineup_mode = "optimal"
    lineup_delta_st = 0.0
    lineup_delta_lt = 0.0
    lineup_before_st: list[dict[str, Any]] = []
    lineup_after_st: list[dict[str, Any]] = []
    notes = [
        f"Redraft window is {window} (ST {st_w:.0%}/LT {lt_w:.0%}; "
        f"roster {roster_w:.0%}/lineup {lineup_w:.0%}).",
        "Roster surplus uses waiver replacement VORP; lineup surplus uses starting XI points.",
    ]

    if our_roster and baselines is not None:
        slots = starter_slot_template(our_roster)
        before = list(our_roster)
        after = apply_trade_to_roster(before, send_players, recv_players)
        before_st, lineup_before_st = optimal_lineup_points(
            before, context=context, baselines=baselines, window=window, horizon="st", slot_template=slots
        )
        after_st, lineup_after_st = optimal_lineup_points(
            after, context=context, baselines=baselines, window=window, horizon="st", slot_template=slots
        )
        before_lt, _ = optimal_lineup_points(
            before, context=context, baselines=baselines, window=window, horizon="lt", slot_template=slots
        )
        after_lt, _ = optimal_lineup_points(
            after, context=context, baselines=baselines, window=window, horizon="lt", slot_template=slots
        )
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
    blended = st_w * delta_st + lt_w * delta_lt

    if lineup_delta_st > 1 or lineup_delta_lt > 1:
        notes.append("Lineup improves — consolidation / upgrade shows up without a stud tax.")
    if lineup_delta_st < -1 or lineup_delta_lt < -1:
        notes.append("Lineup worsens — depth for a starter (or a hole) is priced in lineup surplus.")
    if context.current_week >= context.regular_season_end:
        notes.append("Regular season is over or nearly over; playoff schedule dominates.")

    return {
        "window": window,
        "weights": {
            "st": st_w,
            "lt": lt_w,
            "roster": roster_w,
            "lineup": lineup_w,
        },
        "grade": letter_grade(blended),
        "verdict": verdict(blended),
        "blended": round(blended, 2),
        "delta_st": round(delta_st, 2),
        "delta_lt": round(delta_lt, 2),
        "roster": {
            "delta_st": round(roster_delta_st, 2),
            "delta_lt": round(roster_delta_lt, 2),
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
) -> dict[str, Any]:
    if not send_ids or not receive_ids:
        raise ValueError("Trade grade needs at least one send id and one receive id")
    send_players = [_lookup(players, player_id) for player_id in send_ids]
    recv_players = [_lookup(players, player_id) for player_id in receive_ids]
    resolved_window = infer_window(context, window)
    send_values = [value_player(player, context, baselines, window=resolved_window) for player in send_players]
    recv_values = [value_player(player, context, baselines, window=resolved_window) for player in recv_players]
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
    )


def _summary(
    window: str,
    roster_st: float,
    roster_lt: float,
    lineup_st: float,
    lineup_lt: float,
    blended: float,
) -> str:
    return (
        f"As a {window}: roster ST {roster_st:+.1f} / LT {roster_lt:+.1f}; "
        f"lineup ST {lineup_st:+.1f} / LT {lineup_lt:+.1f} "
        f"(blended {blended:+.1f})."
    )
