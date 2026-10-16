"""Redraft player value, weighted week by week.

A player's value is the weighted sum of their per-week VORP over the remaining
weeks:

    value = sum_k  w_k * (projection_k * availability_k - replacement)

`w_k` comes from `leverage.week_weights` and sums to 1, so values read as points
per weighted game above replacement. Projections prefer a real per-week curve
(Sleeper publishes one per week) and fall back to a remaining-season total spread
across the remaining games. Availability ramps an injury back toward full over
the weeks ahead rather than applying one flat discount to the whole season.

`st_vorp` and `lt_vorp` survive as a split of that single weighted sum — the
short-term window and everything after it — so they add to `blended` instead of
overlapping it, which is what used to inflate these numbers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from league_manager.leverage import WeekWeights, week_weights
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

# status -> (share of a game available this week, weeks until back to full).
# An OUT player is worth nothing now and close to whole in three weeks, which a
# single season-long discount cannot express.
INJURY_RAMP = {
    "IR": (0.0, 6),
    "INJURY_RESERVE": (0.0, 6),
    "OUT": (0.0, 3),
    "SUSPENSION": (0.0, 4),
    "DOUBTFUL": (0.25, 2),
    "QUESTIONABLE": (0.75, 1),
}
# Flagged injured with no status attached.
INJURY_RAMP_UNKNOWN = (0.35, 3)

# How urgent the season is. This no longer weights time — `leverage.week_weights`
# does that from playoff odds — it only picks how much asset depth matters
# against the starting lineup.
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
    # Active (non-IR) roster spots. None → graders treat current occupancy as full.
    roster_cap: int | None = None

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
        if override not in STRUCTURE_WEIGHTS:
            raise ValueError(f"Unknown window {override!r}. Use contender, bubble, rebuilder, or auto.")
        return override
    rank = context.standing or context.team_count
    spots = max(int(context.playoff_team_count), 1)
    if rank <= max(1, round(spots * 0.6)) or context.win_pct >= 0.65:
        return "contender"
    if rank <= spots + 1 or context.win_pct >= 0.45:
        return "bubble"
    return "rebuilder"


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


def availability(player: dict[str, Any], *, weeks_out: int) -> float:
    """Share of a full game expected `weeks_out` weeks from now.

    Ramps linearly from the status's immediate share back to 1.0, so an injury
    discounts the next week or two hard and the rest of the season barely.
    """
    status = str(player.get("injury_status") or "").upper()
    if status in INJURY_RAMP:
        immediate, ramp = INJURY_RAMP[status]
    elif player.get("injured"):
        immediate, ramp = INJURY_RAMP_UNKNOWN
    else:
        return 1.0
    if ramp <= 0 or weeks_out >= ramp:
        return 1.0
    return immediate + (1.0 - immediate) * (max(0, weeks_out) / ramp)


def games_in_weeks(weeks: Iterable[int], bye_week: int | None) -> list[int]:
    return [week for week in weeks if bye_week is None or int(week) != int(bye_week)]


def weekly_projection_curve(
    player: dict[str, Any],
    context: LeagueContext,
) -> tuple[dict[int, float | None], str]:
    """Expected points per remaining week, or None for a week not being played.

    A published per-week projection is trusted as given. The sources already
    price an injury into the week it affects (a questionable starter is marked
    down that week and whole the next), and they signal the weeks a player is
    expected to miss by omitting them entirely, which is a better timeline than
    any status tag -- players carrying a stale `INJURY_RESERVE` tag are still
    projected at full strength every week, and discounting those by the tag
    would throw away the better signal.

    So `availability` only applies to a player with no published per-week
    coverage at all, where the status tag is the only injury information there
    is. A week with coverage elsewhere but none of its own is a week off (a bye
    or an injury); a bench starter covers that slot, so it scores no surplus
    rather than a penalty.
    """
    weeks = context.remaining_weeks
    current = int(context.current_week)
    bye = player.get("bye_week")
    bye_week = int(bye) if bye is not None else None

    def _weeks_from(entries: Any) -> dict[int, float]:
        """Published numbers per week, zeros included.

        A published 0 is information, not a gap: it is how ESPN marks a bye or a
        week a player will not play. Dropping it sends the week to a fallback
        that substitutes a season average, which starts players on bye.
        """
        found: dict[int, float] = {}
        if not isinstance(entries, dict):
            return found
        for key, entry in entries.items():
            try:
                week = int(key)
            except (TypeError, ValueError):
                continue
            raw = entry.get("projected_points") if isinstance(entry, dict) else entry
            value = _float_or_none(raw)
            if value is not None and value >= 0:
                found[week] = value
        return found

    curve: dict[int, float | None]
    sleeper_curve = player.get("weekly_projections")
    if isinstance(sleeper_curve, dict):
        # Sleeper publishes the whole remaining season and leaves out the weeks
        # it expects a player to miss, so an absence here is information.
        published = _weeks_from(sleeper_curve)
        # Average in ESPN wherever both have a number for the same week. ESPN
        # only carries the current week, so in practice this is the documented
        # this-week blend. A week Sleeper leaves out stays left out: its absence
        # is the signal, and blending would put a sidelined player back.
        espn_weeks = _weeks_from(player.get("weekly_stats"))
        curve = {}
        for week in weeks:
            points = published.get(week)
            espn = espn_weeks.get(week)
            if not points or espn == 0:
                # Omitted by Sleeper, or marked zero by either source: not playing.
                curve[week] = None
                continue
            curve[week] = (points + espn) / 2 if espn else points
        covered = sum(1 for week in weeks if curve[week] is not None)
        if covered == len(weeks):
            source = "weekly"
        elif covered:
            source = "weekly_partial"
        else:
            source = "weekly_none"
    else:
        # No per-week curve for this player, so spread a remaining-season total
        # and lean on the status tag for availability. ESPN's weekly_stats only
        # ever holds the current and past weeks, so its gaps say nothing about
        # whether a player is playing and must not be read as weeks off.
        games = len(games_in_weeks(weeks, bye_week))
        total, source = ros_points(player, lt_games=games, lt_factor=1.0)
        flat = total / games if games else 0.0
        espn_weeks = _weeks_from(player.get("weekly_stats"))
        curve = {}
        for week in weeks:
            rate = espn_weeks.get(week)
            if rate == 0:
                # Published zero: a bye or a week off, not missing data.
                curve[week] = None
                continue
            if rate is None:
                # This week's own number beats a season average spread over it.
                rate = (weekly_rate(player) or flat) if week == current else flat
            curve[week] = rate * availability(player, weeks_out=week - current)
        if any(week in espn_weeks for week in weeks):
            source = f"{source}+espn_week"
    if bye_week is not None:
        curve[bye_week] = None
    return curve, source


def weekly_vorp_curve(
    player: dict[str, Any],
    context: LeagueContext,
    baselines: dict[str, float],
) -> dict[int, float]:
    """Per-week points above replacement. Kept out of PlayerValue to stay small."""
    repl = replacement_weekly(player, baselines)
    curve, _ = weekly_projection_curve(player, context)
    vorp: dict[int, float] = {}
    for week in context.remaining_weeks:
        points = curve.get(week)
        # A week off scores no surplus: a bench starter covers that slot.
        vorp[week] = 0.0 if points is None else points - repl
    return vorp


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
    weight_scheme: str = "leverage"
    short_term_weight: float = 0.0
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
    weights: WeekWeights | None = None,
) -> PlayerValue:
    resolved_window = infer_window(context, window)
    weights = weights or week_weights(context)
    rate = weekly_rate(player)
    repl = replacement_weekly(player, baselines)
    bye = player.get("bye_week")
    bye_week = int(bye) if bye is not None else None
    short_weeks = set(context.short_term_week_list)
    st_games = games_in_weeks(context.short_term_week_list, bye_week)
    lt_games = games_in_weeks(context.remaining_weeks, bye_week)

    curve, ros_source = weekly_projection_curve(player, context)
    st_vorp = lt_vorp = 0.0
    st_points = lt_points = 0.0
    weeks_off: list[int] = []
    for week in context.remaining_weeks:
        points = curve.get(week)
        if points is None:
            # A week off scores no surplus: a bench starter covers that slot.
            weeks_off.append(week)
            continue
        weight = weights.weight(week)
        contribution = weight * (points - repl)
        if week in short_weeks:
            st_points += weight * points
            st_vorp += contribution
        else:
            lt_points += weight * points
            lt_vorp += contribution
    # Non-overlapping by construction, so the split adds back to the whole.
    blended = st_vorp + lt_vorp

    notes = []
    if bye_week in context.short_term_week_list:
        notes.append(f"bye in week {bye_week}")
    if weeks_off:
        shown = ", ".join(str(week) for week in weeks_off[:6])
        more = f" (+{len(weeks_off) - 6} more)" if len(weeks_off) > 6 else ""
        notes.append(f"not projected to play weeks {shown}{more}")
    elif "weekly" not in ros_source:
        now_avail = availability(player, weeks_out=0)
        if now_avail < 1:
            notes.append(
                f"no weekly projections published; {player.get('injury_status')} "
                f"discounts this week to {now_avail:.0%}, ramping back to full"
            )
    if ros_source != "weekly_times_games":
        notes.append(f"ROS from {ros_source}")
    notes.append(
        f"{weights.scheme} weights: {weights.mass(short_weeks):.0%} of the season's "
        f"weight is in the next {len(context.short_term_week_list)} weeks"
    )
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
        weight_scheme=weights.scheme,
        short_term_weight=round(weights.mass(short_weeks), 4),
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
    weights: WeekWeights | None = None,
) -> list[PlayerValue]:
    weights = weights or week_weights(context)
    valued = [
        value_player(player, context, baselines, window=window, weights=weights)
        for player in players
    ]
    valued.sort(key=lambda item: item.blended, reverse=True)
    return valued


def roster_cap_from_settings(settings: Any) -> int | None:
    """Active roster spots from ESPN slot counts (excludes IR / taxi / empty)."""
    if settings is None:
        return None
    roster_map = (
        getattr(settings, "position_slot_counts", None)
        or getattr(settings, "roster_settings", None)
        or {}
    )
    if not isinstance(roster_map, dict) or not roster_map:
        return None
    skip = {"IR", "ER", "ROOKIE", "", "21", "24", "25"}
    total = 0
    for key, count in roster_map.items():
        label = str(key).upper()
        if label in skip:
            continue
        try:
            n = int(count or 0)
        except (TypeError, ValueError):
            continue
        if n > 0:
            total += n
    return total or None


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
        roster_cap=roster_cap_from_settings(settings),
    )
