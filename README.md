# Fantasy League Manager

Repository: [github.com/E-stab21/Fantasy-League-Manager](https://github.com/E-stab21/Fantasy-League-Manager)

Cloud Agents spawned from this repo act as an ESPN fantasy league manager. They use the unofficial ESPN fantasy API with your session cookies, then recommend lineups and waivers from public projections.

The original Chrome draft helper still lives in `chrome_extension/`. Weekly league management is now the `league` CLI.

## What a spawned agent does

1. Reads `AGENTS.md` and the fantasy-league-manager skill.
2. Loads `ESPN_S2`, `ESPN_SWID`, and `ESPN_LEAGUE_ID` from environment secrets.
3. Inspects the league, roster, matchup, and free agents.
4. Gives start/sit, waiver, redraft ST/LT values, trade grades, trade search, and buy-low / sell-high opportunities.
5. Previews writes (lineup, add/drop, waiver, trade). Live submits stay gated.

## Setup

```bash
python3 -m pip install -e .
cp .env.example .env   # local only; Cloud Agents use environment secrets
```

See [docs/CREDENTIALS.md](docs/CREDENTIALS.md) for how to copy ESPN cookies.

```bash
python3 -m league_manager auth-status
python3 -m league_manager ping
python3 -m league_manager status
python3 -m league_manager roster
python3 -m league_manager lineup-advice
python3 -m league_manager waiver-advice
python3 -m league_manager values
python3 -m league_manager trade-grade --send 111 --receive 222
python3 -m league_manager trade-search
python3 -m league_manager opportunities
```

Writes default to preview:

```bash
python3 -m league_manager set-lineup --move 3139477:BE:RB
```

Live posts require `ESPN_WRITES_ENABLED=true`, `ESPN_DRY_RUN=false`, and `--confirm`.

## Predictions

Do **not** train a custom model first. Start/sit averages ESPN league-scoring-adjusted weekly projections with Sleeper this-week. Trade ROS defaults to Sleeper remaining weeks (FantasyPros first if `FANTASYPROS_API_KEY` is set), then ESPN season projection minus points scored — not this week flattened across the calendar. Details in [docs/PREDICTION_MODELS.md](docs/PREDICTION_MODELS.md).

## Tests

```bash
python3 -m pip install -e '.[dev]'
pytest
```
