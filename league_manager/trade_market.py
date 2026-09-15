"""Calibrate trade-search realism band from comparable public Sleeper accepts.

Accepted trades set the *band* (what is worth sending vs still plausibly
accepted). They do **not** feed VORP / player pricing — noisy or bad deals
would corrupt valuations.
"""

from __future__ import annotations

import json
import os
import random
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from league_manager.sleeper import (
    as_projection_map,
    cached_week_projections,
    players as sleeper_players,
    projection_points,
    remaining_week_totals,
)
from league_manager.trades import grade_valued_trade, starter_floor_baselines
from league_manager.value import (
    LeagueContext,
    infer_window,
    injury_factor,
    replacement_baselines,
    value_player,
)

SEED_LEAGUE = "522458773317046272"
OUR_KINDS = {"1:1", "2:1", "1:2", "2:2"}
SKIP_POS = {"K", "DEF", "DL", "LB", "DB", "IDP"}
DEFAULT_CACHE = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "league-manager"
BAND_PATH = DEFAULT_CACHE / "trade_band.json"

# Built-in realism band (raised from early Sleeper accept samples).
DEFAULT_MIN_SURPLUS = 1.0
DEFAULT_MAX_SURPLUS = 40.0
DEFAULT_MIN_THEIR_FACE = -55.0
DEFAULT_MAX_THEIR_FACE = 15.0
DEFAULT_MIN_THEIR_LT = -80.0
DEFAULT_MAX_THEIR_LT = 25.0


@dataclass(frozen=True)
class LeagueProfile:
    """Shape used to keep Sleeper samples close to our ESPN league."""

    team_count: int
    scoring: str  # ppr | half | std
    superflex: bool
    best_ball: bool = False


@dataclass(frozen=True)
class Band:
    min_surplus: float = DEFAULT_MIN_SURPLUS
    max_surplus: float = DEFAULT_MAX_SURPLUS
    min_their_face: float = DEFAULT_MIN_THEIR_FACE
    max_their_face: float = DEFAULT_MAX_THEIR_FACE
    min_their_lt: float = DEFAULT_MIN_THEIR_LT
    max_their_lt: float = DEFAULT_MAX_THEIR_LT

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def band_path() -> Path:
    return Path(os.getenv("LEAGUE_MANAGER_TRADE_BAND", str(BAND_PATH)))


def default_band() -> Band:
    return Band()


def load_band_override(path: Path | None = None) -> dict[str, Any] | None:
    target = path or band_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def active_band(path: Path | None = None) -> Band:
    """Code defaults, overlaid by last --apply from trade-calibrate."""
    base = default_band()
    override = load_band_override(path)
    if not override:
        return base
    return Band(
        min_surplus=float(override.get("min_surplus", base.min_surplus)),
        max_surplus=float(override.get("max_surplus", base.max_surplus)),
        min_their_face=float(override.get("min_their_face", base.min_their_face)),
        max_their_face=float(override.get("max_their_face", base.max_their_face)),
        min_their_lt=float(override.get("min_their_lt", base.min_their_lt)),
        max_their_lt=float(override.get("max_their_lt", base.max_their_lt)),
    )


def save_band(band: Band, *, meta: dict[str, Any] | None = None, path: Path | None = None) -> Path:
    target = path or band_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **band.as_dict(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Market accepts calibrate the search band only; they do not affect VORP pricing.",
        **(meta or {}),
    }
    target.write_text(json.dumps(payload, indent=2))
    return target


def espn_reference_profile(league: Any, scoring: str) -> LeagueProfile:
    teams = list(getattr(league, "teams", None) or [])
    team_count = len(teams) or 10
    settings = getattr(league, "settings", None)
    roster_map = getattr(settings, "position_slot_counts", None) or getattr(settings, "roster_settings", None) or {}
    qb_slots = 0
    superflex = False
    if isinstance(roster_map, dict):
        for key, count in roster_map.items():
            label = str(key).upper()
            try:
                n = int(count or 0)
            except (TypeError, ValueError):
                continue
            if n <= 0:
                continue
            if label == "QB":
                qb_slots += n
            # ESPN OP is usually RB/WR/TE flex, not superflex — ignore unless labeled SF.
            if "SUPER" in label or label in {"SF", "SUPERFLEX"}:
                superflex = True
        if qb_slots >= 2:
            superflex = True
    return LeagueProfile(
        team_count=team_count,
        scoring=(scoring or "ppr").lower(),
        superflex=superflex,
        best_ball=False,
    )


def sleeper_league_profile(meta: dict[str, Any]) -> LeagueProfile | None:
    settings = meta.get("settings") or {}
    try:
        league_type = int(settings.get("type"))
    except (TypeError, ValueError):
        return None
    if league_type != 0:
        return None
    if settings.get("best_ball"):
        return None
    if int(settings.get("taxi_slots") or 0) > 0:
        return None
    roster_positions = meta.get("roster_positions") or []
    qb = sum(1 for p in roster_positions if p == "QB")
    superflex = "SUPER_FLEX" in roster_positions or qb >= 2
    scoring_settings = meta.get("scoring_settings") or {}
    rec = float(scoring_settings.get("rec") or 0.0)
    if rec >= 0.9:
        scoring = "ppr"
    elif rec >= 0.4:
        scoring = "half"
    else:
        scoring = "std"
    team_count = int(settings.get("num_teams") or meta.get("total_rosters") or 0)
    if team_count <= 0:
        return None
    return LeagueProfile(
        team_count=team_count,
        scoring=scoring,
        superflex=superflex,
        best_ball=bool(settings.get("best_ball")),
    )


def profiles_compatible(sample: LeagueProfile, reference: LeagueProfile, *, team_slop: int = 2) -> bool:
    if abs(sample.team_count - reference.team_count) > team_slop:
        return False
    if sample.superflex != reference.superflex:
        return False
    if sample.best_ball != reference.best_ball:
        return False
    # Allow half↔ppr; reject std vs PPR-family mismatch.
    family = {"ppr", "half"}
    if (sample.scoring in family) != (reference.scoring in family):
        return False
    if sample.scoring == "std" and reference.scoring != "std":
        return False
    return True


def http_get(url: str, *, sleep: float = 0.05, timeout: float = 40.0) -> Any:
    time.sleep(sleep)
    response = requests.get(url, timeout=timeout)
    if not response.ok:
        return None
    try:
        return response.json()
    except Exception:
        return None


def discover_leagues(
    seed: str,
    *,
    seasons: list[str],
    max_leagues: int = 120,
    max_depth: int = 2,
    users_per_league: int = 8,
    cache_dir: Path | None = None,
) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    queue: list[tuple[str, int]] = [(seed, 0)]
    seen_users: set[str] = set()
    queued: set[str] = {seed}

    while queue and len(found) < max_leagues * 4:
        lid, depth = queue.pop(0)
        users = http_get(f"https://api.sleeper.app/v1/league/{lid}/users") or []
        for user in users[:users_per_league]:
            uid = str(user.get("user_id") or "")
            if not uid or uid in seen_users:
                continue
            seen_users.add(uid)
            for season in seasons:
                leagues = http_get(f"https://api.sleeper.app/v1/user/{uid}/leagues/nfl/{season}") or []
                for league in leagues:
                    other = str(league.get("league_id") or "")
                    if not other:
                        continue
                    found[other] = league
                    if depth < max_depth and other not in queued:
                        queued.add(other)
                        queue.append((other, depth + 1))
            if len(found) >= max_leagues * 4:
                break
    return list(found.values())


def league_meta(league_id: str, *, cache_dir: Path) -> dict[str, Any] | None:
    path = cache_dir / f"league-{league_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    data = http_get(f"https://api.sleeper.app/v1/league/{league_id}")
    if data:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    return data


def fetch_trades(league_id: str, weeks: list[int], *, cache_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for week in weeks:
        path = cache_dir / f"tx-{league_id}-w{week}.json"
        if path.exists():
            txs = json.loads(path.read_text())
        else:
            txs = http_get(f"https://api.sleeper.app/v1/league/{league_id}/transactions/{week}") or []
            cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(txs))
        for tx in txs:
            if tx.get("type") != "trade" or tx.get("status") != "complete":
                continue
            row = dict(tx)
            row["league_id"] = league_id
            row["week"] = week
            out.append(row)
    return out


def parse_sides(tx: dict[str, Any]) -> dict[int, dict[str, list[str]]] | None:
    adds = tx.get("adds") or {}
    drops = tx.get("drops") or {}
    if tx.get("draft_picks"):
        return None
    if not adds and not drops:
        return None
    sides: dict[int, dict[str, list[str]]] = defaultdict(lambda: {"adds": [], "drops": []})
    for pid, roster_id in adds.items():
        sides[int(roster_id)]["adds"].append(str(pid))
    for pid, roster_id in drops.items():
        sides[int(roster_id)]["drops"].append(str(pid))
    if len(sides) != 2:
        return None
    for side in sides.values():
        if not side["adds"] or not side["drops"]:
            return None
    return sides


def sleeper_player_row(
    player_id: str,
    *,
    directory: dict[str, Any],
    ros_totals: dict[str, float],
    week_proj: dict[str, Any],
) -> dict[str, Any] | None:
    meta = directory.get(player_id) or {}
    pos = meta.get("position") or (meta.get("fantasy_positions") or [None])[0]
    if not pos or pos in SKIP_POS:
        return None
    weekly = projection_points(week_proj.get(player_id, {}), "ppr") or 0.0
    ros = float(ros_totals.get(player_id, 0.0) or 0.0)
    return {
        "id": player_id,
        "name": meta.get("full_name") or player_id,
        "position": pos,
        "projected_points": weekly,
        "sleeper_projected_points": weekly,
        "sleeper_ros_points": ros if ros > 0 else None,
        "projected_total_points": ros if ros > 0 else None,
        "points": 0.0,
        "injured": bool(meta.get("injury_status")),
        "injury_status": meta.get("injury_status"),
        "is_starter": True,
    }


def _face_value(player: dict[str, Any], games: int) -> float:
    rate = float(player.get("projected_points") or 0.0)
    return rate * games * injury_factor(player, horizon="lt")


def grade_accepted_side(
    *,
    send_players: list[dict[str, Any]],
    recv_players: list[dict[str, Any]],
    context: LeagueContext,
    baselines: dict[str, float],
    window: str,
    band: Band,
    starter_floors: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    if not send_players or not recv_players:
        return None
    send_values = [value_player(p, context, baselines, window=window) for p in send_players]
    recv_values = [value_player(p, context, baselines, window=window) for p in recv_players]
    grade = grade_valued_trade(
        send_values=send_values,
        recv_values=recv_values,
        send_players=send_players,
        recv_players=recv_players,
        context=context,
        window=window,
        baselines=baselines,
        starter_floors=starter_floors,
    )
    games = len(context.remaining_weeks)
    their_face_net = sum(_face_value(p, games) for p in send_players) - sum(
        _face_value(p, games) for p in recv_players
    )
    their_lt_net = sum(v.lt_vorp for v in send_values) - sum(v.lt_vorp for v in recv_values)
    would_keep = (
        band.min_surplus <= grade["blended"] <= band.max_surplus
        and band.min_their_face <= their_face_net <= band.max_their_face
        and band.min_their_lt <= their_lt_net <= band.max_their_lt
    )
    lineup = grade.get("lineup") or {}
    return {
        "send": [p["name"] for p in send_players],
        "receive": [p["name"] for p in recv_players],
        "kind": f"{len(send_players)}:{len(recv_players)}",
        "blended": grade["blended"],
        "delta_st": grade["delta_st"],
        "delta_lt": grade["delta_lt"],
        "roster": grade.get("roster"),
        "lineup": {
            "mode": lineup.get("mode"),
            "delta_st": lineup.get("delta_st"),
            "delta_lt": lineup.get("delta_lt"),
        },
        "verdict": grade["verdict"],
        "their_espn_face_net": round(their_face_net, 2),
        "their_ros_vorp_net": round(their_lt_net, 2),
        "would_keep_in_search": would_keep,
    }


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    ordered = sorted(vals)
    idx = int(round(p * (len(ordered) - 1)))
    return float(ordered[max(0, min(idx, len(ordered) - 1))])


def _round_up_5(value: float) -> float:
    if value <= 0:
        return 0.0
    return float(5 * int((value + 4.999) // 5))


def _round_down_5(value: float) -> float:
    if value >= 0:
        return float(5 * int(value // 5))
    # more negative floor toward -inf in steps of 5
    return float(-5 * int((-value + 4.999) // 5))


def recommend_band(win_sides: list[dict[str, Any]], current: Band, *, min_sample: int = 8) -> dict[str, Any]:
    """Raise the top edge / loosen give-up floors when accepts say we are too tight."""
    blends = [float(g["blended"]) for g in win_sides]
    faces = [float(g["their_espn_face_net"]) for g in win_sides]
    lts = [float(g["their_ros_vorp_net"]) for g in win_sides]
    if len(win_sides) < min_sample:
        return {
            "changed": False,
            "reason": f"need at least {min_sample} winning comparable sides (have {len(win_sides)})",
            "current": current.as_dict(),
            "recommended": current.as_dict(),
            "stats": {
                "n_win_sides": len(win_sides),
                "blend_p75": round(_percentile(blends, 0.75), 2) if blends else None,
            },
        }

    blend_p75 = _percentile(blends, 0.75)
    blend_p90 = _percentile(blends, 0.90)
    face_p10 = _percentile(faces, 0.10)
    lt_p10 = _percentile(lts, 0.10)

    # Top edge: cover most accepted +EV deals; one-step raise capped so outliers
    # (huge dynasty-like smash in a thin sample) cannot open the floodgates.
    raw_top = _round_up_5(blend_p75)
    suggested_max = max(current.max_surplus, raw_top)
    suggested_max = min(suggested_max, current.max_surplus + 20.0)
    suggested_max = min(suggested_max, max(current.max_surplus, _round_up_5(blend_p90)))

    # Floors: allow them to give up what the market gave up, but not unbounded.
    suggested_min_face = min(current.min_their_face, max(_round_down_5(face_p10), -120.0))
    suggested_min_lt = min(current.min_their_lt, max(_round_down_5(lt_p10), -150.0))

    recommended = Band(
        min_surplus=current.min_surplus,
        max_surplus=suggested_max,
        min_their_face=suggested_min_face,
        max_their_face=current.max_their_face,
        min_their_lt=suggested_min_lt,
        max_their_lt=current.max_their_lt,
    )
    changed = recommended != current
    reason = (
        f"winning-side blend p75={blend_p75:.1f} p90={blend_p90:.1f}; "
        f"their face p10={face_p10:.1f}; their ROS p10={lt_p10:.1f}"
    )
    return {
        "changed": changed,
        "reason": reason,
        "current": current.as_dict(),
        "recommended": recommended.as_dict(),
        "stats": {
            "n_win_sides": len(win_sides),
            "blend_p50": round(_percentile(blends, 0.50), 2),
            "blend_p75": round(blend_p75, 2),
            "blend_p90": round(blend_p90, 2),
            "their_face_p10": round(face_p10, 2),
            "their_lt_p10": round(lt_p10, 2),
        },
    }


def _norm_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def player_market_insights(
    sides: list[dict[str, Any]],
    *,
    our_names: list[str],
) -> dict[str, Any]:
    """Market signals for our roster from comparable accepts (not pricing)."""
    ours = {_norm_name(n): n for n in our_names}
    by_player: dict[str, dict[str, Any]] = {}

    def bucket(name: str) -> dict[str, Any]:
        if name not in by_player:
            by_player[name] = {
                "name": name,
                "acquired_n": 0,
                "sent_n": 0,
                "acquire_examples": [],
                "sent_examples": [],
                "median_blend_when_acquired": None,
                "median_blend_when_sent": None,
                "signal": "none",
            }
        return by_player[name]

    acquire_blends: dict[str, list[float]] = defaultdict(list)
    sent_blends: dict[str, list[float]] = defaultdict(list)

    for side in sides:
        recv = list(side.get("receive") or [])
        send = list(side.get("send") or [])
        blend = float(side.get("blended") or 0.0)
        for name in recv:
            key = _norm_name(name)
            if key not in ours:
                continue
            display = ours[key]
            row = bucket(display)
            row["acquired_n"] += 1
            acquire_blends[display].append(blend)
            if len(row["acquire_examples"]) < 4:
                row["acquire_examples"].append(
                    {
                        "gave_up": send,
                        "got": recv,
                        "kind": side.get("kind"),
                        "blended": blend,
                        "league_name": side.get("league_name"),
                    }
                )
        for name in send:
            key = _norm_name(name)
            if key not in ours:
                continue
            display = ours[key]
            row = bucket(display)
            row["sent_n"] += 1
            sent_blends[display].append(blend)
            if len(row["sent_examples"]) < 4:
                row["sent_examples"].append(
                    {
                        "gave_up": send,
                        "got": recv,
                        "kind": side.get("kind"),
                        "blended": blend,
                        "league_name": side.get("league_name"),
                    }
                )

    players_out = []
    for name, row in sorted(by_player.items(), key=lambda kv: -(kv[1]["acquired_n"] + kv[1]["sent_n"])):
        acq = acquire_blends.get(name) or []
        sent = sent_blends.get(name) or []
        if acq:
            row["median_blend_when_acquired"] = round(_percentile(acq, 0.5), 2)
        if sent:
            row["median_blend_when_sent"] = round(_percentile(sent, 0.5), 2)
        if row["acquired_n"] > row["sent_n"]:
            row["signal"] = "demand"
            row["hint"] = "Showed up more as the player being acquired — ask solid return if selling."
        elif row["sent_n"] > row["acquired_n"]:
            row["signal"] = "supply"
            row["hint"] = "Showed up more as the player being sent — market may be softer; buy-low possible."
        elif row["acquired_n"] or row["sent_n"]:
            row["signal"] = "mixed"
            row["hint"] = "Mixed acquire/send — treat as noise unless the packages rhyme."
        players_out.append(row)

    # Packages where we hold someone who was acquired: those returns are sell comps.
    sell_comps = []
    buy_comps = []
    for side in sides:
        recv = [ours[_norm_name(n)] for n in (side.get("receive") or []) if _norm_name(n) in ours]
        send = [ours[_norm_name(n)] for n in (side.get("send") or []) if _norm_name(n) in ours]
        if recv and not send and float(side.get("blended") or 0) > 0:
            sell_comps.append(
                {
                    "our_players_acquired_elsewhere": recv,
                    "what_market_gave_up": side.get("send"),
                    "kind": side.get("kind"),
                    "blended_for_acquirer": side.get("blended"),
                    "league_name": side.get("league_name"),
                    "note": "Market paid this package to get a player you hold — floor for sell talks.",
                }
            )
        if send and not recv and float(side.get("blended") or 0) > 0:
            buy_comps.append(
                {
                    "our_players_sent_elsewhere": send,
                    "what_market_got_back": side.get("receive"),
                    "kind": side.get("kind"),
                    "blended_for_sender": side.get("blended"),
                    "league_name": side.get("league_name"),
                    "note": "Someone sold a player you hold into this return — possible buy structure inverted.",
                }
            )

    return {
        "players": players_out,
        "sell_comps": sorted(sell_comps, key=lambda r: abs(float(r["blended_for_acquirer"] or 0)), reverse=True)[:12],
        "buy_comps": sorted(buy_comps, key=lambda r: abs(float(r["blended_for_sender"] or 0)), reverse=True)[:12],
        "pricing_note": (
            "These are acceptance / demand signals only. Do not bake them into VORP; "
            "use trade-grade for pricing and this report for the realism band + talk tracks."
        ),
    }


def calibrate_trade_market(
    *,
    reference: LeagueProfile,
    our_names: list[str] | None = None,
    seasons: list[str] | None = None,
    weeks: list[int] | None = None,
    max_leagues: int = 100,
    seed_league: str = SEED_LEAGUE,
    window: str = "bubble",
    apply: bool = False,
    cache_dir: Path | None = None,
    band_file: Path | None = None,
    min_sample: int = 8,
    rng_seed: int = 7,
) -> dict[str, Any]:
    """Pull comparable redraft accepts, recommend band, and roster market insights."""
    seasons = seasons or [str(datetime.now().year)]
    weeks = weeks or [1]
    cache_dir = cache_dir or (DEFAULT_CACHE / "trade-calib")
    cache_dir.mkdir(parents=True, exist_ok=True)
    current = active_band(band_file)
    random.seed(rng_seed)

    discovered = discover_leagues(seed_league, seasons=seasons, max_leagues=max_leagues)
    comparable: list[dict[str, Any]] = []
    skipped = Counter()
    for league in discovered:
        lid = str(league.get("league_id") or "")
        meta = league_meta(lid, cache_dir=cache_dir) or league
        profile = sleeper_league_profile(meta)
        if profile is None:
            skipped["not_redraft_or_exotic"] += 1
            continue
        if not profiles_compatible(profile, reference):
            skipped["settings_mismatch"] += 1
            continue
        comparable.append(meta if meta.get("league_id") else {**league, **meta})

    random.shuffle(comparable)
    comparable = comparable[:max_leagues]

    state = http_get("https://api.sleeper.app/v1/state/nfl") or {}
    season = int(state.get("league_season") or state.get("season") or seasons[0])
    current_week = int(state.get("week") or state.get("display_week") or weeks[0])
    directory = sleeper_players()
    ros_totals = remaining_week_totals(season, current_week, 17, scoring="ppr")
    week_proj = as_projection_map(cached_week_projections(season, current_week))
    fa_like = []
    pool_for_floors = []
    for pid, _pts in sorted(
        week_proj.items(),
        key=lambda kv: projection_points(kv[1], "ppr") or 0,
        reverse=True,
    )[:250]:
        row = sleeper_player_row(str(pid), directory=directory, ros_totals=ros_totals, week_proj=week_proj)
        if not row:
            continue
        pool_for_floors.append(row)
    for row in pool_for_floors[50:200]:
        fa_like.append(row)
    baselines = replacement_baselines(fa_like)
    starter_floors = starter_floor_baselines(pool_for_floors, team_count=reference.team_count)
    context = LeagueContext(
        current_week=current_week,
        season_end_week=17,
        wins=0,
        losses=0,
        standing=5,
        playoff_team_count=6,
        team_count=reference.team_count,
    )
    resolved_window = infer_window(context, window)

    side_grades: list[dict[str, Any]] = []
    skipped_sides = 0
    for meta in comparable:
        lid = str(meta.get("league_id"))
        for tx in fetch_trades(lid, weeks, cache_dir=cache_dir):
            sides = parse_sides(tx)
            if not sides:
                continue
            for _rid, bundle in sides.items():
                send_players: list[dict[str, Any]] = []
                recv_players: list[dict[str, Any]] = []
                ok = True
                for pid in bundle["drops"]:
                    row = sleeper_player_row(pid, directory=directory, ros_totals=ros_totals, week_proj=week_proj)
                    if not row:
                        ok = False
                        break
                    send_players.append(row)
                if not ok:
                    skipped_sides += 1
                    continue
                for pid in bundle["adds"]:
                    row = sleeper_player_row(pid, directory=directory, ros_totals=ros_totals, week_proj=week_proj)
                    if not row:
                        ok = False
                        break
                    recv_players.append(row)
                if not ok:
                    skipped_sides += 1
                    continue
                graded = grade_accepted_side(
                    send_players=send_players,
                    recv_players=recv_players,
                    context=context,
                    baselines=baselines,
                    window=resolved_window,
                    band=current,
                    starter_floors=starter_floors,
                )
                if not graded:
                    skipped_sides += 1
                    continue
                graded.update(
                    {
                        "league_id": lid,
                        "league_name": meta.get("name"),
                        "league_type": (meta.get("settings") or {}).get("type"),
                        "season": meta.get("season"),
                        "week": tx.get("week") or tx.get("leg"),
                        "transaction_id": tx.get("transaction_id"),
                    }
                )
                side_grades.append(graded)

    our_kinds = [g for g in side_grades if g.get("kind") in OUR_KINDS]
    win_sides = [g for g in our_kinds if float(g.get("blended") or 0) > 0]
    band_rec = recommend_band(win_sides, current, min_sample=min_sample)
    applied_path = None
    if apply and band_rec.get("changed"):
        applied_path = str(
            save_band(
                Band(**band_rec["recommended"]),
                meta={"source": "trade-calibrate", "stats": band_rec.get("stats")},
                path=band_file,
            )
        )
    elif apply and not band_rec.get("changed"):
        applied_path = None

    insights = player_market_insights(our_kinds, our_names=our_names or [])

    kept_pct = (
        round(100.0 * sum(1 for g in our_kinds if g.get("would_keep_in_search")) / len(our_kinds), 1)
        if our_kinds
        else 0.0
    )

    return {
        "pricing_policy": (
            "Trade accepts adjust the search realism band only. "
            "Player VORP / trade-grade pricing stays projection-based."
        ),
        "reference_league": asdict(reference),
        "sample": {
            "discovered": len(discovered),
            "comparable_leagues": len(comparable),
            "skipped_leagues": dict(skipped),
            "graded_sides": len(side_grades),
            "our_kind_sides": len(our_kinds),
            "winning_our_kind_sides": len(win_sides),
            "skipped_sides": skipped_sides,
            "weeks": weeks,
            "seasons": seasons,
            "would_keep_in_current_band_pct": kept_pct,
        },
        "kinds": dict(Counter(g["kind"] for g in our_kinds)),
        "band": band_rec,
        "applied_band_path": applied_path,
        "active_band": active_band(band_file).as_dict(),
        "roster_insights": insights,
        "examples_near_even": sorted(our_kinds, key=lambda g: abs(float(g["blended"])))[:8],
        "examples_winning": sorted(win_sides, key=lambda g: float(g["blended"]), reverse=True)[:8],
    }
