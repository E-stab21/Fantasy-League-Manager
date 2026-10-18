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

**Every command that computes a replacement baseline pulls the same free-agent
pool** (`espn_client.REPLACEMENT_FA_POOL`, 150). Replacement is the 3rd-best free
agent at the position, and ESPN returns its free agents in its own order (roughly
ownership), not by projection — so a shallow pool can miss the best available
player entirely and set the bar too low. Measured: the QB baseline reads 11.52 at
25 agents, 14.87 at 50, 15.19 at 80, and settles at **16.35 from 150 on**, moving
the whole roster total from 33.84 to 21.30. Before this was shared, `values` and
`waiver-advice` ran at 50 while `trade-grade` / `add` / `claim` ran at 80, so the
same move graded +3.13 in one and +2.96 in the other. If you change the pool size,
re-measure — it shifts every value.

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

## Starting lineup

The lineup term is `(lineup value after the move) − (lineup value before)`,
both measured over the whole remaining season on the **base** distribution.
**Role curves never touch it** — it is a plain estimate of lineup strength, and
a player's stash or stream label changes nothing here. (For scale: on a live
trade the base distribution gives a before-value of 117.05 where the stream
distribution would give 114.73; the latter is not used anywhere.)

Lineups are reset weekly, so the lineup is **re-optimized for every remaining
week** and the weeks are then weighted (`trades.weekly_lineup_points`). A player
available for only part of the stretch counts in the weeks he plays and is
replaced in the rest, which one lineup held fixed across a horizon cannot
express. `trades.lineup_curves` builds the per-week projections once and a
`curve_cache` is threaded through `search_trades` and `rank_waiver_claims`, so
the weekly loop costs nothing next to re-valuing players per candidate trade.

**Slots must be filled most restrictive first** (`slots.slot_eligibility_size`).
The slot template comes from whatever order ESPN lists the roster in, and a FLEX
can land before a WR slot — filling in that raw order lets FLEX take the best WR
and strands points. Eligibility is nested (anyone who fits a `WR` slot also fits
FLEX), so restrictive-first is optimal here; it matched brute force on a live
roster where raw order lost up to 2.0 points.

**A published `0` is a bye, not missing data.** ESPN marks a bye (and some
inactive weeks) with a `0.0` projection. Dropping zeros as if they were gaps
sends the week to a fallback that substitutes the season average, which starts
players whose team is not playing. `_weeks_from` keeps zeros and both branches
map a published zero to `None`.

Anything that recommends a lineup must run on the **full enrichment**
(`EspnClient.lineup_advice`), not a this-week-only Sleeper attach. Without the
per-week series the only bye and injury signal is ESPN's current-week number.

`advise.optimal_lineup` (start/sit) takes availability from **this week's
per-week projection, not the `injured` flag** — the flag goes stale and flagged
players are routinely still projected to play. Both sides of the reported delta
must be scored the same way, or `current_projected` and `recommended_projected`
are not comparable.

Known gap: Sleeper keys defenses by team abbreviation, not the numeric id the
matcher uses, so D/ST gets no per-week curve and falls back to a flat rate.

## Reserved bench slots and forced drops

Two bench spots are kept for long-term stashes (mostly injury) and two for
streaming (`LeagueContext.stash_slots` / `stream_slots`, default 2/2). Each role
has its own week curve (`leverage.role_weights`):

- `regular` — base leverage weights
- `stream` — the next `STREAM_WEEKS` (2) only, so a one-week spike scores as a
  spike instead of being averaged across a season the player will not be here for
- `stash` — the short-term window dropped, so a player who is not helping yet is
  credited for the weeks he comes back

Two separate questions decide who holds a slot:

- **Does a player qualify?** Both roles are tested the same way: a *normalized*
  delta (`roster_roles.role_ratio`) `(role − regular) / |role|` clearing that
  role's threshold — `stash_ratio_threshold` **0.25**, `stream_ratio_threshold`
  **0.40** — and a role value clearing `role_value_floor` (default **1.0**).

  The normalization is the whole point. For a player simply absent across the
  short window the ratio comes out as **the base weight of the weeks missed**,
  so a full-window absence lands on the window mass (0.278 here) whether the
  player is worth +2.3, +2.8 or −2.3. An absolute gain cannot do this: it
  measures quality, not absence. Measured, a stud missing **one bye week**
  scored +1.04 against +0.64 and +0.78 for mid players out for the **whole
  window** — no threshold separates those, which is why this is a ratio.
  Dividing by `|stash|` rather than `stash` keeps the sign honest: for a
  declining player both values are negative and an unsigned divide reads a
  decline as stash shape (Romeo Doubs, playing all three window weeks, read
  +0.326 before and −0.326 after). The ratio is also stable across a 3-week and
  a 4-week absence where the raw delta was not (0.64 vs 0.53).

  The value floor exists because a near-zero denominator inflates a trivial
  delta — Malik Washington read **+0.443**, the highest on the roster, off a
  delta of 0.12 on a value of 0.28. A player who would not hold a roster spot
  does not deserve a reserved one. A sub-replacement player whose return is
  weak lands negative and is excluded by design (Pittman, −0.278).

  **Stream** reads the same way with its own ceiling. A player who plays *only*
  the stream window puts all of his worth there, so the ratio comes out at
  exactly `1 − (stream window mass)` = **0.815** here, again whatever he is
  worth. Measured shapes below it: declining 20→11 **0.725**, four good weeks
  **0.630**, declining 20→14 **0.484**, six good weeks **0.444**, flat
  **0.000**. The threshold sweep over the live player pool: 0.30 → 2 qualify,
  **0.40 → 2** (Stafford 0.440, Rodgers 1.211), 0.45 → 1, 0.50 → 1. So 0.40 is
  the loosest cut that still rejects a six-week lean.

  The ratio **runs past that ceiling when the regular value is negative** —
  Rachaad White, worth −3.63 over the season and +0.12 for two weeks, reads
  **+30.07**. That is honest ("more than all of my worth sits in the window")
  and harmless, because the value floor is what decides the slot: only **9 of
  97** players clear 1.0 on the stream curve. Before the floor, four
  below-replacement players were stream-eligible, two with a *negative* stream
  value (Kamara −0.60, Bates −0.86) — eligible for a reserved bench slot while
  being worth less than the waiver wire.
- **Who gets the slot?** The highest **value** in that role — not the biggest
  gain, and not whoever was there first. The slot exists to be used well, so a
  third qualifier worth more in the role displaces the weakest incumbent. A
  player qualifying for both prefers the role it gains more from and is
  considered for the other only if that one is full.

**A defense is always a streaming slot** (`ALWAYS_STREAM_POSITIONS`), starter or
not, whatever its curve looks like — it is churned on matchup, never held for a
season. It consumes stream capacity like any other tenant, so only one other
streamer can be held alongside it.

Qualification is about the **shape** of a curve: back-loaded gains under `stash`,
front-loaded under `stream`. Normalizing is what isolates that shape from
quality, and the value floor is what keeps a shape nobody wants out of a slot.
On the live roster exactly one player qualifies on merit (Stafford, stream),
alongside the unconditional D/ST; the stash slots sit empty, which is the
correct answer for a roster with no worthwhile injury stash.

**A role curve is a yardstick for a head-to-head, not a discount on what a
player is worth to own.** `roster_totals` deliberately scores everyone on the
regular curve. Role-weighting a roster total destroys real value: a season-long
asset that happens to be front-loaded gets parked in a streaming slot and has its
rest-of-season worth zeroed, because the stream curve only looks two weeks ahead.
A genuine streamer already scores low later; their own curve does that work.

The reservation is a lens, not a holdback — same number of active spots, and a
role with no good occupant just holds ordinary depth.

**An arrival never inherits the dropped player's role.** Roles are re-derived
from the threshold and the caps on the post-trade roster, so a flat arrival
replacing a stash stays ordinary depth.

### Choosing the forced drop

A drop only happens because someone is arriving, so the arrival is the yardstick.
Two implementations, picked by cost:

**`grade_trade` (single trade, `exact_drop=True` by default)** uses
`trades.choose_drops_exactly`: try every legal cut and keep whichever leaves the
most total value, scored the same way the trade itself is — roster VORP plus the
weekly-reoptimized lineup. Every preview path (`trade-grade`, `add`, `claim`,
`trade`) goes through here.

**`search_trades` / `rank_waiver_claims`** use the fast proxy
`roster_roles.choose_forced_drops`. Measured: 0.08ms a trade against 12.15ms for
the exact version, which would add ~11 minutes to a 54,756-trade search. They
agree on 87% of real packages, so bulk ranking uses the proxy and the decision
you act on uses the exact one. **That divergence is deliberate** — a trade can
show a different drop in search than in its own grade.

The proxy's yardstick is the **marginal arrival**: the weakest of the arrivals,
**averaged across all three role curves** (`roster_roles.marginal_arrival`).
Averaging rather than judging each arrival on one curve keeps the yardstick
independent of which slot an arrival happens to land in, and stops a narrow
one-week spike passing itself off as the stronger piece. On a full roster
the player causing the squeeze is the last man in, not the stud — the stud
displaces someone good. Using the best arrival instead makes every incumbent look
badly beaten and collapses the rule into "cut the lowest-value player", which is
what the role curves exist to avoid. Each rostered player is then measured by how
far that marginal arrival clears them **in the role they themselves occupy**, and
whoever is beaten worst goes.

The drop is priced as if it were on the send side: roster delta is
`after − before` where `after` excludes the cut player, so
`recv − send − dropped` reconciles exactly. The lineup delta also runs on the
**post-drop** roster, never the over-cap one.

**The last player at a position is protected.** Positions are counted *after*
the trade, so a newcomer at that position covers it and un-protects the incumbent
automatically. Without this the old VORP-ranked version cut your only kicker on a
1:2 — a kicker grades near replacement as an asset while being worth a whole
lineup slot every week, which cost 7.5 points of lineup value against cutting a
spare RB. Do not reintroduce a drop rule that ranks by asset VORP alone.

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
