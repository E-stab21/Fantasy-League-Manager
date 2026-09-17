# Player data and prediction models

Question: should this repo train its own fantasy prediction models, or use public ones?

**Use public projections first. Do not build a custom model unless this league's scoring or decision process outgrows them.**

## What we already get for free

### ESPN (primary)

The unofficial fantasy API returns **league-scoring-adjusted** projected points on rosters and free agents. That is the most important number for start/sit and waivers because PPR vs standard, bonus yards, TE premium, and custom scoring are already applied.

`league lineup-advice` averages ESPN and Sleeper this-week projections when both are available (`--no-sleeper` for ESPN only).

`league waiver-advice` and `league claim` / `league add` grade add/drops with the same redraft surplus as trades: ST vs LT and roster VORP vs lineup surplus.

### Sleeper (secondary, no auth)

Public endpoints:

- `https://api.sleeper.app/v1/state/nfl` — current season/week
- `https://api.sleeper.app/v1/players/nfl` — player directory (cache daily)
- `https://api.sleeper.app/v1/players/nfl/trending/add` — add/drop heat
- `https://api.sleeper.app/projections/nfl/{season}/{week}` — weekly projections (`pts_ppr`, `pts_half_ppr`, `pts_std`)

Pass `--sleeper` / `--no-sleeper` to toggle the Sleeper overlay. Trade/ROS
commands default Sleeper **on**; start/sit and waivers also default it on and
average with ESPN. Stay under Sleeper's 1000 requests/minute guidance.

### nflverse / nflreadpy (historical context)

Open, analysis-grade NFL data (CC-BY 4.0 for most files):

- Weekly and seasonal player stats
- Snap counts, depth charts, injuries, schedules
- Next Gen Stats
- FantasyPros rankings redistributed as `load_ff_rankings()`
- Expected yards / fantasy points as `load_ff_opportunity()`

Install with `pip install 'league-manager[research]'` when an agent needs historical research. Not required for weekly lineup management.

### FantasyPros

Official paid REST API (`https://api.fantasypros.com/public/v2/json`) for consensus rankings and projections across 130+ experts. There is no supported free API. This repo does **not** scrape FantasyPros HTML.

`FANTASYPROS_API_KEY` is used when present. The official REST API (`https://api.fantasypros.com/public/v2/json`, `x-api-key` header) is the first ROS source for trade value. This repo does not scrape FantasyPros HTML. Without a key, valuation falls through to Sleeper remaining weeks (default) and ESPN season remainder.

### Paid sports-data APIs

SportsDataIO, FantasyData, 4for4, and similar sell projections and injuries. Useful only if you want vendor SLAs. Not needed to manage one league.

## Custom models: when they help, when they do not

Open-source weekly fantasy models (XGBoost / LightGBM / small nets on nflverse features) routinely land **close to, not clearly better than**, industry consensus. Published hobby benchmarks often trail a paid/consensus projection by a few percent of MAE, and beat only naive baselines (last week / season average).

A custom model is worth building only if at least one of these is true:

- The league uses unusual scoring that ESPN projections handle poorly
- You want season-long simulation, draft capital, or dynasty values ESPN does not expose
- You will maintain weekly feature pipelines (Vegas lines, injuries, depth-chart changes)

Otherwise a custom model is extra training cost, weekly breakage, and worse injury-news reaction than ESPN/Sleeper/FantasyPros already bake in.

## What this repo does

1. **Start/sit averages ESPN + Sleeper this-week projections** when both exist. `lineup-advice` defaults this on (`--no-sleeper` for ESPN only).
2. **Sleeper ROS is on by default** for trades, season-long value, and waivers (`values`, `trade-grade`, `trade-search`, `opportunities`, `waiver-advice`, `claim`, `add`). Use `--no-sleeper` to fall back to ESPN remainder.
3. **Use simple optimizers**, not ML: greedy slot fill for lineups; the same ST/LT roster + lineup surplus as trades for waiver add/drops.
4. **Redraft trade value** (`league values`, `league trade-grade`): short-term and rest-of-season surplus on **two** axes — **roster** (after-trade waiver-replacement VORP minus before) and **lineup** (optimal starting-XI points before vs after the trade). On a full roster, getting more players than you send forces a drop of the lowest remaining VORP (incoming players are kept); that cut is priced as if they were on the send side. An empty leftover spot after sending extra is replacement-level (~0). Horizon weights (contender / bubble / rebuilder) mix ST vs LT; structure weights mix roster vs lineup (contenders lean lineup). Stud tax / hole hacks are gone — selling a starter for bench parts shows up as negative lineup surplus. Outside accepts (`trade-calibrate`) use a typical starter-floor VORP when the other roster is unknown. Their acceptance distance still uses ESPN face. LT ROS order: FantasyPros → Sleeper remaining weeks → ESPN remainder → weekly × games.
5. **Waiver claims** (`league waiver-advice`, `league claim`, `league add`): treat each add/drop as a 1:1 trade. Rank free agents by blended surplus against the best bench drop (same ST/LT and roster/lineup mix). `claim` / `add` attach that grade on preview. Do not rank streamers by this-week points vs an empty positional bench.
6. **Trade search** (`league trade-search`): enumerate 1:1, 2:1, 1:2, and 2:2 packages against other rosters. Keep deals inside a band calibrated from comparable redraft Sleeper accepts (`league trade-calibrate`): blended surplus about 1–40 by default (worth sending, not absurd). `--apply` may raise the top edge when the market accepts fatter +EV. **Accepts do not change player VORP** — only the search band / talk tracks. Rank by fairness + slight edge, not max jackpot.
7. **Buy-low / sell-high** (`league opportunities`): last 1–3 actual games vs those weeks’ ESPN projections (the number other managers anchored on). Cold + still-positive ROS VORP on someone else’s roster is a buy-low. A heater on our roster is a sell-high. Injury with remaining ROS value is also a buy-low. Snap share and recent averages are **not** mixed into our forward value again; they are already inside the projection.

If a later agent is asked to build a weekly point model, start from nflverse weekly stats + ESPN scoring settings, and score it against ESPN/Sleeper holdout weeks before replacing the public numbers.
