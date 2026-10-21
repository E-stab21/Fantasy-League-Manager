"""Read and write ESPN fantasy league data using the unofficial v3 API."""

from __future__ import annotations

from typing import Any

from league_manager.config import Settings
from league_manager.leverage import ROLE_REGULAR, week_weights
from league_manager.roster_roles import RoleScorer, assign_roles, role_weight_set
from league_manager.market import DEFAULT_LOOKBACK, find_opportunities
from league_manager.projections import league_scoring
from league_manager.search import parse_kinds, search_trades
from league_manager.serialize import matchup_to_dict, player_to_dict, team_to_dict
from league_manager.advise import optimal_lineup, rank_waiver_claims
from league_manager.trades import grade_trade
from league_manager.value import (
    DEFAULT_SHORT_TERM_WEEKS,
    context_from_league,
    infer_window,
    replacement_baselines,
    value_players,
    weekly_projection_curve,
)
from league_manager.writes import WriteResult, post_transaction

SPORT_IMPORT = {
    "nfl": "espn_api.football",
    "nba": "espn_api.basketball",
    "mlb": "espn_api.baseball",
    "nhl": "espn_api.hockey",
}

# How many free agents to pull when the pool is what sets replacement level.
# ESPN returns its own order (roughly ownership), not projection order, so a
# shallow pool can miss the best available player at a position entirely and
# set the bar too low. Measured: the QB baseline reads 11.52 at 25 agents,
# 14.87 at 50, 15.19 at 80 and settles at 16.35 from 150 on, swinging the whole
# roster total from 33.84 to 21.30 on a scale where a trade blend is +/-16.
# Every command that computes a baseline uses this, so they agree on the bar.
REPLACEMENT_FA_POOL = 150


class EspnClient:
    def __init__(self, settings: Settings, league: Any | None = None):
        self.settings = settings
        self._league = league

    @property
    def league(self) -> Any:
        if self._league is None:
            self._league = self._connect()
        return self._league

    def _connect(self) -> Any:
        import importlib

        sport = self.settings.sport.lower()
        if sport in {"ffl", "football"}:
            sport = "nfl"
        elif sport in {"fba", "basketball"}:
            sport = "nba"
        elif sport in {"flb", "baseball"}:
            sport = "mlb"
        elif sport in {"fhl", "hockey"}:
            sport = "nhl"
        module_name = SPORT_IMPORT.get(sport)
        if not module_name:
            raise ValueError(f"Unsupported sport {self.settings.sport!r}")
        module = importlib.import_module(module_name)
        return module.League(
            league_id=self.settings.league_id,
            year=self.settings.season,
            espn_s2=self.settings.espn_s2,
            swid=self.settings.swid,
        )

    def resolve_team_id(self, team_id: int | None = None) -> int:
        if team_id is not None:
            return team_id
        if self.settings.team_id is not None:
            return self.settings.team_id
        swid = self.settings.swid.lower()
        for team in self.league.teams:
            for owner in getattr(team, "owners", []) or []:
                owner_id = owner.get("id") if isinstance(owner, dict) else str(owner)
                if owner_id and str(owner_id).lower() == swid:
                    return team.team_id
        if len(self.league.teams) == 1:
            return self.league.teams[0].team_id
        raise ValueError(
            "Could not determine your team. Set ESPN_TEAM_ID or pass --team-id."
        )

    def get_team(self, team_id: int | None = None) -> Any:
        resolved = self.resolve_team_id(team_id)
        for team in self.league.teams:
            if team.team_id == resolved:
                return team
        raise ValueError(f"No team with id {resolved}")

    def ping(self) -> dict[str, Any]:
        league = self.league
        team = None
        team_error = None
        try:
            team = self.get_team()
        except ValueError as exc:
            team_error = str(exc)
        return {
            "ok": True,
            "league_id": self.settings.league_id,
            "season": self.settings.season,
            "sport": self.settings.sport,
            "name": getattr(league.settings, "name", None),
            "week": getattr(league, "current_week", None),
            "nfl_week": getattr(league, "nfl_week", None),
            "team_count": len(getattr(league, "teams", []) or []),
            "your_team": team_to_dict(team, include_roster=False) if team else None,
            "team_error": team_error,
        }

    def status(self) -> dict[str, Any]:
        league = self.league
        settings = league.settings
        return {
            "league_id": self.settings.league_id,
            "name": getattr(settings, "name", None),
            "season": self.settings.season,
            "week": getattr(league, "current_week", None),
            "nfl_week": getattr(league, "nfl_week", None),
            "scoring_type": getattr(settings, "scoring_type", None)
            or getattr(settings, "reg_season_count", None),
            "team_count": getattr(settings, "team_count", None)
            or len(league.teams),
            "playoff_team_count": getattr(settings, "playoff_team_count", None),
            "your_team": team_to_dict(self.get_team(), include_roster=False),
        }

    def standings(self) -> list[dict[str, Any]]:
        rows = []
        standing_fn = getattr(self.league, "standings", None)
        teams = standing_fn() if callable(standing_fn) else self.league.teams
        for index, team in enumerate(teams, start=1):
            row = team_to_dict(team, include_roster=False)
            row["rank"] = row.get("standing") or index
            rows.append(row)
        return rows

    def roster(self, team_id: int | None = None) -> dict[str, Any]:
        team = self.get_team(team_id)
        return team_to_dict(team, include_roster=True)

    def matchup(self, week: int | None = None, team_id: int | None = None) -> dict[str, Any]:
        resolved = self.resolve_team_id(team_id)
        scoreboard = self.league.scoreboard(week=week)
        for game in scoreboard:
            payload = matchup_to_dict(game)
            if payload.get("home_id") == resolved or payload.get("away_id") == resolved:
                payload["week"] = week or getattr(self.league, "current_week", None)
                return payload
        return {
            "week": week or getattr(self.league, "current_week", None),
            "message": "No matchup found for that team and week.",
        }

    def scoreboard(self, week: int | None = None) -> list[dict[str, Any]]:
        return [matchup_to_dict(game) for game in self.league.scoreboard(week=week)]

    def free_agents(
        self,
        position: str | None = None,
        size: int = 50,
        week: int | None = None,
    ) -> list[dict[str, Any]]:
        players = self.league.free_agents(week=week, size=size, position=position)
        return [player_to_dict(player, include_lineup=False) for player in players]

    def player(self, name: str) -> dict[str, Any]:
        found = self.league.player_info(name=name)
        if not found:
            return {"name": name, "found": False}
        if isinstance(found, list):
            return {"found": True, "players": [player_to_dict(item, include_lineup=False) for item in found]}
        return {"found": True, "player": player_to_dict(found, include_lineup=False)}

    def rostered_players(self) -> dict[Any, dict[str, Any]]:
        found: dict[Any, dict[str, Any]] = {}
        for team in self.league.teams:
            payload = team_to_dict(team, include_roster=True)
            for player in payload.get("roster") or []:
                player["team_id"] = payload.get("id")
                player["team_name"] = payload.get("name")
                if player.get("id") is not None:
                    found[player["id"]] = player
        return found

    def _enrich_players(
        self,
        players: list[dict[str, Any]],
        *,
        current_week: int,
        season_end_week: int,
        sleeper: bool = True,
    ) -> list[dict[str, Any]]:
        scoring = league_scoring(self.league)
        enriched = players
        if sleeper:
            from league_manager.projections import attach_sleeper_projections, attach_sleeper_ros

            try:
                enriched = attach_sleeper_projections(
                    enriched,
                    season=self.settings.season,
                    week=current_week,
                    scoring=scoring,
                )
                enriched = attach_sleeper_ros(
                    enriched,
                    season=self.settings.season,
                    current_week=current_week,
                    through=season_end_week,
                    scoring=scoring,
                )
            except Exception:
                # Offline / rate-limit: fall through to ESPN remainder / weekly.
                pass
        if self.settings.fantasypros_api_key:
            from league_manager.fantasypros import attach_fantasypros

            fp_scoring = {"ppr": "PPR", "half": "HALF", "std": "STD"}.get(scoring, "PPR")
            enriched = attach_fantasypros(
                enriched,
                api_key=self.settings.fantasypros_api_key,
                season=self.settings.season,
                scoring=fp_scoring,
            )
        return enriched

    def _ros_source_flags(self, *, sleeper: bool) -> dict[str, bool]:
        return {
            "espn_remainder": True,
            "sleeper_ros": bool(sleeper),
            "fantasypros": bool(self.settings.fantasypros_api_key),
        }

    def player_index(
        self,
        extra: list[dict[str, Any]] | None = None,
        *,
        fa_size: int = REPLACEMENT_FA_POOL,
    ) -> dict[Any, dict[str, Any]]:
        found = self.rostered_players()
        for player in extra or []:
            if player.get("id") is not None:
                found.setdefault(player["id"], player)
        if fa_size:
            for player in self.free_agents(size=fa_size):
                if player.get("id") is not None:
                    found.setdefault(player["id"], player)
        return found

    def values(
        self,
        team_id: int | None = None,
        *,
        window: str = "auto",
        short_term_weeks: int = DEFAULT_SHORT_TERM_WEEKS,
        season_end_week: int | None = None,
        fa_size: int = REPLACEMENT_FA_POOL,
        sleeper: bool = True,
    ) -> dict[str, Any]:
        team = self.get_team(team_id)
        context = context_from_league(
            self.league,
            team,
            short_term_weeks=short_term_weeks,
            season_end_week=season_end_week,
        )
        roster = team_to_dict(team, include_roster=True)
        players = roster.get("roster") or []
        free_agents = self.free_agents(size=fa_size)
        players = self._enrich_players(
            players,
            current_week=context.current_week,
            season_end_week=context.season_end_week,
            sleeper=sleeper,
        )
        free_agents = self._enrich_players(
            free_agents,
            current_week=context.current_week,
            season_end_week=context.season_end_week,
            sleeper=sleeper,
        )
        baselines = replacement_baselines(free_agents)
        resolved_window = infer_window(context, window)
        weights = week_weights(context)
        valued = value_players(
            players, context, baselines, window=resolved_window, weights=weights
        )
        curve_cache: dict[Any, dict[int, float | None]] = {}

        def curve_for(player: dict[str, Any]) -> dict[int, float | None]:
            pid = player.get("id")
            if pid not in curve_cache:
                curve_cache[pid], _ = weekly_projection_curve(player, context)
            return curve_cache[pid]

        scorer = RoleScorer(
            context, baselines, role_weight_set(weights, context), curve_for=curve_for
        )
        roles = assign_roles(
            players,
            scorer=scorer,
            stash_slots=context.stash_slots,
            stream_slots=context.stream_slots,
            stash_ratio_threshold=context.stash_ratio_threshold,
            stream_ratio_threshold=context.stream_ratio_threshold,
            value_floor=context.role_value_floor,
        )
        by_id = {player.get("id"): player for player in players}
        rows = []
        for item in valued:
            row = item.to_dict()
            role = roles.get(item.id, ROLE_REGULAR)
            row["role"] = role
            source = by_id.get(item.id)
            if source is not None:
                row["role_value"] = round(scorer.value(source, role), 2)
            rows.append(row)
        return {
            "team": {"id": roster.get("id"), "name": roster.get("name")},
            "window": resolved_window,
            "week_weights": weights.to_dict(),
            "sources": self._ros_source_flags(sleeper=sleeper),
            "context": {
                "current_week": context.current_week,
                "short_term_weeks": context.short_term_week_list,
                "remaining_weeks": context.remaining_weeks,
                "season_end_week": context.season_end_week,
                "standing": context.standing,
                "record": [context.wins, context.losses, context.ties],
            },
            "replacement_weekly": baselines,
            "reserved_slots": {
                "stash": context.stash_slots,
                "stream": context.stream_slots,
            },
            "players": rows,
        }

    def lineup_advice(
        self,
        team_id: int | None = None,
        *,
        sleeper: bool = True,
    ) -> dict[str, Any]:
        """Recommend a starting lineup for the current week.

        Uses the same enrichment the valuations do, so Sleeper's per-week curve
        is available. Without it the only injury and bye signal is ESPN's
        current-week number, and a player on bye falls back to a season average.
        """
        team = self.get_team(team_id)
        context = context_from_league(self.league, team)
        payload = team_to_dict(team, include_roster=True)
        players = self._enrich_players(
            payload.get("roster") or [],
            current_week=context.current_week,
            season_end_week=context.season_end_week,
            sleeper=sleeper,
        )
        # Only feeds the gap alerts: an uncovered or below-replacement slot is
        # worth little without naming who could fill it.
        free_agents = self._enrich_players(
            self.free_agents(size=REPLACEMENT_FA_POOL),
            current_week=context.current_week,
            season_end_week=context.season_end_week,
            sleeper=sleeper,
        )
        advice = optimal_lineup(
            players,
            context=context,
            free_agents=free_agents,
            baselines=replacement_baselines(free_agents),
        )
        advice["team"] = {"id": payload.get("id"), "name": payload.get("name")}
        advice["week"] = context.current_week
        advice["sources"] = {
            "espn_week": True,
            "sleeper_week": bool(sleeper),
            "blend": "average" if sleeper else "espn",
        }
        return advice

    def waiver_advice(
        self,
        team_id: int | None = None,
        *,
        position: str | None = None,
        size: int = 50,
        limit: int = 10,
        window: str = "auto",
        short_term_weeks: int = DEFAULT_SHORT_TERM_WEEKS,
        season_end_week: int | None = None,
        sleeper: bool = True,
    ) -> dict[str, Any]:
        """Rank free-agent claims with the same ST/LT roster + lineup grade as trades."""
        team = self.get_team(team_id)
        context = context_from_league(
            self.league,
            team,
            short_term_weeks=short_term_weeks,
            season_end_week=season_end_week,
        )
        roster_payload = team_to_dict(team, include_roster=True)
        roster = roster_payload.get("roster") or []
        mixed_fa = self.free_agents(size=max(int(size), REPLACEMENT_FA_POOL))
        target_fa = self.free_agents(position=position, size=size) if position else mixed_fa
        roster = self._enrich_players(
            roster,
            current_week=context.current_week,
            season_end_week=context.season_end_week,
            sleeper=sleeper,
        )
        mixed_fa = self._enrich_players(
            mixed_fa,
            current_week=context.current_week,
            season_end_week=context.season_end_week,
            sleeper=sleeper,
        )
        if position:
            target_fa = self._enrich_players(
                target_fa,
                current_week=context.current_week,
                season_end_week=context.season_end_week,
                sleeper=sleeper,
            )
        else:
            target_fa = mixed_fa
        baselines = replacement_baselines(mixed_fa)
        ranked = rank_waiver_claims(
            roster,
            target_fa,
            context=context,
            baselines=baselines,
            window=window,
            limit=limit,
        )
        return {
            "team": {"id": roster_payload.get("id"), "name": roster_payload.get("name")},
            "window": ranked["window"],
            "weights": ranked["weights"],
            "sources": self._ros_source_flags(sleeper=sleeper),
            "context": {
                "current_week": context.current_week,
                "short_term_weeks": context.short_term_week_list,
                "remaining_weeks": context.remaining_weeks,
                "season_end_week": context.season_end_week,
                "standing": context.standing,
                "record": [context.wins, context.losses, context.ties],
            },
            "replacement_weekly": baselines,
            "ir_stash_alerts": ranked.get("ir_stash_alerts", []),
            "targets": ranked["targets"],
        }

    def trade_grade(
        self,
        send_ids: list[int],
        receive_ids: list[int],
        *,
        team_id: int | None = None,
        window: str = "auto",
        short_term_weeks: int = DEFAULT_SHORT_TERM_WEEKS,
        season_end_week: int | None = None,
        fa_size: int = REPLACEMENT_FA_POOL,
        sleeper: bool = True,
    ) -> dict[str, Any]:
        team = self.get_team(team_id)
        context = context_from_league(
            self.league,
            team,
            short_term_weeks=short_term_weeks,
            season_end_week=season_end_week,
        )
        free_agents = self.free_agents(size=fa_size)
        players = self.player_index(free_agents, fa_size=0)
        enriched = self._enrich_players(
            list(players.values()),
            current_week=context.current_week,
            season_end_week=context.season_end_week,
            sleeper=sleeper,
        )
        players = {player["id"]: player for player in enriched if player.get("id") is not None}
        baselines = replacement_baselines(free_agents)
        our_roster = [
            players[player["id"]]
            for player in (team_to_dict(team, include_roster=True).get("roster") or [])
            if player.get("id") in players
        ]
        # Ensure roster rows carry current enrichment (ROS / Sleeper).
        for row in our_roster:
            row["team_id"] = team.team_id
        result = grade_trade(
            send_ids=send_ids,
            receive_ids=receive_ids,
            players=players,
            context=context,
            baselines=baselines,
            window=window,
            our_roster=our_roster,
        )
        result["team"] = team_to_dict(team, include_roster=False)
        result["sources"] = self._ros_source_flags(sleeper=sleeper)
        return result

    def opportunities(
        self,
        team_id: int | None = None,
        *,
        window: str = "auto",
        short_term_weeks: int = DEFAULT_SHORT_TERM_WEEKS,
        season_end_week: int | None = None,
        lookback: int = DEFAULT_LOOKBACK,
        limit: int = 8,
        fa_size: int = REPLACEMENT_FA_POOL,
        sleeper: bool = True,
    ) -> dict[str, Any]:
        team = self.get_team(team_id)
        context = context_from_league(
            self.league,
            team,
            short_term_weeks=short_term_weeks,
            season_end_week=season_end_week,
        )
        free_agents = self.free_agents(size=fa_size)
        rostered = self._enrich_players(
            list(self.rostered_players().values()),
            current_week=context.current_week,
            season_end_week=context.season_end_week,
            sleeper=sleeper,
        )
        baselines = replacement_baselines(free_agents)
        resolved = infer_window(context, window)
        valued_list = value_players(rostered, context, baselines, window=resolved)
        valued = {item.id: item for item in valued_list}
        found = find_opportunities(
            rostered,
            valued,
            our_team_id=team.team_id,
            current_week=context.current_week,
            lookback=lookback,
            limit=limit,
        )
        found["team"] = team_to_dict(team, include_roster=False)
        found["window"] = resolved
        found["lookback_weeks"] = lookback
        found["sources"] = self._ros_source_flags(sleeper=sleeper)
        return found

    def trade_search(
        self,
        team_id: int | None = None,
        *,
        window: str = "auto",
        short_term_weeks: int = DEFAULT_SHORT_TERM_WEEKS,
        season_end_week: int | None = None,
        kinds: str | None = None,
        limit: int = 40,
        min_surplus: float | None = None,
        max_surplus: float | None = None,
        with_team_id: int | None = None,
        fa_size: int = REPLACEMENT_FA_POOL,
        sleeper: bool = True,
    ) -> dict[str, Any]:
        team = self.get_team(team_id)
        context = context_from_league(
            self.league,
            team,
            short_term_weeks=short_term_weeks,
            season_end_week=season_end_week,
        )
        free_agents = self.free_agents(size=fa_size)
        teams = [team_to_dict(item, include_roster=True) for item in self.league.teams]
        rostered = []
        for payload in teams:
            for player in payload.get("roster") or []:
                player["team_id"] = payload.get("id")
                player["team_name"] = payload.get("name")
                rostered.append(player)
        rostered = self._enrich_players(
            rostered,
            current_week=context.current_week,
            season_end_week=context.season_end_week,
            sleeper=sleeper,
        )
        by_id = {player["id"]: player for player in rostered if player.get("id") is not None}
        for payload in teams:
            payload["roster"] = [
                by_id[player["id"]]
                for player in (payload.get("roster") or [])
                if player.get("id") in by_id
            ]
        baselines = replacement_baselines(free_agents)
        result = search_trades(
            our_team_id=team.team_id,
            teams=teams,
            players=by_id,
            context=context,
            baselines=baselines,
            window=window,
            kinds=parse_kinds(kinds),
            limit=limit,
            min_surplus=min_surplus,
            max_surplus=max_surplus,
            with_team_id=with_team_id,
        )
        result["team"] = team_to_dict(team, include_roster=False)
        result["sources"] = self._ros_source_flags(sleeper=sleeper)
        return result

    def activity(self, size: int = 25, msg_type: str | None = None) -> list[dict[str, Any]]:
        items = self.league.recent_activity(size=size, msg_type=msg_type)
        rows = []
        for item in items:
            rows.append(
                {
                    "date": str(getattr(item, "date", None)),
                    "actions": [str(action) for action in getattr(item, "actions", []) or []],
                    "raw": str(item),
                }
            )
        return rows

    def submit_transaction(
        self,
        payload: dict[str, Any],
        *,
        confirm: bool,
        scoring_period_id: int | None = None,
    ) -> WriteResult:
        week = scoring_period_id or getattr(self.league, "current_week", None) or 1
        return post_transaction(
            settings=self.settings,
            payload=payload,
            confirm=confirm,
            scoring_period_id=week,
        )
