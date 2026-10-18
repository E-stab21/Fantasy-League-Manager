"""Bench slots reserved for a purpose, and valued for that purpose.

A bench spot is an option on future lineup value, and which weeks it is an
option on depends on why the player is being held. Two spots are kept for
long-term stashes (a player who is not helping yet, mostly injury) and two for
streaming (churned every week or two). Each role scores its occupant on its own
week curve, so a one-week spike is not averaged across a season the player will
not be around for, and an injury stash is credited for the weeks he comes back
rather than the weeks he misses.

The reservation is a lens, not a holdback: there are still the same number of
active spots, and a role with no good occupant just holds ordinary depth valued
on the regular curve.
"""

from __future__ import annotations

from typing import Any, Callable

from league_manager.leverage import (
    ROLE_REGULAR,
    ROLE_STASH,
    ROLE_STREAM,
    STREAM_WEEKS,
    WeekWeights,
    role_weights,
)
from league_manager.slots import IR_SLOT, is_starter_slot, normalize_position, parse_slot
from league_manager.value import LeagueContext, replacement_weekly, weighted_vorp

ROLES_ALL = (ROLE_REGULAR, ROLE_STASH, ROLE_STREAM)

# Defenses are a streaming position by nature -- churned on matchup, never held
# for a season -- so one is always a streaming slot regardless of what its curve
# looks like, and it consumes stream capacity like any other occupant.
ALWAYS_STREAM_POSITIONS = {"D/ST"}

DEFAULT_STASH_SLOTS = 2
DEFAULT_STREAM_SLOTS = 2

# A player earns a reserved slot only when its curve is worth a real amount more
# than the regular one, in the same points-per-weighted-game units as everything
# else. Below this they are ordinary depth that happens to lean slightly one way.
# (A flat curve scores identically under any normalized weighting, so without
# some floor floating-point noise alone would hand out slots.)
DEFAULT_ROLE_GAIN_THRESHOLD = 0.5


def _slot_id(player: dict[str, Any]) -> int | None:
    raw = player.get("lineup_slot_id")
    if raw is None and player.get("lineup_slot"):
        try:
            return parse_slot(player["lineup_slot"])
        except ValueError:
            return None
    return int(raw) if raw is not None else None


def role_weight_set(
    base: WeekWeights,
    context: LeagueContext,
    *,
    stream_weeks: int = STREAM_WEEKS,
) -> dict[str, WeekWeights]:
    """One week curve per role, built once and reused."""
    return {
        role: role_weights(base, context, role, stream_weeks=stream_weeks)
        for role in (ROLE_REGULAR, ROLE_STREAM, ROLE_STASH)
    }


class RoleScorer:
    """Scores a player under any role curve, reusing their per-week curve."""

    def __init__(
        self,
        context: LeagueContext,
        baselines: dict[str, float],
        role_sets: dict[str, WeekWeights],
        *,
        curve_for: Callable[[dict[str, Any]], dict[int, float | None]],
    ) -> None:
        self.context = context
        self.baselines = baselines
        self.role_sets = role_sets
        self.curve_for = curve_for
        self.short_weeks = set(context.short_term_week_list)
        self._cache: dict[tuple[Any, str], tuple[float, float]] = {}

    def split(self, player: dict[str, Any], role: str) -> tuple[float, float]:
        key = (player.get("id"), role)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        curve = self.curve_for(player)
        repl = replacement_weekly(player, self.baselines)
        value = weighted_vorp(curve, repl, self.role_sets[role], self.short_weeks)
        self._cache[key] = value
        return value

    def value(self, player: dict[str, Any], role: str) -> float:
        short_total, long_total = self.split(player, role)
        return short_total + long_total


def assign_roles(
    roster: list[dict[str, Any]],
    *,
    scorer: RoleScorer,
    stash_slots: int = DEFAULT_STASH_SLOTS,
    stream_slots: int = DEFAULT_STREAM_SLOTS,
    gain_threshold: float = DEFAULT_ROLE_GAIN_THRESHOLD,
) -> dict[Any, str]:
    """Decide which bench players occupy the stash and stream slots.

    Two separate questions, deliberately:

    - **Does a player qualify?** Only if its role curve is worth at least
      `gain_threshold` more than the regular curve. A slight lean is not a
      specialism, and without a floor a flat curve qualifies on rounding noise.
    - **Who gets the slot?** The highest *value* in that role, not the biggest
      gain. The slot exists to be used well, so a third qualifier worth more in
      the role displaces the weakest incumbent.

    A player qualifying for both prefers the role it gains more from, and is
    considered for the other only if that one is full. Starters and IR keep the
    regular curve: a starter's week-to-week worth is already carried by the
    lineup term.
    """
    capacity = {
        ROLE_STASH: max(0, int(stash_slots)),
        ROLE_STREAM: max(0, int(stream_slots)),
    }
    roles: dict[Any, str] = {}
    bench: list[dict[str, Any]] = []
    for player in roster:
        pid = player.get("id")
        if pid is None:
            continue
        # A defense is always a streaming slot, starter or not, and takes up
        # stream capacity -- so only one other streamer can be held alongside it.
        if normalize_position(str(player.get("position") or "")) in ALWAYS_STREAM_POSITIONS:
            roles[pid] = ROLE_STREAM
            capacity[ROLE_STREAM] = max(0, capacity[ROLE_STREAM] - 1)
            continue
        slot = _slot_id(player)
        if slot == IR_SLOT or is_starter_slot(slot):
            roles[pid] = ROLE_REGULAR
            continue
        bench.append(player)
    # role -> [(role value, gain, player)] for everyone who clears the bar
    qualified: dict[str, list[tuple[float, float, dict[str, Any]]]] = {
        ROLE_STASH: [],
        ROLE_STREAM: [],
    }
    preferred: dict[Any, str] = {}
    for player in bench:
        pid = player.get("id")
        baseline = scorer.value(player, ROLE_REGULAR)
        best_gain = 0.0
        for role in (ROLE_STASH, ROLE_STREAM):
            value = scorer.value(player, role)
            gain = value - baseline
            if gain < gain_threshold:
                continue
            qualified[role].append((value, gain, player))
            if gain > best_gain:
                best_gain = gain
                preferred[pid] = role

    # Fill each slot with the most valuable qualifier that prefers it, then let
    # anyone still unplaced compete for whatever capacity is left.
    for pass_number in (1, 2):
        for role in (ROLE_STASH, ROLE_STREAM):
            if capacity[role] <= 0:
                continue
            ranked = sorted(qualified[role], key=lambda row: row[0], reverse=True)
            for value, _gain, player in ranked:
                pid = player.get("id")
                if pid in roles or capacity[role] <= 0:
                    continue
                if pass_number == 1 and preferred.get(pid) != role:
                    continue
                roles[pid] = role
                capacity[role] -= 1

    for player in bench:
        roles.setdefault(player.get("id"), ROLE_REGULAR)
    return roles


def roster_totals(
    roster: list[dict[str, Any]],
    scorer: RoleScorer,
) -> tuple[float, float]:
    """(short-term, rest) weighted VORP for the whole roster.

    Everyone is scored on the regular curve here, deliberately. A role curve is
    the yardstick for a head-to-head -- which player a new arrival displaces and
    on what basis -- not a discount on what a player is worth to own. Scoring a
    roster total through role curves throws away real value: a season-long asset
    sitting in a streaming slot would have its rest-of-season worth zeroed
    because the stream curve only looks two weeks ahead. A genuine streamer
    already scores low later on; their own curve does that work.
    """
    short_total = long_total = 0.0
    seen: set[Any] = set()
    for player in roster:
        pid = player.get("id")
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        short, long = scorer.split(player, ROLE_REGULAR)
        short_total += short
        long_total += long
    return short_total, long_total


def _position_counts(roster: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for player in roster:
        if _slot_id(player) == IR_SLOT:
            continue
        pos = normalize_position(str(player.get("position") or ""))
        if pos:
            counts[pos] = counts.get(pos, 0) + 1
    return counts


def marginal_arrival(
    incoming: list[dict[str, Any]],
    *,
    scorer: RoleScorer,
    roles: dict[Any, str] | None = None,
) -> dict[str, Any] | None:
    """The arrival that causes the squeeze: the weakest across all three roles.

    Averaged over regular, stash and stream rather than judged on one curve, so
    the yardstick does not depend on which slot the arrival happens to land in
    -- and an arrival that is only useful in a slot it cannot get does not pass
    itself off as the stronger piece.

    On a full roster this is the player who actually forces the drop. The stud
    in a package displaces someone good; the last man in pushes the final body
    off the roster.
    """
    if not incoming:
        return None

    def across_roles(player: dict[str, Any]) -> float:
        values = [scorer.value(player, role) for role in ROLES_ALL]
        return sum(values) / len(values)

    return min(incoming, key=across_roles)


def choose_forced_drops(
    roster: list[dict[str, Any]],
    *,
    incoming: list[dict[str, Any]],
    drops_needed: int,
    scorer: RoleScorer,
    roles: dict[Any, str],
) -> list[dict[str, Any]]:
    """Cut whoever the *marginal* arrival beats worst in that player's own role.

    A drop only happens because someone is arriving, and on a full roster the
    player who actually causes the squeeze is the weakest of the arrivals, not
    the best one. The stud displaces someone good; it is the last man in who
    pushes the final body off the roster. So the yardstick is the weakest
    arrival, judged at its own best use, and each rostered player is measured by
    how far that arrival clears them in the role they themselves occupy. Beaten
    worst means most redundant, so they go.

    Using the best arrival instead makes every incumbent look badly beaten and
    collapses the rule into "cut the lowest-value player", which is the thing
    the role curves exist to avoid.

    The last player at a position is protected, because cutting them leaves a
    starting slot that nothing can fill -- a kicker grades near replacement as
    an asset while being worth a whole slot every week. Positions are counted
    after the trade, so a newcomer at that position covers it and un-protects
    the incumbent automatically.
    """
    if drops_needed <= 0:
        return []
    incoming_ids = {player.get("id") for player in incoming}
    all_roles = set(roles.values()) | {ROLE_REGULAR, ROLE_STASH, ROLE_STREAM}
    marginal = marginal_arrival(incoming, scorer=scorer, roles=roles)
    yardstick = {
        role: (scorer.value(marginal, role) if marginal is not None else 0.0)
        for role in all_roles
    }

    remaining = list(roster)
    dropped: list[dict[str, Any]] = []
    for _ in range(drops_needed):
        counts = _position_counts(remaining)
        scored: list[tuple[float, bool, dict[str, Any]]] = []
        for player in remaining:
            pid = player.get("id")
            if pid is None or pid in incoming_ids or _slot_id(player) == IR_SLOT:
                continue
            role = roles.get(pid, ROLE_REGULAR)
            margin = yardstick.get(role, 0.0) - scorer.value(player, role)
            pos = normalize_position(str(player.get("position") or ""))
            last_at_position = counts.get(pos, 0) <= 1
            scored.append((margin, last_at_position, player))
        if not scored:
            break
        # Beaten worst first, and only fall back to a position's last player
        # when there is nobody else left to cut.
        scored.sort(key=lambda row: (row[1], -row[0]))
        _margin, _protected, cut = scored[0]
        dropped.append(cut)
        cut_id = cut.get("id")
        remaining = [player for player in remaining if player.get("id") != cut_id]
    return dropped
