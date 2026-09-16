"""Grade redraft trades with roster surplus and lineup surplus.

Roster surplus = after-trade VORP minus before-trade VORP (asset pile).
On a full roster, extra incoming players force a drop of the lowest remaining
VORP (not the players just received); that cut is priced like part of the trade.
Empty spots after a net-loss deal are replacement-level (~0 VORP).
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


def _is_ir(player: dict[str, Any]) -> bool:
    return _slot_id(player) == IR_SLOT


def _active_players(roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [player for player in roster if not _is_ir(player)]


def resolve_roster_cap(roster: list[dict[str, Any]], context: LeagueContext) -> int:
    """Active spots. League setting if known, else current occupancy (assume full)."""
    if context.roster_cap is not None and int(context.roster_cap) > 0:
        return int(context.roster_cap)
    return len(_active_players(roster))


def _drop_row(value: PlayerValue) -> dict[str, Any]:
    return {
        "id": value.id,
        "name": value.name,
        "position": value.position,
        "st_vorp": round(value.st_vorp, 2),
        "lt_vorp": round(value.lt_vorp, 2),
        "blended": round(value.blended, 2),
    }


def _roster_vorp_totals(
    roster: list[dict[str, Any]],
    value_of,
) -> tuple[float, float]:
    st = lt = 0.0
    seen: set[Any] = set()
    for player in roster:
        pid = player.get("id")
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        value = value_of(player)
        st += value.st_vorp
        lt += value.lt_vorp
    return st, lt


def fit_roster_to_cap(
    roster: list[dict[str, Any]],
    *,
    cap: int,
    protected_ids: set[Any],
    value_of,
) -> tuple[list[dict[str, Any]], list[PlayerValue]]:
    """Drop lowest-VORP leftovers until active size fits the cap.

    Incoming receive players are protected so the trade is actually kept.
    IR does not count toward the cap. Empty leftover spots are just absent
    (replacement-level, ~0 VORP) — no phantom player is added.
    """
    after = list(roster)
    dropped: list[PlayerValue] = []
    if cap <= 0:
        return after, dropped
    keep_ids = set(protected_ids)
    while len(_active_players(after)) > cap:
        active = _active_players(after)
        candidates = [player for player in active if player.get("id") not in keep_ids]
        if not candidates:
            candidates = list(active)
        candidates.sort(
            key=lambda player: (
                value_of(player).blended,
                1 if player.get("is_starter") else 0,
                value_of(player).lt_vorp,
                value_of(player).st_vorp,
            )
        )
        cut = candidates[0]
        cut_id = cut.get("id")
        dropped.append(value_of(cut))
        after = [player for player in after if player.get("id") != cut_id]
    return after, dropped


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
    dropped_values: list[PlayerValue] = []
    roster_before_st = roster_before_lt = None
    roster_after_st = roster_after_lt = None
    roster_cap: int | None = None

    lineup_mode = "optimal"
    lineup_delta_st = 0.0
    lineup_delta_lt = 0.0
    lineup_before_st: list[dict[str, Any]] = []
    lineup_after_st: list[dict[str, Any]] = []
    notes = [
        f"Redraft window is {window} (ST {st_w:.0%}/LT {lt_w:.0%}; "
        f"roster {roster_w:.0%}/lineup {lineup_w:.0%}).",
        "Roster surplus is after minus before (waiver-replacement VORP); "
        "lineup surplus uses starting XI points.",
    ]

    if our_roster and baselines is not None:
        valued_cache = {item.id: item for item in send_values + recv_values}

        def value_of(player: dict[str, Any]) -> PlayerValue:
            pid = player.get("id")
            if pid in valued_cache:
                return valued_cache[pid]
            valued = value_player(player, context, baselines, window=window)
            valued_cache[pid] = valued
            return valued

        slots = starter_slot_template(our_roster)
        before = list(our_roster)
        raw_after = apply_trade_to_roster(before, send_players, recv_players)
        roster_cap = resolve_roster_cap(before, context)
        after, dropped_values = fit_roster_to_cap(
            raw_after,
            cap=roster_cap,
            protected_ids={player.get("id") for player in recv_players},
            value_of=value_of,
        )
        roster_before_st, roster_before_lt = _roster_vorp_totals(before, value_of)
        roster_after_st, roster_after_lt = _roster_vorp_totals(after, value_of)
        roster_delta_st = roster_after_st - roster_before_st
        roster_delta_lt = roster_after_lt - roster_before_lt
        if dropped_values:
            drop_names = ", ".join(
                f"{item.name or item.id} ({item.position})" for item in dropped_values
            )
            notes.append(
                f"Roster is full ({roster_cap} active spots); dropping {drop_names} "
                "so the extra incoming player(s) fit. Their VORP is in roster surplus "
                "(after minus before), as if they were on the send side."
            )
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
        "dropped": [_drop_row(item) for item in dropped_values],
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
    dropped: list[PlayerValue] | None = None,
) -> str:
    drop_bit = ""
    if dropped:
        names = ", ".join(str(item.name or item.id) for item in dropped)
        drop_bit = f"; drop {names} to stay at cap"
    return (
        f"As a {window}: roster ST {roster_st:+.1f} / LT {roster_lt:+.1f}; "
        f"lineup ST {lineup_st:+.1f} / LT {lineup_lt:+.1f} "
        f"(blended {blended:+.1f}){drop_bit}."
    )
