"""Redraft short-term and rest-of-season player value.

Short-term is this week's rate over the next few games. Long-term prefers a
real remaining-season total (FantasyPros ROS, Sleeper remaining weeks by default,
or ESPN season projection minus points already scored) before falling back to
this week flattened across the rest of the year.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from league_manager.projections import primary_projection
from league_manager.slots import normalize_position

DEFAULT_SHORT_TERM_WEEKS = 3
DEFAULT_SEASON_END_WEEK = 17
REPLACEMENT_FA_INDEX = 2  # 0-based; third-best available FA at the position

INJURY_ST_FACTOR = {
    "IR": 0.0,
    "OUT": 0.0,
    "DOUBTFUL": 0.25,
    "QUESTIONABLE": 0.75,
    "SUSPENSION": 0.0,
}

INJURY_LT_FACTOR = {
    "IR": 0.35,
    "OUT": 0.55,
    "DOUBTFUL": 0.7,
    "QUESTIONABLE": 0.9,
    "SUSPENSION": 0.5,
}

WINDOW_WEIGHTS = {
    "contender": (0.65, 0.35),
    "bubble": (0.5, 0.5),
    "rebuilder": (0.3, 0.7),
}

# roster weight, lineup weight — how much asset VORP vs starting-XI points matter
STRUCTURE_WEIGHTS = {
    "contender": (0.35, 0.65),
    "bubble": (0.45, 0.55),
    "rebuilder": (0.60, 0.40),
}


@dataclass(frozen=True)
class LeagueContext:
    current_week: int
    season_end_week: int = DEFAULT_SEASON_END_WEEK
    regular_season_end: int = 14
    playoff_team_count: int = 4
    team_count: int = 10
    short_term_weeks: int = DEFAULT_SHORT_TERM_WEEKS
    wins: int = 0
    losses: int = 0
    ties: int = 0
    standing: int | None = None

    @property
    def remaining_weeks(self) -> list[int]:
        start = max(int(self.current_week), 1)
        end = max(int(self.season_end_week), start)
        return list(range(start, end + 1))

    @property
    def short_term_week_list(self) -> list[int]:
        return self.remaining_weeks[: max(1, int(self.short_term_weeks))]

    @property
    def games_played(self) -> int:
        return int(self.wins) + int(self.losses) + int(self.ties)

    @property
    def win_pct(self) -> float:
        played = self.games_played
        if played <= 0:
            return 0.5
        return (int(self.wins) + 0.5 * int(self.ties)) / played


def infer_window(context: LeagueContext, override: str | None = None) -> str:
    if override and override != "auto":
        if override not in WINDOW_WEIGHTS:
            raise ValueError(f"Unknown window {override!r}. Use contender, bubble, rebuilder, or auto.")
        return override
    rank = context.standing or context.team_count
    spots = max(int(context.playoff_team_count), 1)
    if rank <= max(1, round(spots * 0.6)) or context.win_pct >= 0.65:
        return "contender"
    if rank <= spots + 1 or context.win_pct >= 0.45:
        return "bubble"
    return "rebuilder"


def window_weights(window: str) -> tuple[float, float]:
    return WINDOW_WEIGHTS[window]


def structure_weights(window: str) -> tuple[float, float]:
    """(roster_weight, lineup_weight) for trade grading."""
    return STRUCTURE_WEIGHTS[window]


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def espn_weekly_only(player: dict[str, Any]) -> float:
    """ESPN this-week / average only. Used as the other manager's face value."""
    for key in ("projected_points", "projected_avg_points"):
        value = _float_or_none(player.get(key))
        if value is not None and value > 0:
            return value
    return 0.0


def espn_season_remainder(player: dict[str, Any]) -> float | None:
    """ESPN season-long projection minus points already scored."""
    total = _float_or_none(player.get("projected_total_points"))
    if total is None or total <= 0:
        return None
    scored = _float_or_none(player.get("points")) or 0.0
    remainder = total - scored
    if remainder <= 0:
        return None
    return remainder


def ros_points(player: dict[str, Any], *, lt_games: int, lt_factor: float) -> tuple[float, str]:
    """Remaining-season points and the source that produced them."""
    fp_ros = _float_or_none(player.get("fantasypros_ros_points"))
    if fp_ros is not None and fp_ros > 0:
        return fp_ros * lt_factor, "fantasypros_ros"
    sleeper_ros = _float_or_none(player.get("sleeper_ros_points"))
    if sleeper_ros is not None and sleeper_ros > 0:
        return sleeper_ros * lt_factor, "sleeper_ros"
    remainder = espn_season_remainder(player)
    if remainder is not None:
        return remainder * lt_factor, "espn_remainder"
    fp_week = _float_or_none(player.get("fantasypros_weekly_points"))
    if fp_week is not None and fp_week > 0:
        return fp_week * lt_games * lt_factor, "fantasypros_weekly"
    rate = weekly_rate(player)
    return rate * lt_games * lt_factor, "weekly_times_games"


def espn_face_value(player: dict[str, Any], context: LeagueContext) -> float:
    """What another manager sees: ESPN weekly rate x remaining games x injury."""
    bye = player.get("bye_week")
    bye_week = int(bye) if bye is not None else None
    games = games_in_weeks(context.remaining_weeks, bye_week)
    return espn_weekly_only(player) * len(games) * injury_factor(player, horizon="lt")


def weekly_rate(player: dict[str, Any]) -> float:
    status = str(player.get("injury_status") or "").upper()
    raw = player.get("projected_points")
    avg = player.get("projected_avg_points")
    if raw is not None:
        try:
            value = float(raw)
            if value > 0 or status in INJURY_ST_FACTOR:
                return value if value > 0 else float(avg or 0.0)
            if avg is not None:
                return float(avg)
        except (TypeError, ValueError):
            pass
    if avg is not None:
        try:
            return float(avg)
        except (TypeError, ValueError):
            pass
    return primary_projection(player)


def injury_factor(player: dict[str, Any], *, horizon: str) -> float:
    if player.get("injured") and not player.get("injury_status"):
        return 0.35 if horizon == "st" else 0.6
    status = str(player.get("injury_status") or "").upper()
    table = INJURY_ST_FACTOR if horizon == "st" else INJURY_LT_FACTOR
    return table.get(status, 1.0)


def games_in_weeks(weeks: Iterable[int], bye_week: int | None) -> list[int]:
    return [week for week in weeks if bye_week is None or int(week) != int(bye_week)]


def replacement_baselines(
    free_agents: list[dict[str, Any]],
    *,
    index: int = REPLACEMENT_FA_INDEX,
) -> dict[str, float]:
    by_pos: dict[str, list[float]] = {}
    for player in free_agents:
        pos = normalize_position(str(player.get("position") or ""))
        if not pos:
            continue
        by_pos.setdefault(pos, []).append(weekly_rate(player))
    baselines = {}
    for pos, rates in by_pos.items():
        rates.sort(reverse=True)
        pick = rates[min(index, len(rates) - 1)]
        baselines[pos] = max(0.0, float(pick))
    return baselines


def replacement_weekly(player: dict[str, Any], baselines: dict[str, float]) -> float:
    pos = normalize_position(str(player.get("position") or ""))
    if pos in baselines:
        return baselines[pos]
    if pos in {"RB", "WR", "TE"} and "FLEX" in baselines:
        return baselines["FLEX"]
    return 0.0


@dataclass
class PlayerValue:
    id: Any
    name: str | None
    position: str
    weekly_rate: float
    replacement_weekly: float
    st_games: int
    lt_games: int
    st_points: float
    lt_points: float
    st_vorp: float
    lt_vorp: float
    blended: float
    window: str
    ros_source: str = "weekly_times_games"
    injured: bool = False
    injury_status: str | None = None
    bye_week: int | None = None
    team_id: Any = None
    team_name: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def value_player(
    player: dict[str, Any],
    context: LeagueContext,
    baselines: dict[str, float],
    *,
    window: str | None = None,
) -> PlayerValue:
    resolved_window = infer_window(context, window)
    st_w, lt_w = window_weights(resolved_window)
    rate = weekly_rate(player)
    repl = replacement_weekly(player, baselines)
    bye = player.get("bye_week")
    bye_week = int(bye) if bye is not None else None
    st_games = games_in_weeks(context.short_term_week_list, bye_week)
    lt_games = games_in_weeks(context.remaining_weeks, bye_week)
    st_factor = injury_factor(player, horizon="st")
    lt_factor = injury_factor(player, horizon="lt")
    st_points = rate * len(st_games) * st_factor
    lt_points, ros_source = ros_points(player, lt_games=len(lt_games), lt_factor=lt_factor)
    st_vorp = st_points - repl * len(st_games)
    lt_vorp = lt_points - repl * len(lt_games)
    blended = st_w * st_vorp + lt_w * lt_vorp
    notes = []
    if bye_week in context.short_term_week_list:
        notes.append(f"bye in week {bye_week}")
    if st_factor < 1:
        notes.append(f"short-term injury discount {st_factor:.0%}")
    if ros_source != "weekly_times_games":
        notes.append(f"ROS from {ros_source}")
    return PlayerValue(
        id=player.get("id"),
        name=player.get("name"),
        position=normalize_position(str(player.get("position") or "")),
        weekly_rate=round(rate, 2),
        replacement_weekly=round(repl, 2),
        st_games=len(st_games),
        lt_games=len(lt_games),
        st_points=round(st_points, 2),
        lt_points=round(lt_points, 2),
        st_vorp=round(st_vorp, 2),
        lt_vorp=round(lt_vorp, 2),
        blended=round(blended, 2),
        window=resolved_window,
        ros_source=ros_source,
        injured=bool(player.get("injured")),
        injury_status=player.get("injury_status"),
        bye_week=bye_week,
        team_id=player.get("team_id"),
        team_name=player.get("team_name"),
        notes=notes,
    )


def value_players(
    players: list[dict[str, Any]],
    context: LeagueContext,
    baselines: dict[str, float],
    *,
    window: str | None = None,
) -> list[PlayerValue]:
    valued = [value_player(player, context, baselines, window=window) for player in players]
    valued.sort(key=lambda item: item.blended, reverse=True)
    return valued


def context_from_league(
    league: Any,
    team: Any | None = None,
    *,
    short_term_weeks: int = DEFAULT_SHORT_TERM_WEEKS,
    season_end_week: int | None = None,
) -> LeagueContext:
    settings = getattr(league, "settings", None)
    current = int(getattr(league, "current_week", None) or 1)
    regular = int(getattr(settings, "reg_season_count", None) or 14)
    playoff_teams = int(getattr(settings, "playoff_team_count", None) or 4)
    teams = int(getattr(settings, "team_count", None) or len(getattr(league, "teams", []) or []))
    end = season_end_week or max(DEFAULT_SEASON_END_WEEK, regular + 3)
    standing = getattr(team, "standing", None) if team is not None else None
    return LeagueContext(
        current_week=current,
        season_end_week=end,
        regular_season_end=regular,
        playoff_team_count=playoff_teams,
        team_count=teams,
        short_term_weeks=short_term_weeks,
        wins=int(getattr(team, "wins", 0) or 0) if team is not None else 0,
        losses=int(getattr(team, "losses", 0) or 0) if team is not None else 0,
        ties=int(getattr(team, "ties", 0) or 0) if team is not None else 0,
        standing=int(standing) if standing is not None else None,
    )
