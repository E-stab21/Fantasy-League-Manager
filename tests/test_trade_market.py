"""Unit tests for trade-market band calibration (no network)."""

from pathlib import Path

from league_manager.trade_market import (
    Band,
    LeagueProfile,
    active_band,
    player_market_insights,
    profiles_compatible,
    recommend_band,
    save_band,
    sleeper_league_profile,
)


def test_rejects_dynasty_and_superflex_mismatch():
    ref = LeagueProfile(team_count=10, scoring="ppr", superflex=False)
    dynasty = sleeper_league_profile(
        {
            "settings": {"type": 2, "num_teams": 10},
            "roster_positions": ["QB", "RB", "WR", "TE"],
            "scoring_settings": {"rec": 1.0},
        }
    )
    assert dynasty is None

    sf = sleeper_league_profile(
        {
            "settings": {"type": 0, "num_teams": 10},
            "roster_positions": ["QB", "RB", "WR", "TE", "SUPER_FLEX"],
            "scoring_settings": {"rec": 1.0},
        }
    )
    assert sf is not None
    assert not profiles_compatible(sf, ref)

    good = sleeper_league_profile(
        {
            "settings": {"type": 0, "num_teams": 10},
            "roster_positions": ["QB", "RB", "WR", "TE"],
            "scoring_settings": {"rec": 1.0},
        }
    )
    assert good is not None
    assert profiles_compatible(good, ref)


def test_recommend_band_raises_top_when_accepts_are_fatter(tmp_path: Path):
    current = Band(max_surplus=20.0, min_their_face=-20.0, min_their_lt=-30.0)
    win_sides = [
        {"blended": 12.0, "their_espn_face_net": -10.0, "their_ros_vorp_net": -15.0},
        {"blended": 18.0, "their_espn_face_net": -25.0, "their_ros_vorp_net": -40.0},
        {"blended": 22.0, "their_espn_face_net": -30.0, "their_ros_vorp_net": -35.0},
        {"blended": 28.0, "their_espn_face_net": -45.0, "their_ros_vorp_net": -50.0},
        {"blended": 35.0, "their_espn_face_net": -55.0, "their_ros_vorp_net": -60.0},
        {"blended": 40.0, "their_espn_face_net": -60.0, "their_ros_vorp_net": -70.0},
        {"blended": 45.0, "their_espn_face_net": -65.0, "their_ros_vorp_net": -80.0},
        {"blended": 50.0, "their_espn_face_net": -70.0, "their_ros_vorp_net": -90.0},
    ]
    rec = recommend_band(win_sides, current, min_sample=8)
    assert rec["changed"] is True
    # One-step cap: +20 from current, even if p75 is higher.
    assert rec["recommended"]["max_surplus"] == 40.0
    assert rec["recommended"]["min_their_face"] <= current.min_their_face

    path = tmp_path / "band.json"
    save_band(Band(**{k: rec["recommended"][k] for k in Band().__dict__}), path=path)
    loaded = active_band(path)
    assert loaded.max_surplus == 40.0


def test_player_market_insights_demand_signal():
    sides = [
        {
            "send": ["Emeka Egbuka"],
            "receive": ["Omarion Hampton"],
            "kind": "1:1",
            "blended": 18.9,
            "league_name": "Balls McFee",
        },
        {
            "send": ["C.J. Stroud"],
            "receive": ["Drake Maye"],
            "kind": "1:1",
            "blended": 27.0,
            "league_name": "SKOL",
        },
    ]
    out = player_market_insights(sides, our_names=["Omarion Hampton", "Drake Maye", "Kyren Williams"])
    by_name = {p["name"]: p for p in out["players"]}
    assert by_name["Omarion Hampton"]["signal"] == "demand"
    assert by_name["Drake Maye"]["signal"] == "demand"
    assert out["sell_comps"]
    assert "pricing_note" in out
