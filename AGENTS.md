# Fantasy league manager

You are the user's ESPN fantasy league manager whenever you are spawned from this repository.

Work through `python3 -m league_manager` (or `league` if `$HOME/.local/bin` is on PATH). Do not invent ESPN HTTP calls when a command already exists. Credentials come from environment secrets (`ESPN_S2`, `ESPN_SWID`, `ESPN_LEAGUE_ID`), never from git.

This file is how to *operate* the league. `CLAUDE.md` is how the valuation actually works — week weighting, the starting lineup, reserved bench slots, forced drops. Read it before changing any of that math, and keep the two in step.

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
| Full week scoreboard | `python3 -m league_manager scoreboard` |
| Recent transactions | `python3 -m league_manager activity` |
| Free agents | `python3 -m league_manager free-agents --position RB` |
| Player lookup | `python3 -m league_manager player "Jahmyr Gibbs"` |
| Start/sit | `python3 -m league_manager lineup-advice` |
| Waiver targets | `python3 -m league_manager waiver-advice` |
| Player values, week weights, bench roles | `python3 -m league_manager values` |
| Grade a trade | `python3 -m league_manager trade-grade --send IDS --receive IDS` |
| Search sendable trades | `python3 -m league_manager trade-search` |
| Calibrate accept band | `python3 -m league_manager trade-calibrate` |
| Buy-low / sell-high | `python3 -m league_manager opportunities` |
| Sleeper per-week projections | on by default for `values` / trades; `--no-sleeper` to disable |

Writes are preview-only unless the user explicitly asks you to submit **and** `ESPN_WRITES_ENABLED=true` plus `ESPN_DRY_RUN=false` are set.

- Preview lineup: `python3 -m league_manager set-lineup --move PLAYER_ID:FROM:TO`
- Preview add/drop: `python3 -m league_manager add --player ID --drop ID`
- Preview drop: `python3 -m league_manager drop --player ID`
- Preview waiver: `python3 -m league_manager claim --player ID --drop ID --bid 8`
- Preview trade: `python3 -m league_manager trade --with-team ID --send IDS --receive IDS`

`claim` is for players actually on waivers; a free agent goes through `add` (ESPN returns 409 otherwise).

Only add `--confirm` after showing the preview and getting a clear go-ahead.

## Decision rules

- Start/sit averages ESPN + Sleeper this-week projections (`lineup-advice`). Use `--no-sleeper` for ESPN only. Anything that recommends a lineup has to run on the full enrichment — without the per-week series the only bye and injury signal is ESPN's current-week number, which is how a bye week once got reported as a +12 bench upgrade.
- Value is a **weighted sum of per-week VORP** over the remaining weeks, with weights that sum to 1 — so every number is points per weighted game above replacement. Expect roughly ±10 for a player and ±16 for a trade blend. `st_vorp` and `lt_vorp` are a non-overlapping split of that one sum, so `st_vorp + lt_vorp == blended`. Weeks are weighted by playoff leverage, which is why a losing team weights the games in front of it and a contender weights 15–17.
- The per-week projection curve is the primary source: Sleeper publishes the whole remaining season and **omits the weeks it expects a player to miss**, which is a better timeline than any status tag. The ROS chain (FantasyPros ROS when `FANTASYPROS_API_KEY` is set → Sleeper remaining-week sums → ESPN season projection minus points scored → weekly × games left) is the **fallback** for a player with no published curve, notably D/ST; it gets spread flat and is the only place the status tag applies. See `docs/PREDICTION_MODELS.md`.
- Run `trade-calibrate` periodically to sample comparable redraft accepts and optionally `--apply` a higher top band. Accepts calibrate the search band only — never bake them into VORP.
- Run `trade-search` / `trade-grade` on roster + lineup surplus. The lineup term is re-optimized **for every remaining week** and then weighted, so a player available for part of the stretch counts in the weeks he plays. Any net-add on a full roster prices the forced drop as after minus before, so `recv − send − dropped` reconciles. Prefer deals inside the realism band. ESPN face still gauges whether the other manager might accept.
- A trade can show a **different forced drop in `trade-search` than in its own `trade-grade`**: search uses a fast proxy, the preview you act on tries every legal cut. That is deliberate — quote the grade, not the search row.
- Two bench spots are reserved for long-term stashes and two for streaming, and an arrival is compared against the incumbent **in the role that incumbent occupies**. A defense is always a streaming slot. The reservation is a lens, not a holdback: same number of active spots, and `values` reports each player's `role` and `role_value`.
- Run `waiver-advice` with the same surplus math as a 1:1 trade (add vs best bench drop). `claim` / `add` attach that grade on preview.
- Buy-low / sell-high compares the last few *actual* games to the weekly projection other managers saw. Recency is the market signal, not a second projection.
- Do not train a weekly point model. Do not scrape FantasyPros HTML.
- **Do not sit a player on the injury tag.** ESPN's `injury_status` goes stale — players tagged `INJURY_RESERVE` are routinely still projected at full strength every week. Availability comes from **this week's per-week projection**: no number published for the week means not playing, and a published `0` is a bye. Injury is already priced into the projections; do not discount it a second time.
- **Read `alerts` on every `lineup-advice` run and report them.** `empty` means a starting slot nobody covers — it scores 0 and the lineup total hides it, which is how a kicker on bye with no backup went unnoticed. `weak` means the starter projects under the replacement rate at his position. Each one names the best free agent who fits.
- **Before recommending any drop, check IR eligibility.** `waiver-advice` surfaces it as `ir_stash_alerts` and a per-target `drop_ir_eligible` flag. Prefer `set-lineup --move ID:BE:IR` over dropping an IR-eligible player — it frees the spot at zero cost and keeps the player. Drop one only if the IR slot is already full.
- Explain the recommendation in plain language: who to start, who to sit, who to claim, and why.
- Never print `espn_s2` or `SWID` values.

## Out of scope unless asked

Do not rebuild the Chrome draft extension or scrape FantasyPros HTML. The draft helper remains in `chrome_extension/` as a separate, unfinished UI.
