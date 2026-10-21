"""Per-week leverage: how much each remaining week's production matters.

Replaces the old two-bucket short-term / long-term split, which overlapped (the
short-term weeks were a subset of the long-term weeks, so near weeks counted
twice at different weights) and inflated every value.

Each remaining week gets a weight instead, normalized to sum to 1, so a value
built from them reads as points per weighted game above replacement.

What the weights can and cannot know:

- Regular-season weeks are weighted **flat**. The league schedule is not
  available here, so remaining games are exchangeable, and the expected
  qualification swing of a game is then identical for every remaining week.
  Tilting them against each other would be inventing structure we cannot see.
- Playoff weeks are weighted by the chance we are still alive to play them,
  times `playoff_stakes` for the fact that a playoff game decides more than a
  regular one.

So the real work the odds model does is set the regular-vs-playoff balance. A
team that is already in weights the playoff weeks heavily; a team that probably
will not get there weights the games in front of it instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from statistics import NormalDist
from typing import Any, Iterable, Mapping

# Early-season records are noisy, so shrink toward a coin flip by this many games.
STRENGTH_PRIOR_GAMES = 4.0
STRENGTH_FLOOR = 0.20
STRENGTH_CEILING = 0.80

# A playoff game decides more than a regular-season game, so it counts for more
# than one once you are actually there.
DEFAULT_PLAYOFF_STAKES = 3.0

SCHEMES = ("leverage", "decay", "uniform", "manual")
DEFAULT_DECAY = 0.15


def team_strength(
    wins: int,
    losses: int,
    ties: int = 0,
    *,
    prior_games: float = STRENGTH_PRIOR_GAMES,
) -> float:
    """Per-game win probability, shrunk toward 0.5 so a 1-3 start is not 0.25."""
    played = int(wins) + int(losses) + int(ties)
    credited = int(wins) + 0.5 * int(ties)
    strength = (credited + 0.5 * prior_games) / (played + prior_games)
    return min(STRENGTH_CEILING, max(STRENGTH_FLOOR, strength))


def playoff_cut_wins(
    *,
    regular_season_games: int,
    playoff_team_count: int,
    team_count: int,
) -> float:
    """Win total that claims a playoff spot, as a quantile of the league.

    With coin-flip games, final win rates spread roughly Normal(0.5, 0.5/sqrt(G)),
    so the cut sits where the top `playoff_team_count / team_count` share begins.
    """
    games = max(int(regular_season_games), 1)
    share = min(0.99, max(0.01, playoff_team_count / max(int(team_count), 1)))
    z = NormalDist().inv_cdf(1.0 - share)
    cut_rate = 0.5 + z * (0.5 / math.sqrt(games))
    return min(float(games), max(0.0, cut_rate * games))


def _binom_pmf(n: int, k: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    return math.comb(n, k) * (p**k) * ((1.0 - p) ** (n - k))


def p_reach(wins_now: float, games_left: int, cut: float, p: float) -> float:
    """P(final wins >= cut) from here, with `games_left` games at win rate p."""
    need = cut - wins_now
    if need <= 0:
        return 1.0
    if need > games_left:
        return 0.0
    first = math.ceil(need - 1e-9)
    return sum(_binom_pmf(games_left, k, p) for k in range(first, games_left + 1))


def game_swing(wins_now: float, games_after: int, cut: float, p: float) -> float:
    """P(make playoffs | win this game) - P(make playoffs | lose it).

    Reported as a diagnostic. It is the same for every remaining regular-season
    game under exchangeable opponents, so it does not vary the weights week to
    week; it only describes how pivotal each game is.
    """
    won = p_reach(wins_now + 1, games_after, cut, p)
    lost = p_reach(wins_now, games_after, cut, p)
    return max(0.0, won - lost)


@dataclass(frozen=True)
class WeekWeights:
    """Normalized weight per remaining week, plus the inputs that produced it."""

    weights: Mapping[int, float]
    scheme: str
    strength: float = 0.5
    cut_wins: float = 0.0
    playoff_odds: float = 0.0
    game_swing: float = 0.0
    degenerate: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def weeks(self) -> list[int]:
        return sorted(self.weights)

    def weight(self, week: int) -> float:
        return float(self.weights.get(int(week), 0.0))

    def mass(self, weeks: Iterable[int]) -> float:
        """Share of total weight sitting in `weeks`."""
        return sum(self.weight(week) for week in weeks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "strength": round(self.strength, 3),
            "cut_wins": round(self.cut_wins, 2),
            "playoff_odds": round(self.playoff_odds, 3),
            "game_swing": round(self.game_swing, 3),
            "weights": {week: round(value, 4) for week, value in sorted(self.weights.items())},
            "notes": list(self.notes),
        }


ROLE_REGULAR = "regular"
ROLE_STREAM = "stream"
ROLE_STASH = "stash"
ROLES = (ROLE_REGULAR, ROLE_STREAM, ROLE_STASH)

# How many weeks ahead a streaming slot is held for.
STREAM_WEEKS = 2


def role_weights(
    base: "WeekWeights",
    context: Any,
    role: str,
    *,
    stream_weeks: int = STREAM_WEEKS,
) -> "WeekWeights":
    """Reweight the remaining weeks for what a roster slot is actually for.

    A bench slot is an option on future lineup value, and which weeks it is an
    option on depends on why you are holding the player:

    - `regular` keeps the base weights.
    - `stream` counts only the next couple of weeks, because that slot gets
      churned; a one-week spike should score as a spike, not be averaged away
      across a season the player will not be here for.
    - `stash` drops the short-term window and counts what is left, because the
      slot is held for a player who is not helping yet.

    Falls back to the base weights when a role has no weeks to stand on (late
    season, where everything remaining is already the short-term window).
    """
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}. Use one of {', '.join(ROLES)}.")
    if role == ROLE_REGULAR:
        return base
    weeks = list(context.remaining_weeks)
    if role == ROLE_STREAM:
        kept = weeks[: max(1, int(stream_weeks))]
    else:
        short = set(context.short_term_week_list)
        kept = [week for week in weeks if week not in short]
    raw = {week: base.weight(week) for week in kept}
    weights, degenerate = _normalize(raw)
    if degenerate and not raw:
        return base
    return WeekWeights(
        weights=weights,
        scheme=f"{base.scheme}:{role}",
        strength=base.strength,
        cut_wins=base.cut_wins,
        playoff_odds=base.playoff_odds,
        game_swing=base.game_swing,
        degenerate=base.degenerate,
        notes=base.notes + (f"{role} slot: weeks {kept[0]}-{kept[-1]}" if kept else (),),
    )


def _normalize(raw: dict[int, float]) -> tuple[dict[int, float], bool]:
    total = sum(raw.values())
    if total <= 0:
        # Eliminated, or every week graded zero: fall back to flat so values stay
        # comparable instead of collapsing to 0 for every player at once.
        if not raw:
            return {}, True
        flat = 1.0 / len(raw)
        return {week: flat for week in raw}, True
    return {week: value / total for week, value in raw.items()}, False


@lru_cache(maxsize=256)
def _leverage_raw(
    current_week: int,
    season_end_week: int,
    regular_season_end: int,
    wins: int,
    losses: int,
    ties: int,
    playoff_team_count: int,
    team_count: int,
    playoff_stakes: float,
) -> tuple[tuple[tuple[int, float], ...], float, float, float, float]:
    p = team_strength(wins, losses, ties)
    cut = playoff_cut_wins(
        regular_season_games=regular_season_end,
        playoff_team_count=playoff_team_count,
        team_count=team_count,
    )
    games_left = max(0, regular_season_end - current_week + 1)
    odds = p_reach(wins, games_left, cut, p)
    swing = game_swing(wins, max(0, games_left - 1), cut, p)

    raw: dict[int, float] = {}
    for week in range(current_week, season_end_week + 1):
        if week <= regular_season_end:
            # Flat: exchangeable opponents leave nothing to tell these weeks apart.
            raw[week] = 1.0
        else:
            # Alive here only by making the field and winning your way to it.
            rounds_in = week - regular_season_end
            raw[week] = odds * (p ** (rounds_in - 1)) * playoff_stakes
    return tuple(sorted(raw.items())), p, cut, odds, swing


def week_weights(
    context: Any,
    *,
    scheme: str = "leverage",
    decay: float = DEFAULT_DECAY,
    manual: Mapping[int, float] | None = None,
    playoff_stakes: float = DEFAULT_PLAYOFF_STAKES,
) -> WeekWeights:
    """Weight each remaining week by how much its production matters.

    `leverage` balances the games in front of you against playoff weeks you may
    not reach; `decay` falls off exponentially from this week; `uniform` treats
    every week alike; `manual` takes the weights as given (missing weeks score 0).
    """
    if scheme not in SCHEMES:
        raise ValueError(f"Unknown scheme {scheme!r}. Use one of {', '.join(SCHEMES)}.")
    weeks = list(context.remaining_weeks)

    if scheme == "manual":
        if not manual:
            raise ValueError("scheme='manual' needs a week -> weight mapping.")
        raw = {week: max(0.0, float(manual.get(week, 0.0))) for week in weeks}
    elif scheme == "uniform":
        raw = {week: 1.0 for week in weeks}
    elif scheme == "decay":
        raw = {week: math.exp(-decay * (week - context.current_week)) for week in weeks}
    else:
        items, strength, cut, odds, swing = _leverage_raw(
            int(context.current_week),
            int(context.season_end_week),
            int(context.regular_season_end),
            int(context.wins),
            int(context.losses),
            int(context.ties),
            int(context.playoff_team_count),
            int(context.team_count),
            float(playoff_stakes),
        )
        weights, degenerate = _normalize(dict(items))
        notes = [
            f"per-game win rate {strength:.0%}; playoff cut ~{cut:.1f} wins",
            f"playoff odds {odds:.0%}; each remaining game swings them {swing:+.0%}",
            f"regular-season weeks weighted flat; playoff weeks x{playoff_stakes:g} when reached",
        ]
        if degenerate:
            notes.append("no week carried weight (eliminated); weighting every week alike")
        return WeekWeights(
            weights=weights,
            scheme=scheme,
            strength=strength,
            cut_wins=cut,
            playoff_odds=odds,
            game_swing=swing,
            degenerate=degenerate,
            notes=tuple(notes),
        )

    weights, degenerate = _normalize(raw)
    notes = []
    if scheme == "decay":
        notes.append(f"exponential decay {decay:.2f}/week from week {context.current_week}")
    return WeekWeights(
        weights=weights, scheme=scheme, degenerate=degenerate, notes=tuple(notes)
    )
