"""Enumerate 1-for-1, 2-for-1, 1-for-2, and 2-for-2 redraft trades.

Keep packages inside a realism band calibrated from recent accepted Sleeper
trades: enough blended surplus to be worth sending, but not so lopsided that
accepts almost never happen. ESPN face + ROS caps bound how much the other
manager can give up and still look plausible.

`trade-calibrate --apply` may raise the top edge via an override file; see
`league_manager.trade_market.active_band`. Accepts never feed VORP pricing.
"""

from __future__ import annotations

from itertools import combinations, product
from typing import Any, Iterable

from league_manager.slots import normalize_position
from league_manager.trades import grade_valued_trade
from league_manager.trade_market import active_band
from league_manager.value import (
    LeagueContext,
    PlayerValue,
    espn_face_value,
    infer_window,
    value_player,
    weekly_rate,
)

DEFAULT_KINDS = ("1:1", "2:1", "1:2", "2:2")
SKIP_POSITIONS = {"K", "D/ST", "DST", "DEF", "PK"}
MIN_WEEKLY = 5.0
MAX_PER_SIDE = 12
MAX_PER_OPPONENT = 8


def _band_defaults() -> dict[str, float]:
    band = active_band()
    return band.as_dict()


# Module-level names kept for tests / calibration imports; reflect active band.
def _refresh_band_constants() -> None:
    global MIN_SURPLUS, MAX_SURPLUS, MIN_THEIR_FACE, MAX_THEIR_FACE, MIN_THEIR_LT, MAX_THEIR_LT
    d = _band_defaults()
    MIN_SURPLUS = d["min_surplus"]
    MAX_SURPLUS = d["max_surplus"]
    MIN_THEIR_FACE = d["min_their_face"]
    MAX_THEIR_FACE = d["max_their_face"]
    MIN_THEIR_LT = d["min_their_lt"]
    MAX_THEIR_LT = d["max_their_lt"]


_refresh_band_constants()


def parse_kinds(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_KINDS
    allowed = set(DEFAULT_KINDS)
    parts = tuple(part.strip() for part in raw.split(",") if part.strip())
    bad = [part for part in parts if part not in allowed]
    if bad:
        raise ValueError(f"Unknown trade kinds {bad}. Use 1:1, 2:1, 1:2, 2:2.")
    return parts or DEFAULT_KINDS


def _candidates(
    roster: list[dict[str, Any]],
    valued: dict[Any, PlayerValue],
    *,
    max_players: int = MAX_PER_SIDE,
    min_weekly: float = MIN_WEEKLY,
) -> list[dict[str, Any]]:
    rows = []
    for player in roster:
        player_id = player.get("id")
        if player_id is None or player_id not in valued:
            continue
        pos = normalize_position(str(player.get("position") or ""))
        if pos in SKIP_POSITIONS:
            continue
        value = valued[player_id]
        rate = value.weekly_rate or weekly_rate(player)
        if rate < min_weekly and not player.get("is_starter") and value.lt_vorp < 2:
            continue
        rows.append(player)
    rows.sort(key=lambda item: valued[item["id"]].blended, reverse=True)
    return rows[: max(1, int(max_players))]


def _side_summary(players: list[dict[str, Any]], valued: dict[Any, PlayerValue]) -> list[dict[str, Any]]:
    rows = []
    for player in players:
        value = valued[player["id"]]
        rows.append(
            {
                "id": player.get("id"),
                "name": player.get("name"),
                "position": value.position,
                "blended": value.blended,
                "lt_vorp": value.lt_vorp,
                "ros_source": value.ros_source,
            }
        )
    return rows


def _package_score(blended: float, their_face_net: float, their_lt_net: float) -> float:
    """Prefer solid +EV with still-plausible optics; slight lean into surplus.

    Hand-tuned (not fit from accepts). Band filters hard caps; this only ranks.
    """
    # Sweet spot ~2–12; above that, soft diminishing returns (not a smash magnet).
    if blended <= 12.0:
        surplus_part = blended * 1.95
    else:
        surplus_part = 23.4 - (blended - 12.0) * 0.25

    # Slight ESPN-face gift to them (0..5) reads as fair / easy to accept.
    if 0.0 <= their_face_net <= 5.0:
        face_part = 3.2 - abs(their_face_net - 2.0) * 0.3
    elif -2.5 <= their_face_net < 0.0:
        face_part = their_face_net * 0.6
    else:
        face_part = -abs(their_face_net - 2.0) * 0.25

    # Their ROS VORP net under our model should stay in a dealable range.
    if -8.0 <= their_lt_net <= 10.0:
        ros_part = 2.7 - abs(their_lt_net) * 0.18
    else:
        ros_part = -abs(their_lt_net) * 0.12

    return surplus_part + face_part + ros_part


def _why(
    grade: dict[str, Any],
    their_face_net: float,
    their_lt_net: float,
    send: list[dict],
    recv: list[dict],
) -> str:
    send_names = ", ".join(str(player.get("name")) for player in send)
    recv_names = ", ".join(str(player.get("name")) for player in recv)
    return (
        f"Send {send_names} for {recv_names}. {grade['summary']} "
        f"ESPN face for them {their_face_net:+.1f}; their ROS VORP net {their_lt_net:+.1f}."
    )


def search_trades(
    *,
    our_team_id: Any,
    teams: list[dict[str, Any]],
    players: dict[Any, dict[str, Any]],
    context: LeagueContext,
    baselines: dict[str, float],
    window: str | None = None,
    kinds: Iterable[str] = DEFAULT_KINDS,
    limit: int = 40,
    min_surplus: float | None = None,
    max_surplus: float | None = None,
    min_their_face: float | None = None,
    max_their_face: float | None = None,
    min_their_lt: float | None = None,
    max_their_lt: float | None = None,
    max_players_per_side: int = MAX_PER_SIDE,
    with_team_id: Any | None = None,
    max_per_opponent: int = MAX_PER_OPPONENT,
) -> dict[str, Any]:
    band = active_band()
    if min_surplus is None:
        min_surplus = band.min_surplus
    if max_surplus is None:
        max_surplus = band.max_surplus
    if min_their_face is None:
        min_their_face = band.min_their_face
    if max_their_face is None:
        max_their_face = band.max_their_face
    if min_their_lt is None:
        min_their_lt = band.min_their_lt
    if max_their_lt is None:
        max_their_lt = band.max_their_lt
    resolved = infer_window(context, window)
    valued = {
        player_id: value_player(player, context, baselines, window=resolved)
        for player_id, player in players.items()
    }
    faces = {player_id: espn_face_value(player, context) for player_id, player in players.items()}
    kind_set = tuple(kinds) or DEFAULT_KINDS
    our_team = next((team for team in teams if team.get("id") == our_team_id), None)
    if our_team is None:
        raise ValueError(f"No team with id {our_team_id}")
    our_players = _candidates(our_team.get("roster") or [], valued, max_players=max_players_per_side)
    considered = 0
    kept: list[dict[str, Any]] = []

    def consider(kind: str, other: dict[str, Any], send: list[dict[str, Any]], recv: list[dict[str, Any]]) -> None:
        nonlocal considered
        considered += 1
        send_values = [valued[player["id"]] for player in send]
        recv_values = [valued[player["id"]] for player in recv]
        grade = grade_valued_trade(
            send_values=send_values,
            recv_values=recv_values,
            send_players=send,
            recv_players=recv,
            context=context,
            window=resolved,
            our_roster=our_team.get("roster") or [],
            baselines=baselines,
        )
        their_face_net = sum(faces[player["id"]] for player in send) - sum(
            faces[player["id"]] for player in recv
        )
        # Positive => they gain ROS VORP if they used our numbers.
        their_lt_net = sum(item.lt_vorp for item in send_values) - sum(
            item.lt_vorp for item in recv_values
        )
        if grade["blended"] < min_surplus or grade["blended"] > max_surplus:
            return
        if their_face_net < min_their_face or their_face_net > max_their_face:
            return
        if their_lt_net < min_their_lt or their_lt_net > max_their_lt:
            return
        score = _package_score(grade["blended"], their_face_net, their_lt_net)
        kept.append(
            {
                "kind": kind,
                "with_team": {"id": other.get("id"), "name": other.get("name")},
                "send": _side_summary(send, valued),
                "receive": _side_summary(recv, valued),
                "send_ids": [player["id"] for player in send],
                "receive_ids": [player["id"] for player in recv],
                "grade": grade["grade"],
                "verdict": grade["verdict"],
                "blended": grade["blended"],
                "delta_st": grade["delta_st"],
                "delta_lt": grade["delta_lt"],
                "roster": grade.get("roster"),
                "lineup": {
                    "delta_st": (grade.get("lineup") or {}).get("delta_st"),
                    "delta_lt": (grade.get("lineup") or {}).get("delta_lt"),
                    "mode": (grade.get("lineup") or {}).get("mode"),
                },
                "their_espn_face_net": round(their_face_net, 2),
                "their_ros_vorp_net": round(their_lt_net, 2),
                "score": round(score, 2),
                "why": _why(grade, their_face_net, their_lt_net, send, recv),
            }
        )

    opponents = [
        team
        for team in teams
        if team.get("id") != our_team_id and (with_team_id is None or team.get("id") == with_team_id)
    ]
    for other in opponents:
        theirs = _candidates(other.get("roster") or [], valued, max_players=max_players_per_side)
        if not theirs:
            continue
        if "1:1" in kind_set:
            for send_player, recv_player in product(our_players, theirs):
                consider("1:1", other, [send_player], [recv_player])
        if "2:1" in kind_set and len(our_players) >= 2:
            for send_pair, recv_player in product(combinations(our_players, 2), theirs):
                consider("2:1", other, list(send_pair), [recv_player])
        if "1:2" in kind_set and len(theirs) >= 2:
            for send_player, recv_pair in product(our_players, combinations(theirs, 2)):
                consider("1:2", other, [send_player], list(recv_pair))
        if "2:2" in kind_set and len(our_players) >= 2 and len(theirs) >= 2:
            for send_pair, recv_pair in product(
                combinations(our_players, 2), combinations(theirs, 2)
            ):
                consider("2:2", other, list(send_pair), list(recv_pair))

    kept.sort(key=lambda item: item["score"], reverse=True)
    picked: list[dict[str, Any]] = []
    per_team: dict[Any, int] = {}
    for row in kept:
        team_id = row["with_team"]["id"]
        if per_team.get(team_id, 0) >= max_per_opponent and len(picked) >= max(limit // 2, 1):
            continue
        picked.append(row)
        per_team[team_id] = per_team.get(team_id, 0) + 1
        if len(picked) >= limit:
            break
    return {
        "window": resolved,
        "kinds": list(kind_set),
        "considered": considered,
        "kept": len(kept),
        "returned": len(picked),
        "filters": {
            "min_surplus": min_surplus,
            "max_surplus": max_surplus,
            "min_their_espn_face": min_their_face,
            "max_their_espn_face": max_their_face,
            "min_their_ros_vorp": min_their_lt,
            "max_their_ros_vorp": max_their_lt,
        },
        "trades": picked,
    }
