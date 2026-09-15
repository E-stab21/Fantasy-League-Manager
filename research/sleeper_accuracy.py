#!/usr/bin/env python3
"""Calibrate Sleeper projection accuracy vs actual fantasy points.

Answers:
1. Weekly MAE / bias by position
2. Best blend weight between Sleeper and an alternate weekly source
   (nflverse expected fantasy points — we do not have archived ESPN weeklies)
3. ROS week-sum drift (as-of week W remaining proj vs remaining actuals)
4. Injury-status miss (projected vs actual when tagged OUT/Q/D/IR)

Usage:
  PYENV_VERSION=3.13.3 python3 research/sleeper_accuracy.py
  PYENV_VERSION=3.13.3 python3 research/sleeper_accuracy.py --seasons 2024,2025
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests

CACHE = Path("/tmp/league-manager-research")
POSITIONS = ("QB", "RB", "WR", "TE")
SCORING = "pts_ppr"


def _get_json(url: str, *, params: dict | None = None, sleep: float = 0.05) -> Any:
    time.sleep(sleep)
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def load_sleeper_week_projections(season: int, week: int) -> dict[str, dict[str, Any]]:
    path = CACHE / f"sleeper-proj-{season}-w{week}.json"
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        CACHE.mkdir(parents=True, exist_ok=True)
        rows = _get_json(
            f"https://api.sleeper.app/projections/nfl/{season}/{week}",
            params={"season_type": "regular"},
        )
        path.write_text(json.dumps(rows))
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        pid = str(row.get("player_id") or "")
        stats = row.get("stats") or {}
        pred = stats.get(SCORING)
        if not pid or pred is None:
            continue
        player = row.get("player") or {}
        pos = player.get("position")
        if pos not in POSITIONS:
            continue
        out[pid] = {
            "player_id": pid,
            "name": " ".join(
                p for p in (player.get("first_name"), player.get("last_name")) if p
            ),
            "position": pos,
            "pred": float(pred),
            "injury_status": (player.get("injury_status") or "").upper() or None,
            "week": week,
            "season": season,
        }
    return out


def load_sleeper_week_stats(season: int, week: int) -> dict[str, float]:
    path = CACHE / f"sleeper-stats-{season}-w{week}.json"
    if path.exists():
        raw = json.loads(path.read_text())
    else:
        CACHE.mkdir(parents=True, exist_ok=True)
        raw = _get_json(f"https://api.sleeper.app/v1/stats/nfl/regular/{season}/{week}")
        path.write_text(json.dumps(raw))
    out: dict[str, float] = {}
    for pid, row in raw.items():
        if not isinstance(row, dict):
            continue
        pts = row.get(SCORING)
        if pts is None:
            continue
        out[str(pid)] = float(pts)
    return out


def mae(errors: list[float]) -> float:
    return sum(abs(x) for x in errors) / len(errors) if errors else float("nan")


def bias(errors: list[float]) -> float:
    # pred - actual residuals
    return sum(errors) / len(errors) if errors else float("nan")


def rmse(errors: list[float]) -> float:
    return math.sqrt(sum(x * x for x in errors) / len(errors)) if errors else float("nan")


def summarize(residuals: list[float]) -> dict[str, float]:
    """residuals = pred - actual."""
    return {
        "n": len(residuals),
        "mae": round(mae(residuals), 3),
        "bias": round(bias(residuals), 3),
        "rmse": round(rmse(residuals), 3),
    }


def build_weekly_pairs(seasons: list[int], through_week: int = 17) -> list[dict[str, Any]]:
    pairs = []
    for season in seasons:
        for week in range(1, through_week + 1):
            proj = load_sleeper_week_projections(season, week)
            stats = load_sleeper_week_stats(season, week)
            for pid, row in proj.items():
                if pid not in stats:
                    continue
                actual = stats[pid]
                # Skip pure zeros on both sides (often inactive placeholders)
                if row["pred"] <= 0 and actual <= 0:
                    continue
                pairs.append(
                    {
                        **row,
                        "actual": actual,
                        "residual": row["pred"] - actual,
                        "abs_err": abs(row["pred"] - actual),
                    }
                )
    return pairs


def weekly_accuracy(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    by_pos: dict[str, list[float]] = defaultdict(list)
    overall = []
    for row in pairs:
        by_pos[row["position"]].append(row["residual"])
        overall.append(row["residual"])
    return {
        "overall": summarize(overall),
        "by_position": {pos: summarize(by_pos[pos]) for pos in POSITIONS if by_pos[pos]},
    }


def load_opportunity_by_sleeper(seasons: list[int]) -> dict[tuple[int, int, str], float]:
    """Map (season, week, sleeper_id) -> nflverse expected fantasy points (PPR-ish).

    WARNING: nflverse opportunity expected points are largely *same-week*
    opportunity conversions, not pregame projections. Kept for reference only.
    """
    import nflreadpy as nfl

    ids = nfl.load_ff_playerids()
    # polars
    id_map = {
        str(row["sleeper_id"]): str(row["gsis_id"])
        for row in ids.select(["sleeper_id", "gsis_id"]).iter_rows(named=True)
        if row.get("sleeper_id") is not None and row.get("gsis_id")
    }
    gsis_to_sleeper = {gsis: sleeper for sleeper, gsis in id_map.items()}
    opp = nfl.load_ff_opportunity(seasons=seasons)
    out: dict[tuple[int, int, str], float] = {}
    for row in opp.select(
        ["season", "week", "player_id", "position", "total_fantasy_points_exp", "total_fantasy_points"]
    ).iter_rows(named=True):
        gsis = str(row["player_id"] or "")
        sleeper = gsis_to_sleeper.get(gsis)
        if not sleeper:
            continue
        if row["position"] not in POSITIONS:
            continue
        exp = row["total_fantasy_points_exp"]
        if exp is None:
            continue
        season = int(row["season"])
        week = int(row["week"])
        out[(season, week, sleeper)] = float(exp)
    return out


def _mae_for_weight(w: float, left: list[float], right: list[float], actual: list[float]) -> float:
    errs = [abs(w * a + (1 - w) * b - y) for a, b, y in zip(left, right, actual)]
    return sum(errs) / len(errs)


def blend_grid(
    left: list[float],
    right: list[float],
    actual: list[float],
    *,
    left_name: str,
    right_name: str,
) -> dict[str, Any]:
    grid = [i / 20 for i in range(0, 21)]
    scores = {w: _mae_for_weight(w, left, right, actual) for w in grid}
    best_w = min(scores, key=scores.get)
    return {
        "n": len(actual),
        "left": left_name,
        "right": right_name,
        "best_left_weight": best_w,
        "mae_at_best": round(scores[best_w], 3),
        f"mae_{left_name}_only": round(scores[1.0], 3),
        f"mae_{right_name}_only": round(scores[0.0], 3),
        "mae_5050": round(scores[0.5], 3),
    }


def blend_weights(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Pregame blends: Sleeper vs last-week actual / trailing mean.

    ESPN historical weeklies are not publicly archived, so we cannot fit
    ESPN↔Sleeper directly.
    """
    by_key = {(r["season"], r["week"], r["player_id"]): r for r in pairs}

    # Sleeper vs prior-week actual
    s_lw, lw, y_lw, pos_lw = [], [], [], []
    for r in pairs:
        prev = by_key.get((r["season"], r["week"] - 1, r["player_id"]))
        if not prev:
            continue
        s_lw.append(r["pred"])
        lw.append(prev["actual"])
        y_lw.append(r["actual"])
        pos_lw.append(r["position"])

    # Sleeper vs trailing 3-week mean of actuals
    s_tr, tr, y_tr, pos_tr = [], [], [], []
    for r in pairs:
        hist = []
        for back in (1, 2, 3):
            prev = by_key.get((r["season"], r["week"] - back, r["player_id"]))
            if prev:
                hist.append(prev["actual"])
        if len(hist) < 2:
            continue
        s_tr.append(r["pred"])
        tr.append(sum(hist) / len(hist))
        y_tr.append(r["actual"])
        pos_tr.append(r["position"])

    out: dict[str, Any] = {
        "espn_note": (
            "No archived ESPN weekly projections available via public APIs, "
            "so ESPN↔Sleeper start/sit weights cannot be fit from history yet. "
            "Proxy blends below use only pregame-available signals."
        ),
        "vs_last_week_actual": blend_grid(
            s_lw, lw, y_lw, left_name="sleeper", right_name="last_week"
        ),
        "vs_trailing_mean": blend_grid(
            s_tr, tr, y_tr, left_name="sleeper", right_name="trail3"
        ),
        "by_position_vs_last_week": {},
    }
    for pos in POSITIONS:
        idx = [i for i, p in enumerate(pos_lw) if p == pos]
        if len(idx) < 50:
            continue
        out["by_position_vs_last_week"][pos] = blend_grid(
            [s_lw[i] for i in idx],
            [lw[i] for i in idx],
            [y_lw[i] for i in idx],
            left_name="sleeper",
            right_name="last_week",
        )

    # Reference only: same-week opportunity (NOT a valid pregame blend)
    try:
        seasons = sorted({r["season"] for r in pairs})
        alt = load_opportunity_by_sleeper(seasons)
        s_o, o_o, y_o = [], [], []
        for r in pairs:
            key = (r["season"], r["week"], r["player_id"])
            if key not in alt:
                continue
            s_o.append(r["pred"])
            o_o.append(alt[key])
            y_o.append(r["actual"])
        out["reference_same_week_opportunity"] = {
            **blend_grid(s_o, o_o, y_o, left_name="sleeper", right_name="opp_exp"),
            "warning": (
                "nflverse total_fantasy_points_exp uses same-week opportunity; "
                "it is not a pregame projection. Do not use this weight for start/sit."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        out["reference_same_week_opportunity"] = {"error": str(exc)}

    return out



def ros_drift(seasons: list[int], through_week: int = 17) -> dict[str, Any]:
    """As of week W, sum of proj[W..17] vs sum of actual[W..17]."""
    by_asof: dict[int, list[float]] = defaultdict(list)
    by_asof_pos: dict[str, dict[int, list[float]]] = {p: defaultdict(list) for p in POSITIONS}

    for season in seasons:
        # cache all weeks
        proj_weeks = {w: load_sleeper_week_projections(season, w) for w in range(1, through_week + 1)}
        stat_weeks = {w: load_sleeper_week_stats(season, w) for w in range(1, through_week + 1)}
        # players who appear in any week
        players = set()
        for week_map in proj_weeks.values():
            players.update(week_map.keys())

        for asof in range(1, through_week + 1):
            remaining = list(range(asof, through_week + 1))
            for pid in players:
                # need projection at asof (or first remaining week available)
                pred_total = 0.0
                act_total = 0.0
                pos = None
                used = False
                for week in remaining:
                    prow = proj_weeks[week].get(pid)
                    if prow is None:
                        continue
                    pos = prow["position"]
                    pred_total += prow["pred"]
                    act_total += float(stat_weeks[week].get(pid, 0.0))
                    used = True
                if not used or pred_total <= 0:
                    continue
                # Require the player to have been projected in the as-of week
                if pid not in proj_weeks[asof]:
                    continue
                residual = pred_total - act_total
                by_asof[asof].append(residual)
                if pos in by_asof_pos:
                    by_asof_pos[pos][asof].append(residual)

    weeks_out = {}
    for asof, residuals in sorted(by_asof.items()):
        weeks_out[str(asof)] = summarize(residuals)
    # early / mid / late snapshots
    snapshots = {
        "week_1": summarize(by_asof.get(1, [])),
        "week_5": summarize(by_asof.get(5, [])),
        "week_10": summarize(by_asof.get(10, [])),
        "week_14": summarize(by_asof.get(14, [])),
    }
    return {
        "definition": (
            "As of week W: sum Sleeper weekly projections for weeks W..17 "
            "minus sum actual PPR for weeks W..17 (same players)."
        ),
        "snapshots": snapshots,
        "by_asof_week": weeks_out,
        "by_position_week_1": {
            pos: summarize(by_asof_pos[pos].get(1, [])) for pos in POSITIONS
        },
    }


def injury_miss(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = defaultdict(list)
    for row in pairs:
        status = row.get("injury_status") or "NONE"
        if status in {"", "None"}:
            status = "NONE"
        buckets[status].append(row["residual"])
    # focus on common fantasy statuses
    interesting = ["NONE", "QUESTIONABLE", "DOUBTFUL", "OUT", "IR", "PUP", "SUSPEND"]
    out = {}
    for key in interesting:
        if key in buckets:
            out[key] = summarize(buckets[key])
    # any other
    for key, vals in sorted(buckets.items()):
        if key not in out:
            out[key] = summarize(vals)
    return {
        "definition": "residual = projected PPR − actual. Positive bias = overprojected.",
        "by_injury_status": out,
    }


def naive_baselines(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare Sleeper to prior-week actual (persistence) where available."""
    by_key = {(r["season"], r["week"], r["player_id"]): r for r in pairs}
    sleeper_err = []
    persist_err = []
    for r in pairs:
        prev = by_key.get((r["season"], r["week"] - 1, r["player_id"]))
        if not prev:
            continue
        sleeper_err.append(abs(r["pred"] - r["actual"]))
        persist_err.append(abs(prev["actual"] - r["actual"]))
    return {
        "n": len(sleeper_err),
        "mae_sleeper": round(sum(sleeper_err) / len(sleeper_err), 3) if sleeper_err else None,
        "mae_last_week_actual": round(sum(persist_err) / len(persist_err), 3) if persist_err else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", default="2024,2025", help="Comma-separated seasons")
    parser.add_argument("--through-week", type=int, default=17)
    parser.add_argument(
        "--out",
        default="research/sleeper_accuracy_report.json",
        help="Write full JSON report here",
    )
    args = parser.parse_args()
    seasons = [int(s.strip()) for s in args.seasons.split(",") if s.strip()]

    print(f"Building weekly pairs for seasons={seasons} weeks=1..{args.through_week} ...")
    pairs = build_weekly_pairs(seasons, through_week=args.through_week)
    print(f"  {len(pairs)} player-weeks")

    report: dict[str, Any] = {
        "seasons": seasons,
        "scoring": SCORING,
        "positions": list(POSITIONS),
        "n_player_weeks": len(pairs),
        "caveats": [
            "Sleeper historical projections may be revised after the fact; "
            "updated_at timestamps for 2024 W1 clustered near game week, so bias risk looks limited but not zero.",
            "No archived ESPN weekly projections — blend study uses nflverse expected fantasy points instead.",
            "Actuals are Sleeper pts_ppr (not your ESPN league scoring).",
        ],
        "1_weekly_accuracy": weekly_accuracy(pairs),
        "naive_baseline": naive_baselines(pairs),
    }

    print("Fitting pregame blend weights...")
    report["2_blend_weights"] = blend_weights(pairs)

    print("Computing ROS drift...")
    report["3_ros_drift"] = ros_drift(seasons, through_week=args.through_week)

    print("Injury miss...")
    report["4_injury_miss"] = injury_miss(pairs)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
