"""Turn espn-api objects into plain JSON-friendly dictionaries."""

from __future__ import annotations

from typing import Any

from league_manager.slots import is_starter_slot, parse_slot, slot_name


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def weekly_stats_to_dict(player: Any) -> dict[int, dict[str, float | None]]:
    raw = _attr(player, "stats") or _attr(player, "weekly_stats") or {}
    weeks: dict[int, dict[str, float | None]] = {}
    if not isinstance(raw, dict):
        return weeks
    for period, payload in raw.items():
        try:
            week = int(period)
        except (TypeError, ValueError):
            continue
        if week == 0 or not isinstance(payload, dict):
            continue
        weeks[week] = {
            "points": payload.get("points"),
            "projected_points": payload.get("projected_points"),
        }
    return weeks


def _lineup_slot_id(player: Any) -> int | None:
    """espn-api 0.46+ stores the current slot as a name (`WR`, `BE`), not `lineupSlotId`."""
    raw = _attr(player, "lineupSlotId")
    if raw is None:
        raw = _attr(player, "lineup_slot_id")
    if raw is None:
        raw = _attr(player, "lineupSlot")
    if raw is None:
        raw = _attr(player, "lineup_slot")
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    try:
        return parse_slot(raw)
    except (TypeError, ValueError):
        return None


def player_to_dict(player: Any, *, include_lineup: bool = True) -> dict[str, Any]:
    slot_id = _lineup_slot_id(player)
    projected = _attr(player, "projected_points")
    projected_avg = _attr(player, "projected_avg_points")
    if projected is None:
        projected = projected_avg
    data = {
        "id": _attr(player, "playerId") or _attr(player, "player_id"),
        "name": _attr(player, "name"),
        "position": _attr(player, "position"),
        "pro_team": _attr(player, "proTeam") or _attr(player, "pro_team"),
        "injured": bool(_attr(player, "injured", False)),
        "injury_status": _attr(player, "injuryStatus") or _attr(player, "injury_status"),
        "projected_points": projected,
        "projected_avg_points": projected_avg,
        "projected_total_points": _attr(player, "projected_total_points"),
        "points": _attr(player, "points") or _attr(player, "total_points"),
        "percent_owned": _attr(player, "percent_owned"),
        "avg_points": _attr(player, "avg_points"),
        "bye_week": _attr(player, "bye_week"),
        "weekly_stats": weekly_stats_to_dict(player),
    }
    if include_lineup:
        data["lineup_slot_id"] = slot_id
        data["lineup_slot"] = slot_name(slot_id) if slot_id is not None else None
        data["is_starter"] = is_starter_slot(slot_id)
    return {key: value for key, value in data.items() if value is not None or key in {"injured"}}


def team_to_dict(team: Any, *, include_roster: bool = True) -> dict[str, Any]:
    owners = _attr(team, "owners") or []
    owner_ids = []
    for owner in owners:
        if isinstance(owner, dict):
            owner_ids.append(owner.get("id") or owner.get("userId"))
        else:
            owner_ids.append(str(owner))
    data = {
        "id": _attr(team, "team_id"),
        "name": _attr(team, "team_name"),
        "abbrev": _attr(team, "team_abbrev"),
        "wins": _attr(team, "wins"),
        "losses": _attr(team, "losses"),
        "ties": _attr(team, "ties"),
        "points_for": _attr(team, "points_for"),
        "points_against": _attr(team, "points_against"),
        "standing": _attr(team, "standing"),
        "owners": owner_ids,
    }
    if include_roster:
        roster = _attr(team, "roster") or []
        data["roster"] = [player_to_dict(player) for player in roster]
    return data


def matchup_to_dict(matchup: Any) -> dict[str, Any]:
    home = _attr(matchup, "home_team")
    away = _attr(matchup, "away_team")
    return {
        "home": _attr(home, "team_name"),
        "home_id": _attr(home, "team_id"),
        "home_score": _attr(matchup, "home_score"),
        "away": _attr(away, "team_name"),
        "away_id": _attr(away, "team_id"),
        "away_score": _attr(matchup, "away_score"),
    }
