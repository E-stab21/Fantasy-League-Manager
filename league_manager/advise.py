"""Lineup and waiver recommendations from public projections, not a custom model."""

from __future__ import annotations

from collections import defaultdict
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


def waiver_targets(
    roster: list[dict[str, Any]],
    free_agents: list[dict[str, Any]],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    bench_by_pos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in roster:
        slot = _slot_id(player)
        if slot == BENCH_SLOT or not is_starter_slot(slot):
            bench_by_pos[str(player.get("position") or "")].append(player)
    for group in bench_by_pos.values():
        group.sort(key=primary_projection)

    rows = []
    for agent in free_agents:
        pos = str(agent.get("position") or "")
        replacement = (bench_by_pos.get(pos) or [None])[0]
        replacement_pts = primary_projection(replacement) if replacement else 0.0
        agent_pts = primary_projection(agent)
        delta = agent_pts - replacement_pts
        rows.append(
            {
                "id": agent.get("id"),
                "name": agent.get("name"),
                "position": pos,
                "projected_points": agent_pts,
                "sleeper_projected_points": agent.get("sleeper_projected_points"),
                "percent_owned": agent.get("percent_owned"),
                "drop_candidate": replacement.get("name") if replacement else None,
                "drop_candidate_id": replacement.get("id") if replacement else None,
                "drop_candidate_projection": replacement_pts if replacement else None,
                "delta": round(delta, 2),
                "reason": (
                    f"+{delta:.1f} vs bench {replacement.get('name')}"
                    if replacement and delta > 0
                    else "watch list / streaming option"
                ),
            }
        )
    rows.sort(key=lambda row: (row["delta"], row["projected_points"] or 0), reverse=True)
    return rows[:limit]
