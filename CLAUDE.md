# Claude Code Rules for Fantasy League Manager

This document provides guidance for Claude Code agents working on the Fantasy League Manager project.

## Core Instructions

You are this user's ESPN fantasy league manager.

- Use the `league` CLI for reads, advice, and writes. Do not hardcode credentials.
- Start with `league auth-status`. If secrets are missing, point at `docs/CREDENTIALS.md` instead of asking the user to paste cookies in chat.
- Writes stay in preview unless the user clearly asks to submit and write gates are enabled.

## Trade & Waiver Grading

- Start/sit averages ESPN + Sleeper this-week projections
- Waivers use the same roster + lineup surplus as trades (`waiver-advice`, `claim`, `add`)
- For trades, run `python3 -m league_manager trade-calibrate` (band only), then `trade-search` and `trade-grade`
- **Full-roster net-adds price the forced drop's VORP (after minus before)**
- Accepts never feed VORP
- Do not scrape FantasyPros

See `docs/PREDICTION_MODELS.md` for details on projection models.

## Week weighting (replaces the ST/LT split)

Player value is a weighted sum of per-week VORP over the remaining weeks:

```
value = sum_k  w_k * (projection_k * availability_k - replacement)
```

- `w_k` comes from `leverage.week_weights` and **sums to 1**, so every value is
  **points per weighted game above replacement**. Expect roughly ±16 for a trade
  blend and ±10 for a player, not the hundreds the old math produced.
- Weights are set by playoff leverage: regular-season weeks are weighted **flat**
  (no schedule is available, so remaining games are exchangeable and nothing
  distinguishes them), and playoff weeks by `P(still alive) x playoff_stakes`.
  A losing team therefore weights the games in front of it; a contender weights
  weeks 15-17. This is the inverse of the old record-keyed window weighting.
- Schemes: `leverage` (default), `decay`, `uniform`, `manual`. An eliminated team
  with no weeks left that matter falls back to flat rather than zeroing everything.
- `st_vorp` and `lt_vorp` are a **non-overlapping split** of that one weighted sum
  (short-term window / everything after), so `st_vorp + lt_vorp == blended`.
  They used to overlap, double-counting the near weeks and inflating every value.
- **Injury is already priced into the published projections — do not discount it
  again.** Sleeper publishes a full-season per-week curve
  (`sleeper.remaining_week_series`) and signals an injury two ways: it marks down
  the week a questionable player is iffy for, and it **omits the weeks it expects
  them to miss entirely**. Both are better than the ESPN `injury_status` tag,
  which goes stale — players tagged `INJURY_RESERVE` are routinely still
  projected at full strength every week.
- So for a player the curve covers: present weeks are taken as published, and an
  absent week is a week off. A week off scores **no surplus, not a penalty** — a
  bench starter covers the slot, so the week is worth 0, not `-replacement`.
  Covered with nothing published at all (`ros_source == "weekly_none"`) means out
  for the foreseeable future and grades 0. The roster spot such a player occupies
  is priced by the forced-drop logic, not here.
- `availability` / `INJURY_RAMP` is a **fallback only**, for a player with no
  per-week curve (notably D/ST), where the status tag is the only signal there is.
- ESPN's `weekly_stats` only ever holds the current and past weeks, so **its gaps
  must never be read as weeks off.** Only `weekly_projections` (Sleeper) carries
  meaningful absences. Conflating the two reads every D/ST as missing the season.
- `window` (contender / bubble / rebuilder) no longer weights time. It only sets
  `STRUCTURE_WEIGHTS` — how much asset depth matters against the starting lineup.

Band and grade thresholds in `trade_market.py` and `trades.letter_grade` are
calibrated to this scale. **If you change the weighting, re-measure the blended
distribution before touching those cutoffs.**

Known gap: Sleeper keys defenses by team abbreviation, not the numeric id the
matcher uses, so D/ST gets no per-week curve and falls back to a flat rate.

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
