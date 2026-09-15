#!/usr/bin/env python3
"""Thin wrapper — prefer `python3 -m league_manager trade-calibrate`."""

from __future__ import annotations

import argparse

from league_manager.trade_market import LeagueProfile, calibrate_trade_market


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teams", type=int, default=10)
    parser.add_argument("--scoring", default="ppr", choices=("ppr", "half", "std"))
    parser.add_argument("--superflex", action="store_true")
    parser.add_argument("--weeks", default="1")
    parser.add_argument("--seasons", default="")
    parser.add_argument("--max-leagues", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--players", default="", help="Comma roster names for insights")
    args = parser.parse_args()
    weeks = [int(w) for w in args.weeks.split(",") if w.strip()]
    seasons = [s.strip() for s in args.seasons.split(",") if s.strip()] or None
    our_names = [n.strip() for n in args.players.split(",") if n.strip()]
    report = calibrate_trade_market(
        reference=LeagueProfile(
            team_count=args.teams,
            scoring=args.scoring,
            superflex=args.superflex,
        ),
        our_names=our_names,
        seasons=seasons,
        weeks=weeks,
        max_leagues=args.max_leagues,
        apply=args.apply,
    )
    import json

    print(json.dumps({k: report[k] for k in report if k not in {"examples_near_even", "examples_winning"}}, indent=2))
    print(json.dumps({"examples_winning": report.get("examples_winning", [])[:5]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
