---
name: fantasy-league-manager
description: Manage the user's ESPN fantasy league with the league CLI, credentials from environment secrets, and public projection sources. Use whenever the user asks about their roster, lineup, waivers, trades, standings, or player decisions.
---

# Fantasy league manager

## Credentials

Required environment secrets:

- `ESPN_S2`
- `ESPN_SWID` (keep curly braces)
- `ESPN_LEAGUE_ID`

Optional: `ESPN_TEAM_ID`, `ESPN_SEASON`, `ESPN_SPORT`.

```bash
python3 -m league_manager auth-status
python3 -m league_manager ping
```

If auth fails with 401, the ESPN cookies expired. Ask the user to refresh secrets using `docs/CREDENTIALS.md`.

## Weekly loop

```bash
python3 -m league_manager status
python3 -m league_manager roster
python3 -m league_manager matchup
python3 -m league_manager lineup-advice
python3 -m league_manager waiver-advice
python3 -m league_manager values
```

Sleeper is on by default for start/sit (averaged with ESPN) and for trade/waiver ROS. Use `--no-sleeper` to force ESPN-only.

`lineup-advice` fills the most restrictive slots first, so a FLEX never takes a player a WR/RB slot needed. Availability comes from this week's projection rather than the `injured` flag, which goes stale — a flagged player the sources still project will start, and one they leave out of this week will not.

## Waivers (redraft)

```bash
python3 -m league_manager waiver-advice
python3 -m league_manager claim --player ID --drop ID
```

`waiver-advice` grades each free agent as a 1:1 add/drop using the same ST/LT roster VORP and lineup surplus as `trade-grade`. It pairs the add with the bench drop that maximizes blended surplus. `claim` and `add` attach that grade on preview. `--window` can be `auto`, `contender`, `bubble`, or `rebuilder`.

`waiver-advice` output also includes `ir_stash_alerts`: bench players who are OUT/IR-status and not yet parked on IR. Check this before recommending any drop — a `BE` player who is IR-eligible should go to IR (`set-lineup --move ID:BE:IR`) to free the spot for free, not get cut. If a suggested drop target has `drop_ir_eligible: true`, move that player to IR first, then `add`/`claim` into the opened spot instead of a straight add/drop.

## Trades (redraft)

```bash
python3 -m league_manager values --window auto
python3 -m league_manager trade-search
python3 -m league_manager trade-grade --send 111,222 --receive 333
```

Two bench spots are reserved for long-term stashes (mostly injury) and two for streaming, each scored on its own week curve — `values` reports every player's `role` and `role_value`, plus `reserved_slots`. When a trade forces a drop, grading one trade tries every legal cut and keeps whichever leaves the most total value; bulk `trade-search` uses a faster proxy that measures each player against the *weakest* arrival (the one actually causing the squeeze) in their own role, so a search result can name a different drop than that trade's own grade. Either way the last player at a position is protected unless the trade brings that position in, the dropped player is priced as if on the send side, and the lineup delta runs on the post-drop roster.

Values are **points per weighted game above replacement**, so a trade blend of `+10` is large and `+1` is marginal. Each remaining week carries a weight that sums to 1, set by playoff leverage: a losing team weights the games in front of it, a contender weights weeks 15–17 (`values` returns the whole distribution under `week_weights`). `st_vorp` and `lt_vorp` split that single sum and add back to `blended`.

`--window` can be `auto`, `contender`, `bubble`, or `rebuilder`. Auto uses record and standings. It no longer weights time — only how much asset depth counts against the starting lineup. `--horizon` sets how many weeks the ST split covers (default 3). `trade-search` walks 1:1 / 2:1 / 1:2 / 2:2 against other rosters and keeps packages inside a realism band (default blended ~1–12; `trade-calibrate --apply` can raise the top). Grades mix **roster VORP** (after minus before, including a forced drop on a full roster when you net extra players) and **lineup surplus** — the starting lineup re-optimized for each remaining week and then weighted, since lineups are reset weekly, so a player available for only part of the stretch counts only in the weeks he plays. Accepted public trades set the band only — they do **not** change VORP pricing. Grade before proposing; `league trade` attaches the same grade on preview.

```bash
python3 -m league_manager trade-calibrate
python3 -m league_manager trade-calibrate --apply
```

Comparable redraft Sleeper accepts (skips dynasty / superflex-mismatch / best-ball). Reports whether to shift the top band and roster demand/supply comps for talk tracks.

```bash
python3 -m league_manager opportunities
```

Buy-lows are on other rosters (cold stretch or injury, ROS VORP still positive). Sell-highs are on our roster (heater vs weekly projection). Pairings are suggested; always `trade-grade` before offering. Recency is the *market* signal, not a second projection.

## Writes

Preview first. Example:

```bash
python3 -m league_manager set-lineup --move 3139477:BE:RB
python3 -m league_manager add --player 123 --drop 456
python3 -m league_manager claim --player 123 --drop 456 --bid 5
```

Submit only when the user says to execute, then add `--confirm`. Live posts also need `ESPN_WRITES_ENABLED=true` and `ESPN_DRY_RUN=false`.

## Projections

Do not train a model unless asked. Start/sit averages ESPN + Sleeper this-week projections. Trade and waiver values weight each remaining week separately from Sleeper's per-week curve (FantasyPros first if `FANTASYPROS_API_KEY` is set), falling back to ESPN season projection minus points scored. Research notes live in `docs/PREDICTION_MODELS.md`.

Injury is already in those projections — Sleeper marks down an iffy week and omits the weeks a player is expected to miss — so do not discount again by the ESPN `injury_status` tag, which goes stale (players tagged `INJURY_RESERVE` are often still projected at full strength). A `ros_source` of `weekly_none` means the curve covers them and projects no remaining week: out for the foreseeable future, graded 0. An omitted week is worth 0, not a penalty, because a bench starter covers the slot.
