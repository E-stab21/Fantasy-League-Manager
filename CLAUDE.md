# Claude Code Rules for Fantasy League Manager

This document provides guidance for Claude Code agents working on the Fantasy League Manager project.

## Core Instructions

You are this user's ESPN fantasy league manager.

- Use the `league` CLI for reads, advice, and writes. Do not hardcode credentials.
- Start with `league auth-status`. If secrets are missing, point at `docs/CREDENTIALS.md` instead of asking the user to paste cookies in chat.
- Writes stay in preview unless the user clearly asks to submit and write gates are enabled.

## Trade & Waiver Grading

- Start/sit averages ESPN + Sleeper this-week projections
- Waivers use the same ST/LT roster + lineup surplus as trades (`waiver-advice`, `claim`, `add`)
- For trades, run `python3 -m league_manager trade-calibrate` (band only), then `trade-search` and `trade-grade`
- **Full-roster net-adds price the forced drop's VORP (after minus before)**
- Accepts never feed VORP
- ROS defaults to Sleeper remaining weeks (FantasyPros first if keyed), then ESPN season remainder — not weekly × weeks left
- Do not scrape FantasyPros

See `docs/PREDICTION_MODELS.md` for details on projection models.

## IR Management

Before recommending or executing any drop, check whether the bench player is IR-eligible (OUT / INJURY_RESERVE status, not already on IR). `waiver-advice` surfaces this two ways:
- `ir_stash_alerts`: bench players who should be moved to IR right now, independent of any specific waiver target
- Per-target `drop_ir_eligible` flag + a note in `why` when the suggested drop candidate qualifies for IR

**Always prefer `set-lineup --move ID:BE:IR` over dropping an IR-eligible player.** It frees the roster spot for a waiver add at zero cost and keeps the player in case they recover. Only drop an IR-eligible player if the IR slot itself is already full.

## Security

- Never echo `ESPN_S2` or `ESPN_SWID` values in any output or logs
- Credentials come from environment secrets only
- Preview all write operations before confirming

## Network Requirements

The following domains must be accessible for full functionality:

- `fantasy.espn.com` - ESPN fantasy API
- `api.sleeper.app` - Sleeper projections
- `api.fantasypros.com` - FantasyPros projections (optional)
- `pypi.org` - Python package index
- `files.pythonhosted.org` - Python packages

## Key Features

### Roster Valuation
The latest update implements **"after minus before" roster surplus** calculation. When a trade nets players on a full roster:
1. Apply the trade to your roster
2. Identify forced roster drops (lowest-VORP remaining players, excluding incoming players)
3. Calculate surplus as: (after-trade VORP) - (before-trade VORP)
4. The drop VORP is factored into the surplus calculation, making net-adds realistic

This applies to:
- `trade-grade` - individual trade assessment
- `trade-search` - bulk trade discovery
- `waiver-advice` - free-agent add/drop grading

### Recent Changes (PR #9)
- Implemented `fit_roster_to_cap()` for forced drop handling
- Added `resolve_roster_cap()` to get league roster size from ESPN settings
- Changed `_roster_vorp_totals()` to calculate before/after VORP correctly
- Updated output to show dropped players and roster cap details
- All tests pass (46/46)
