"""Public Sleeper endpoints used as a second projection / trending source."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

STATE_URL = "https://api.sleeper.app/v1/state/nfl"
PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
TRENDING_URL = "https://api.sleeper.app/v1/players/nfl/trending/{kind}"
PROJECTIONS_URL = "https://api.sleeper.app/projections/nfl/{season}/{week}"
CACHE_PATH = Path("/tmp/league-manager-sleeper-players.json")
CACHE_TTL_SECONDS = 20 * 60 * 60
PROJ_CACHE_DIR = Path("/tmp/league-manager-sleeper-proj")
PROJ_CACHE_TTL_SECONDS = 6 * 60 * 60


def normalize_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    for suffix in ("jr", "sr", "ii", "iii", "iv", "v"):
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 2:
            cleaned = cleaned[: -len(suffix)]
    return cleaned


def nfl_state(http_get=None) -> dict[str, Any]:
    getter = http_get or requests.get
    response = getter(STATE_URL, timeout=20)
    response.raise_for_status()
    return response.json()


def trending(kind: str = "add", limit: int = 25, http_get=None) -> list[dict[str, Any]]:
    getter = http_get or requests.get
    response = getter(
        TRENDING_URL.format(kind=kind),
        params={"limit": limit},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def projections(season: int, week: int, http_get=None) -> dict[str, Any]:
    getter = http_get or requests.get
    response = getter(
        PROJECTIONS_URL.format(season=season, week=week),
        params={"season_type": "regular"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def players(http_get=None, cache_path: Path = CACHE_PATH) -> dict[str, Any]:
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < CACHE_TTL_SECONDS:
        return json.loads(cache_path.read_text())
    getter = http_get or requests.get
    response = getter(PLAYERS_URL, timeout=60)
    response.raise_for_status()
    data = response.json()
    cache_path.write_text(json.dumps(data))
    return data


def index_players_by_name(player_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for player_id, player in player_map.items():
        name = player.get("full_name") or " ".join(
            part for part in (player.get("first_name"), player.get("last_name")) if part
        )
        if not name.strip():
            continue
        record = {**player, "player_id": player_id}
        index[normalize_name(name)] = record
    return index


def as_projection_map(raw: Any) -> dict[str, Any]:
    """Normalize Sleeper weekly projections to {player_id: entry}."""
    if isinstance(raw, dict):
        first = next(iter(raw.values()), None)
        if isinstance(first, dict):
            return {str(key): value for key, value in raw.items()}
        if raw.get("player_id") is not None:
            return {str(raw["player_id"]): raw}
        return {}
    if isinstance(raw, list):
        mapped: dict[str, Any] = {}
        for item in raw:
            if isinstance(item, dict) and item.get("player_id") is not None:
                mapped[str(item["player_id"])] = item
        return mapped
    return {}


def cached_week_projections(
    season: int,
    week: int,
    http_get=None,
    cache_dir: Path = PROJ_CACHE_DIR,
) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{season}-w{week}.json"
    if path.exists() and time.time() - path.stat().st_mtime < PROJ_CACHE_TTL_SECONDS:
        return json.loads(path.read_text())
    data = projections(season, week, http_get=http_get)
    path.write_text(json.dumps(data))
    return data


def remaining_week_series(
    season: int,
    current_week: int,
    through: int = 17,
    *,
    scoring: str = "ppr",
    http_get=None,
    week_maps: dict[int, Any] | None = None,
) -> dict[str, dict[int, float]]:
    """Per-week projections for the remaining weeks, keyed by Sleeper player id.

    Keeps the week-by-week shape rather than collapsing it, so values can weight
    each week separately. Same cached fetches `remaining_week_totals` uses.
    """
    series: dict[str, dict[int, float]] = {}
    start = max(int(current_week), 1)
    end = max(int(through), start)
    for week in range(start, end + 1):
        raw = week_maps[week] if week_maps is not None else cached_week_projections(
            season, week, http_get=http_get
        )
        for player_id, entry in as_projection_map(raw).items():
            points = projection_points(entry, scoring)
            if points is None:
                continue
            series.setdefault(player_id, {})[week] = float(points)
    return series


def remaining_week_totals(
    season: int,
    current_week: int,
    through: int = 17,
    *,
    scoring: str = "ppr",
    http_get=None,
    week_maps: dict[int, Any] | None = None,
) -> dict[str, float]:
    series = remaining_week_series(
        season,
        current_week,
        through,
        scoring=scoring,
        http_get=http_get,
        week_maps=week_maps,
    )
    return {player_id: sum(weeks.values()) for player_id, weeks in series.items()}


def projection_points(entry: dict[str, Any], scoring: str = "ppr") -> float | None:
    if not entry:
        return None
    stats = entry.get("stats") or entry
    key = {
        "ppr": "pts_ppr",
        "half": "pts_half_ppr",
        "half_ppr": "pts_half_ppr",
        "std": "pts_std",
        "standard": "pts_std",
    }.get(scoring, "pts_ppr")
    value = stats.get(key)
    if value is None:
        return None
    return float(value)
