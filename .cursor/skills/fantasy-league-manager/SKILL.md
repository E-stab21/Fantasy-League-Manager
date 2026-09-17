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

## Waivers (redraft)

```bash
python3 -m league_manager waiver-advice
python3 -m league_manager claim --player ID --drop ID
```

`waiver-advice` grades each free agent as a 1:1 add/drop using the same ST/LT roster VORP and lineup surplus as `trade-grade`. It pairs the add with the bench drop that maximizes blended surplus. `claim` and `add` attach that grade on preview. `--window` can be `auto`, `contender`, `bubble`, or `rebuilder`.

## Trades (redraft)

```bash
python3 -m league_manager values --window auto
python3 -m league_manager trade-search
python3 -m league_manager trade-grade --send 111,222 --receive 333
```

`--window` can be `auto`, `contender`, `bubble`, or `rebuilder`. Auto uses record and standings. ST is the next `--horizon` weeks (default 3). LT prefers FantasyPros ROS, Sleeper remaining weeks (default), or ESPN season remainder over flattening this week. `trade-search` walks 1:1 / 2:1 / 1:2 / 2:2 against other rosters and keeps packages inside a realism band (default blended ~1–40; `trade-calibrate --apply` can raise the top). Grades mix **roster VORP** (after minus before, including a forced drop on a full roster when you net extra players) and **lineup surplus** (optimal start/sit before vs after). Accepted public trades set the band only — they do **not** change VORP pricing. Grade before proposing; `league trade` attaches the same grade on preview.

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

Do not train a model unless asked. Start/sit averages ESPN + Sleeper this-week projections. Trade and waiver ROS defaults to Sleeper remaining weeks (FantasyPros first if `FANTASYPROS_API_KEY` is set), then ESPN season projection minus points scored. Research notes live in `docs/PREDICTION_MODELS.md`.
