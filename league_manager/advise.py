"""Lineup and waiver recommendations from public projections, not a custom model."""

from __future__ import annotations

from typing import Any

from league_manager.projections import primary_projection, projection_source
from league_manager.slots import (
    BENCH_SLOT,
    IR_SLOT,
    is_starter_slot,
    parse_slot,
    player_fits_slot,
    slot_name,
)
from league_manager.trades import grade_valued_trade
from league_manager.value import (
    LeagueContext,
    infer_window,
    structure_weights,
    value_player,
    window_weights,
)


def _slot_id(player: dict[str, Any]) -> int | None:
    raw = player.get("lineup_slot_id")
    if raw is None and player.get("lineup_slot"):
        try:
            return parse_slot(player["lineup_slot"])
        except ValueError:
            return None
    return int(raw) if raw is not None else None


def _sit_reason(player: dict[str, Any]) -> str:
    if player.get("injured"):
        return "injured"
    status = str(player.get("injury_status") or "").upper()
    if status in {"OUT", "IR", "DOUBTFUL"}:
        return status
    return "lower projection than a bench replacement"


def optimal_lineup(roster: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill each starting slot with the highest remaining eligible projection."""
    starter_slots = [
        slot
        for slot in (_slot_id(player) for player in roster)
        if slot is not None and is_starter_slot(slot)
    ]
    available = [
        player
        for player in roster
        if _slot_id(player) != IR_SLOT and not player.get("injured")
    ]
    assigned: set[Any] = set()
    recommended: list[dict[str, Any]] = []
    for slot in starter_slots:
        candidates = [
            player
            for player in available
            if player.get("id") not in assigned
            and player_fits_slot(str(player.get("position") or ""), slot)
        ]
        candidates.sort(key=primary_projection, reverse=True)
        if not candidates:
            continue
        pick = candidates[0]
        assigned.add(pick.get("id"))
        recommended.append(
            {
                **pick,
                "recommended_slot_id": slot,
                "recommended_slot": slot_name(slot),
                "projection": primary_projection(pick),
                "espn_projection": pick.get("projected_points"),
                "sleeper_projection": pick.get("sleeper_projected_points"),
                "projection_source": projection_source(pick),
            }
        )

    current_starters = [player for player in roster if is_starter_slot(_slot_id(player))]
    current_ids = {player.get("id") for player in current_starters}
    recommended_ids = {player.get("id") for player in recommended}

    sit = [
        {
            "id": player.get("id"),
            "name": player.get("name"),
            "position": player.get("position"),
            "slot": player.get("lineup_slot"),
            "projection": primary_projection(player),
            "reason": _sit_reason(player),
        }
        for player in current_starters
        if player.get("id") not in recommended_ids
    ]
    start = [
        {
            "id": player.get("id"),
            "name": player.get("name"),
            "position": player.get("position"),
            "from_slot": player.get("lineup_slot"),
            "to_slot": player.get("recommended_slot"),
            "from_slot_id": _slot_id(player),
            "to_slot_id": player.get("recommended_slot_id"),
            "projection": player.get("projection"),
        }
        for player in recommended
        if player.get("id") not in current_ids
    ]

    moves: list[dict[str, Any]] = []
    for player in recommended:
        current = _slot_id(player)
        target = player.get("recommended_slot_id")
        if current is None or target is None or current == target:
            continue
        moves.append(
            {
                "player_id": player.get("id"),
                "from_slot": current,
                "to_slot": target,
                "name": player.get("name"),
            }
        )
    for player in sit:
        outgoing = next((row for row in current_starters if row.get("id") == player.get("id")), None)
        if not outgoing:
            continue
        current = _slot_id(outgoing)
        if current is None:
            continue
        moves.append(
            {
                "player_id": outgoing.get("id"),
                "from_slot": current,
                "to_slot": BENCH_SLOT,
                "name": outgoing.get("name"),
            }
        )

    current_total = sum(primary_projection(player) for player in current_starters)
    recommended_total = sum(float(item["projection"]) for item in recommended)
    return {
        "current_projected": round(current_total, 2),
        "recommended_projected": round(recommended_total, 2),
        "delta": round(recommended_total - current_total, 2),
        "start": start,
        "sit": sit,
        "recommended": recommended,
        "moves": moves,
        "notes": [
            "Averages ESPN and Sleeper this-week projections when both exist.",
            "Injured players are excluded from the recommended lineup.",
            "This is a greedy slot fill, not a trained model.",
        ],
    }


def _is_bench(player: dict[str, Any]) -> bool:
    slot = _slot_id(player)
    if slot == IR_SLOT:
        return False
    return slot == BENCH_SLOT or not is_starter_slot(slot)


def _side_snip(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "position": row.get("position"),
        "weekly_rate": row.get("weekly_rate"),
        "st_vorp": row.get("st_vorp"),
        "lt_vorp": row.get("lt_vorp"),
        "blended": row.get("blended"),
        "ros_source": row.get("ros_source"),
    }


def _compact_claim_grade(grade: dict[str, Any]) -> dict[str, Any]:
    lineup = grade.get("lineup") or {}
    return {
        "grade": grade.get("grade"),
        "verdict": grade.get("verdict"),
        "blended": grade.get("blended"),
        "delta_st": grade.get("delta_st"),
        "delta_lt": grade.get("delta_lt"),
        "roster": grade.get("roster"),
        "dropped": grade.get("dropped") or [],
        "lineup": {
            "mode": lineup.get("mode"),
            "delta_st": lineup.get("delta_st"),
            "delta_lt": lineup.get("delta_lt"),
        },
        "summary": grade.get("summary"),
    }


def rank_waiver_claims(
    roster: list[dict[str, Any]],
    free_agents: list[dict[str, Any]],
    *,
    context: LeagueContext,
    baselines: dict[str, float],
    window: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Rank add/drop claims with the same ST/LT roster + lineup math as trades."""
    resolved = infer_window(context, window)
    st_w, lt_w = window_weights(resolved)
    roster_w, lineup_w = structure_weights(resolved)
    valued = {
        player["id"]: value_player(player, context, baselines, window=resolved)
        for player in roster + free_agents
        if player.get("id") is not None
    }
    drops = [player for player in roster if player.get("id") in valued and _is_bench(player)]
    rows: list[dict[str, Any]] = []
    seen: set[Any] = {player.get("id") for player in roster}

    for agent in free_agents:
        agent_id = agent.get("id")
        if agent_id is None or agent_id in seen or agent_id not in valued:
            continue
        fa_pos = str(agent.get("position") or "")
        best: dict[str, Any] | None = None
        for drop in drops:
            grade = grade_valued_trade(
                send_values=[valued[drop["id"]]],
                recv_values=[valued[agent_id]],
                send_players=[drop],
                recv_players=[agent],
                context=context,
                window=resolved,
                our_roster=roster,
                baselines=baselines,
            )
            compact = _compact_claim_grade(grade)
            same_pos = str(drop.get("position") or "") == fa_pos
            if best is None or compact["blended"] > best["blended"] or (
                compact["blended"] == best["blended"] and same_pos and not best.get("same_position_drop")
            ):
                best = {
                    **compact,
                    "same_position_drop": same_pos,
                    "drop_player": drop,
                    "send": grade.get("send"),
                    "receive": grade.get("receive"),
                }
        if best is None:
            continue
        drop = best["drop_player"]
        rows.append(
            {
                "id": agent_id,
                "name": agent.get("name"),
                "position": fa_pos,
                "percent_owned": agent.get("percent_owned"),
                "drop_candidate": drop.get("name"),
                "drop_candidate_id": drop.get("id"),
                "drop_candidate_position": drop.get("position"),
                "grade": best["grade"],
                "verdict": best["verdict"],
                "blended": best["blended"],
                "delta_st": best["delta_st"],
                "delta_lt": best["delta_lt"],
                "roster": best["roster"],
                "lineup": best["lineup"],
                "why": best["summary"],
                "add": _side_snip((best.get("receive") or [None])[0]),
                "drop": _side_snip((best.get("send") or [None])[0]),
            }
        )

    rows.sort(key=lambda row: (row["blended"], row.get("delta_lt") or 0), reverse=True)
    return {
        "window": resolved,
        "weights": {"st": st_w, "lt": lt_w, "roster": roster_w, "lineup": lineup_w},
        "targets": rows[:limit],
    }


def waiver_targets(
    roster: list[dict[str, Any]],
    free_agents: list[dict[str, Any]],
    *,
    context: LeagueContext,
    baselines: dict[str, float],
    window: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Rank waiver adds using trade-grade surplus. Kept as a list for older callers."""
    return rank_waiver_claims(
        roster,
        free_agents,
        context=context,
        baselines=baselines,
        window=window,
        limit=limit,
    )["targets"]
