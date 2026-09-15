"""Command-line interface used by Cloud Agents to manage an ESPN league."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from league_manager.advise import optimal_lineup, waiver_targets
from league_manager.config import ConfigError, auth_status, load_settings
from league_manager.espn_client import EspnClient
from league_manager.market import DEFAULT_LOOKBACK
from league_manager.projections import attach_sleeper_projections, league_scoring
from league_manager.slots import parse_slot
from league_manager.value import DEFAULT_SHORT_TERM_WEEKS
from league_manager.writes import add_drop_payload, lineup_payload, trade_payload


def _add_sleeper_flag(
    parser: argparse.ArgumentParser,
    *,
    help_text: str,
    default: bool = True,
) -> None:
    parser.add_argument(
        "--sleeper",
        action=argparse.BooleanOptionalAction,
        default=default,
        help=help_text,
    )


def _attach_sleeper_week(
    players: list[dict[str, Any]],
    client: EspnClient,
) -> list[dict[str, Any]]:
    try:
        return attach_sleeper_projections(
            players,
            season=client.settings.season,
            week=getattr(client.league, "current_week", None),
            scoring=league_scoring(client.league),
        )
    except Exception:
        return players


def _print(data: Any, fmt: str) -> int:
    if fmt == "text":
        if isinstance(data, (dict, list)):
            print(json.dumps(data, indent=2, default=str))
        else:
            print(data)
    else:
        print(json.dumps(data, indent=2, default=str))
    return 0


def _client() -> EspnClient:
    return EspnClient(load_settings())


def _parse_id_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def cmd_auth_status(_args: argparse.Namespace) -> int:
    return _print(auth_status(), "json")


def cmd_ping(_args: argparse.Namespace) -> int:
    return _print(_client().ping(), _args.format)


def cmd_status(args: argparse.Namespace) -> int:
    return _print(_client().status(), args.format)


def cmd_standings(args: argparse.Namespace) -> int:
    return _print(_client().standings(), args.format)


def cmd_roster(args: argparse.Namespace) -> int:
    return _print(_client().roster(args.team_id), args.format)


def cmd_matchup(args: argparse.Namespace) -> int:
    return _print(_client().matchup(week=args.week, team_id=args.team_id), args.format)


def cmd_scoreboard(args: argparse.Namespace) -> int:
    return _print(_client().scoreboard(week=args.week), args.format)


def cmd_free_agents(args: argparse.Namespace) -> int:
    return _print(
        _client().free_agents(position=args.position, size=args.size, week=args.week),
        args.format,
    )


def cmd_player(args: argparse.Namespace) -> int:
    return _print(_client().player(args.name), args.format)


def cmd_activity(args: argparse.Namespace) -> int:
    return _print(_client().activity(size=args.size, msg_type=args.type), args.format)


def cmd_lineup_advice(args: argparse.Namespace) -> int:
    client = _client()
    roster = client.roster(args.team_id)
    players = roster.get("roster") or []
    if args.sleeper:
        players = _attach_sleeper_week(players, client)
    advice = optimal_lineup(players)
    advice["team"] = {"id": roster.get("id"), "name": roster.get("name")}
    advice["sources"] = {
        "espn_week": True,
        "sleeper_week": bool(args.sleeper),
        "blend": "average" if args.sleeper else "espn",
    }
    return _print(advice, args.format)


def cmd_values(args: argparse.Namespace) -> int:
    return _print(
        _client().values(
            args.team_id,
            window=args.window,
            short_term_weeks=args.horizon,
            season_end_week=args.season_end,
            sleeper=args.sleeper,
        ),
        args.format,
    )


def cmd_opportunities(args: argparse.Namespace) -> int:
    return _print(
        _client().opportunities(
            args.team_id,
            window=args.window,
            short_term_weeks=args.horizon,
            season_end_week=args.season_end,
            lookback=args.lookback,
            limit=args.limit,
            sleeper=args.sleeper,
        ),
        args.format,
    )


def cmd_trade_grade(args: argparse.Namespace) -> int:
    return _print(
        _client().trade_grade(
            _parse_id_list(args.send),
            _parse_id_list(args.receive),
            team_id=args.team_id,
            window=args.window,
            short_term_weeks=args.horizon,
            season_end_week=args.season_end,
            sleeper=args.sleeper,
        ),
        args.format,
    )


def cmd_trade_calibrate(args: argparse.Namespace) -> int:
    from league_manager.projections import league_scoring
    from league_manager.trade_market import calibrate_trade_market, espn_reference_profile

    client = _client()
    scoring = league_scoring(client.league)
    reference = espn_reference_profile(client.league, scoring)
    roster = client.roster(args.team_id)
    our_names = [p.get("name") for p in (roster.get("roster") or []) if p.get("name")]
    weeks = [int(w) for w in args.weeks.split(",") if w.strip()]
    seasons = [s.strip() for s in args.seasons.split(",") if s.strip()] if args.seasons else None
    report = calibrate_trade_market(
        reference=reference,
        our_names=our_names,
        seasons=seasons,
        weeks=weeks,
        max_leagues=args.max_leagues,
        window=args.window,
        apply=args.apply,
        min_sample=args.min_sample,
    )
    return _print(report, args.format)


def cmd_trade_search(args: argparse.Namespace) -> int:
    return _print(
        _client().trade_search(
            args.team_id,
            window=args.window,
            short_term_weeks=args.horizon,
            season_end_week=args.season_end,
            kinds=args.kinds,
            limit=args.limit,
            min_surplus=args.min_surplus,
            max_surplus=args.max_surplus,
            with_team_id=args.with_team,
            sleeper=args.sleeper,
        ),
        args.format,
    )


def cmd_waiver_advice(args: argparse.Namespace) -> int:
    client = _client()
    roster = client.roster(args.team_id)
    free_agents = client.free_agents(position=args.position, size=args.size)
    players = roster.get("roster") or []
    if args.sleeper:
        players = _attach_sleeper_week(players, client)
        free_agents = _attach_sleeper_week(free_agents, client)
    return _print(
        {
            "team": {"id": roster.get("id"), "name": roster.get("name")},
            "sources": {
                "espn_week": True,
                "sleeper_week": bool(args.sleeper),
                "blend": "average" if args.sleeper else "espn",
            },
            "targets": waiver_targets(players, free_agents, limit=args.limit),
        },
        args.format,
    )


def cmd_set_lineup(args: argparse.Namespace) -> int:
    client = _client()
    team_id = client.resolve_team_id(args.team_id)
    week = args.week or getattr(client.league, "current_week", 1)
    moves = []
    for raw in args.move:
        player_id, from_slot, to_slot = raw.split(":")
        moves.append(
            {
                "player_id": int(player_id),
                "from_slot": parse_slot(from_slot),
                "to_slot": parse_slot(to_slot),
            }
        )
    payload = lineup_payload(
        team_id=team_id,
        swid=client.settings.swid,
        scoring_period_id=week,
        moves=moves,
    )
    result = client.submit_transaction(payload, confirm=args.confirm, scoring_period_id=week)
    return _print(result.to_dict(), args.format)


def cmd_add(args: argparse.Namespace) -> int:
    client = _client()
    team_id = client.resolve_team_id(args.team_id)
    week = args.week or getattr(client.league, "current_week", 1)
    payload = add_drop_payload(
        team_id=team_id,
        swid=client.settings.swid,
        scoring_period_id=week,
        add_player_id=args.player,
        drop_player_id=args.drop,
        waiver=False,
    )
    result = client.submit_transaction(payload, confirm=args.confirm, scoring_period_id=week)
    return _print(result.to_dict(), args.format)


def cmd_drop(args: argparse.Namespace) -> int:
    client = _client()
    team_id = client.resolve_team_id(args.team_id)
    week = args.week or getattr(client.league, "current_week", 1)
    payload = add_drop_payload(
        team_id=team_id,
        swid=client.settings.swid,
        scoring_period_id=week,
        drop_player_id=args.player,
        waiver=False,
    )
    result = client.submit_transaction(payload, confirm=args.confirm, scoring_period_id=week)
    return _print(result.to_dict(), args.format)


def cmd_claim(args: argparse.Namespace) -> int:
    client = _client()
    team_id = client.resolve_team_id(args.team_id)
    week = args.week or getattr(client.league, "current_week", 1)
    payload = add_drop_payload(
        team_id=team_id,
        swid=client.settings.swid,
        scoring_period_id=week,
        add_player_id=args.player,
        drop_player_id=args.drop,
        waiver=True,
        bid_amount=args.bid,
    )
    result = client.submit_transaction(payload, confirm=args.confirm, scoring_period_id=week)
    return _print(result.to_dict(), args.format)


def cmd_trade(args: argparse.Namespace) -> int:
    client = _client()
    team_id = client.resolve_team_id(args.team_id)
    week = args.week or getattr(client.league, "current_week", 1)
    payload = trade_payload(
        team_id=team_id,
        receiving_team_id=args.with_team,
        swid=client.settings.swid,
        scoring_period_id=week,
        send_player_ids=_parse_id_list(args.send),
        receive_player_ids=_parse_id_list(args.receive),
        comment=args.comment or "",
    )
    result = client.submit_transaction(payload, confirm=args.confirm, scoring_period_id=week)
    payload_out = result.to_dict()
    try:
        payload_out["grade"] = client.trade_grade(
            _parse_id_list(args.send),
            _parse_id_list(args.receive),
            team_id=team_id,
        )
    except Exception as exc:  # noqa: BLE001 - grade is additive on preview
        payload_out["grade_error"] = str(exc)
    return _print(payload_out, args.format)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="league",
        description="Manage an ESPN fantasy league with credentials from the environment.",
    )
    parser.add_argument("--format", choices=("json", "text"), default="json")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth-status", help="Show whether ESPN secrets are configured").set_defaults(
        func=cmd_auth_status
    )
    p = sub.add_parser("ping", help="Verify credentials against ESPN")
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("status", help="League settings and current week")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("standings", help="Current standings")
    p.set_defaults(func=cmd_standings)

    p = sub.add_parser("roster", help="Team roster")
    p.add_argument("--team-id", type=int)
    p.set_defaults(func=cmd_roster)

    p = sub.add_parser("matchup", help="Your matchup for a week")
    p.add_argument("--team-id", type=int)
    p.add_argument("--week", type=int)
    p.set_defaults(func=cmd_matchup)

    p = sub.add_parser("scoreboard", help="Full week scoreboard")
    p.add_argument("--week", type=int)
    p.set_defaults(func=cmd_scoreboard)

    p = sub.add_parser("free-agents", help="Available free agents")
    p.add_argument("--position")
    p.add_argument("--size", type=int, default=50)
    p.add_argument("--week", type=int)
    p.set_defaults(func=cmd_free_agents)

    p = sub.add_parser("player", help="Look up a player by name")
    p.add_argument("name")
    p.set_defaults(func=cmd_player)

    p = sub.add_parser("activity", help="Recent league transactions")
    p.add_argument("--size", type=int, default=25)
    p.add_argument("--type", choices=("FA", "WAIVER", "TRADED"))
    p.set_defaults(func=cmd_activity)

    p = sub.add_parser("lineup-advice", help="Recommend a starting lineup")
    p.add_argument("--team-id", type=int)
    _add_sleeper_flag(
        p,
        help_text="Average ESPN + Sleeper this-week projections (default on; --no-sleeper for ESPN only)",
    )
    p.set_defaults(func=cmd_lineup_advice)

    p = sub.add_parser("values", help="Redraft ST/LT VORP for a roster")
    p.add_argument("--team-id", type=int)
    p.add_argument(
        "--window",
        choices=("auto", "contender", "bubble", "rebuilder"),
        default="auto",
        help="How hard to weight the next few weeks vs rest of season",
    )
    p.add_argument("--horizon", type=int, default=DEFAULT_SHORT_TERM_WEEKS, help="Short-term weeks")
    p.add_argument("--season-end", type=int, dest="season_end", help="Last fantasy week (default 17)")
    _add_sleeper_flag(p, help_text="Use Sleeper remaining-week sums for ROS (default on)")
    p.set_defaults(func=cmd_values)

    p = sub.add_parser("trade-grade", help="Grade a redraft trade on ST and LT surplus")
    p.add_argument("--team-id", type=int)
    p.add_argument("--send", required=True, help="Comma-separated ESPN player ids you give up")
    p.add_argument("--receive", required=True, help="Comma-separated ESPN player ids you get")
    p.add_argument(
        "--window",
        choices=("auto", "contender", "bubble", "rebuilder"),
        default="auto",
    )
    p.add_argument("--horizon", type=int, default=DEFAULT_SHORT_TERM_WEEKS)
    p.add_argument("--season-end", type=int, dest="season_end")
    _add_sleeper_flag(p, help_text="Use Sleeper remaining-week sums for ROS (default on)")
    p.set_defaults(func=cmd_trade_grade)

    p = sub.add_parser(
        "trade-calibrate",
        help="Sample comparable redraft Sleeper accepts; shift search band; roster market insights",
    )
    p.add_argument("--team-id", type=int)
    p.add_argument(
        "--window",
        choices=("auto", "contender", "bubble", "rebuilder"),
        default="bubble",
    )
    p.add_argument("--weeks", default="1", help="Comma-separated Sleeper transaction weeks")
    p.add_argument("--seasons", default="", help="Default: current NFL season from Sleeper state")
    p.add_argument("--max-leagues", type=int, default=100)
    p.add_argument("--min-sample", type=int, default=8, help="Min winning sides before shifting band")
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write recommended band override for trade-search (does not change VORP pricing)",
    )
    p.set_defaults(func=cmd_trade_calibrate)

    p = sub.add_parser(
        "trade-search",
        help="Enumerate hundreds of 1:1 / 2:1 / 1:2 trades and keep +EV packages",
    )
    p.add_argument("--team-id", type=int)
    p.add_argument(
        "--window",
        choices=("auto", "contender", "bubble", "rebuilder"),
        default="auto",
    )
    p.add_argument("--horizon", type=int, default=DEFAULT_SHORT_TERM_WEEKS)
    p.add_argument("--season-end", type=int, dest="season_end")
    p.add_argument("--kinds", default="1:1,2:1,1:2,2:2", help="Comma-separated: 1:1,2:1,1:2,2:2")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument(
        "--min-surplus",
        type=float,
        default=None,
        dest="min_surplus",
        help="Minimum blended VORP edge for us (default: active band)",
    )
    p.add_argument(
        "--max-surplus",
        type=float,
        default=None,
        dest="max_surplus",
        help="Top of realism band (default: active band from trade-calibrate)",
    )
    p.add_argument("--with-team", type=int, dest="with_team", help="Only search this opponent")
    _add_sleeper_flag(p, help_text="Use Sleeper remaining-week sums for ROS (default on)")
    p.set_defaults(func=cmd_trade_search)

    p = sub.add_parser(
        "opportunities",
        help="Buy-low / sell-high spots where recency likely disagrees with our value",
    )
    p.add_argument("--team-id", type=int)
    p.add_argument(
        "--window",
        choices=("auto", "contender", "bubble", "rebuilder"),
        default="auto",
    )
    p.add_argument("--horizon", type=int, default=DEFAULT_SHORT_TERM_WEEKS)
    p.add_argument("--season-end", type=int, dest="season_end")
    p.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK, help="Recent games to use as the market anchor")
    p.add_argument("--limit", type=int, default=8)
    _add_sleeper_flag(p, help_text="Use Sleeper remaining-week sums for ROS (default on)")
    p.set_defaults(func=cmd_opportunities)

    p = sub.add_parser("waiver-advice", help="Rank free-agent adds vs your bench")
    p.add_argument("--team-id", type=int)
    p.add_argument("--position")
    p.add_argument("--size", type=int, default=50)
    p.add_argument("--limit", type=int, default=10)
    _add_sleeper_flag(
        p,
        help_text="Average ESPN + Sleeper this-week projections (default on; --no-sleeper for ESPN only)",
    )
    p.set_defaults(func=cmd_waiver_advice)

    p = sub.add_parser("set-lineup", help="Preview or submit lineup moves")
    p.add_argument("--team-id", type=int)
    p.add_argument("--week", type=int)
    p.add_argument(
        "--move",
        action="append",
        required=True,
        metavar="PLAYER_ID:FROM:TO",
        help="Example: 3139477:BE:RB or 3139477:20:2",
    )
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_set_lineup)

    p = sub.add_parser("add", help="Preview or add a free agent")
    p.add_argument("--team-id", type=int)
    p.add_argument("--week", type=int)
    p.add_argument("--player", type=int, required=True, help="ESPN player id to add")
    p.add_argument("--drop", type=int, help="ESPN player id to drop")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("drop", help="Preview or drop a player")
    p.add_argument("--team-id", type=int)
    p.add_argument("--week", type=int)
    p.add_argument("--player", type=int, required=True)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_drop)

    p = sub.add_parser("claim", help="Preview or submit a waiver claim")
    p.add_argument("--team-id", type=int)
    p.add_argument("--week", type=int)
    p.add_argument("--player", type=int, required=True)
    p.add_argument("--drop", type=int)
    p.add_argument("--bid", type=int, default=0)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("trade", help="Preview or propose a trade")
    p.add_argument("--team-id", type=int)
    p.add_argument("--week", type=int)
    p.add_argument("--with-team", type=int, required=True, dest="with_team")
    p.add_argument("--send", required=True, help="Comma-separated ESPN player ids")
    p.add_argument("--receive", required=True, help="Comma-separated ESPN player ids")
    p.add_argument("--comment", default="")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_trade)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
