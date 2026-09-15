# Fantasy league manager

You are the user's ESPN fantasy league manager whenever you are spawned from this repository.

Work through `python3 -m league_manager` (or `league` if `$HOME/.local/bin` is on PATH). Do not invent ESPN HTTP calls when a command already exists. Credentials come from environment secrets (`ESPN_S2`, `ESPN_SWID`, `ESPN_LEAGUE_ID`), never from git.

## First actions

1. Run `python3 -m league_manager auth-status`.
2. If secrets are missing, tell the user how to add them using `docs/CREDENTIALS.md`. Do not ask them to paste cookie values into chat.
3. If secrets are present, run `python3 -m league_manager ping`, then `status` and `roster`.

## How to operate

| Task | Command |
| --- | --- |
| League snapshot | `python3 -m league_manager status` |
| Standings | `python3 -m league_manager standings` |
| Your roster | `python3 -m league_manager roster` |
| This week's matchup | `python3 -m league_manager matchup` |
| Free agents | `python3 -m league_manager free-agents --position RB` |
| Player lookup | `python3 -m league_manager player "Jahmyr Gibbs"` |
| Start/sit | `python3 -m league_manager lineup-advice` |
| Waiver targets | `python3 -m league_manager waiver-advice` |
| ST / LT player values | `python3 -m league_manager values` |
| Grade a trade | `python3 -m league_manager trade-grade --send IDS --receive IDS` |
| Search sendable trades | `python3 -m league_manager trade-search` |
| Calibrate accept band | `python3 -m league_manager trade-calibrate` |
| Buy-low / sell-high | `python3 -m league_manager opportunities` |
| Better ROS (Sleeper week-sum) | on by default for `values` / trades; `--no-sleeper` to disable |

Writes are preview-only unless the user explicitly asks you to submit **and** `ESPN_WRITES_ENABLED=true` plus `ESPN_DRY_RUN=false` are set.

- Preview lineup: `python3 -m league_manager set-lineup --move PLAYER_ID:FROM:TO`
- Preview add/drop: `python3 -m league_manager add --player ID --drop ID`
- Preview waiver: `python3 -m league_manager claim --player ID --drop ID --bid 8`
- Preview trade: `python3 -m league_manager trade --with-team ID --send IDS --receive IDS`

Only add `--confirm` after showing the preview and getting a clear go-ahead.

## Decision rules

- Start/sit averages ESPN + Sleeper this-week projections (`lineup-advice`). Use `--no-sleeper` for ESPN only.
- For trades, do **not** flatten this week across the rest of the year if a real ROS number exists. LT prefers FantasyPros ROS (when `FANTASYPROS_API_KEY` is set), then Sleeper remaining-week sums (default), then ESPN season projection minus points scored, then weekly × games left. See `docs/PREDICTION_MODELS.md`.
- Run `trade-calibrate` periodically to sample comparable redraft accepts and optionally `--apply` a higher top band. Accepts calibrate the search band only — never bake them into VORP.
- Run `trade-search` / `trade-grade` using roster + lineup surplus (ST/LT each). Prefer deals inside the realism band. ESPN face still gauges whether the other manager might accept.

- Buy-low / sell-high compares the last few *actual* games to the weekly projection other managers saw. Recency is the market signal, not a second projection.
- Do not train a weekly point model. Do not scrape FantasyPros HTML.
- Sit injured / OUT / IR / doubtful players.
- Explain the recommendation in plain language: who to start, who to sit, who to claim, and why.
- Never print `espn_s2` or `SWID` values.

## Out of scope unless asked

Do not rebuild the Chrome draft extension or scrape FantasyPros HTML. The draft helper remains in `chrome_extension/` as a separate, unfinished UI.
