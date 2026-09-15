import json

from league_manager.cli import main
from league_manager.config import Settings
from league_manager.espn_client import EspnClient


class FakePlayer:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeTeam:
    def __init__(self, team_id=7, name="Test Squad", standing=3, wins=2, losses=1, roster=None, owners=None):
        self.team_id = team_id
        self.team_name = name
        self.team_abbrev = "TST"
        self.wins = wins
        self.losses = losses
        self.ties = 0
        self.points_for = 240.5
        self.points_against = 210.0
        self.standing = standing
        self.owners = owners if owners is not None else [{"id": "{SWID}"}]
        self.roster = roster or [
            FakePlayer(
                playerId=11,
                name="Starter RB",
                position="RB",
                lineupSlotId=2,
                projected_points=15,
                injured=False,
                proTeam="DET",
                stats={
                    1: {"points": 28.0, "projected_points": 15.0},
                    2: {"points": 31.0, "projected_points": 15.0},
                },
            ),
            FakePlayer(
                playerId=12,
                name="Bench RB",
                position="RB",
                lineupSlotId=20,
                projected_points=8,
                injured=False,
                proTeam="CHI",
            ),
        ]


class FakeSettings:
    name = "Agent League"
    team_count = 10
    playoff_team_count = 4
    scoring_type = "ppr"
    reg_season_count = 14


class FakeLeague:
    def __init__(self):
        self.settings = FakeSettings()
        self.current_week = 3
        self.nfl_week = 3
        self.teams = [
            FakeTeam(),
            FakeTeam(
                team_id=8,
                name="Other Squad",
                standing=8,
                wins=1,
                losses=2,
                owners=[{"id": "{OTHER}"}],
                roster=[
                    FakePlayer(
                        playerId=21,
                        name="WR1",
                        position="WR",
                        lineupSlotId=4,
                        projected_points=18,
                        injured=False,
                        proTeam="CIN",
                        stats={
                            1: {"points": 5.0, "projected_points": 18.0},
                            2: {"points": 6.0, "projected_points": 18.0},
                        },
                    )
                ],
            ),
        ]

    def standings(self):
        return self.teams

    def scoreboard(self, week=None):
        class Game:
            home_team = FakeTeam()
            away_team = FakeTeam()
            home_score = 90
            away_score = 80

        return [Game()]

    def free_agents(self, week=None, size=50, position=None):
        return [
            FakePlayer(
                playerId=99,
                name="FA RB",
                position=position or "RB",
                projected_points=13,
                percent_owned=42,
            )
        ]

    def player_info(self, name=None, playerId=None):
        return FakePlayer(playerId=5, name=name, position="WR", projected_avg_points=11)

    def recent_activity(self, size=25, msg_type=None):
        return []


def test_cli_reads_with_fake_league(monkeypatch, capsys):
    settings = Settings(
        league_id=1,
        espn_s2="s2",
        swid="{SWID}",
        season=2026,
        team_id=7,
    )
    monkeypatch.setattr("league_manager.cli.load_settings", lambda: settings)
    monkeypatch.setattr(
        "league_manager.cli.EspnClient",
        lambda _settings: EspnClient(_settings, league=FakeLeague()),
    )
    monkeypatch.setattr(
        "league_manager.espn_client.EspnClient._enrich_players",
        lambda self, players, **kwargs: players,
    )
    monkeypatch.setattr(
        "league_manager.cli._attach_sleeper_week",
        lambda players, client: players,
    )

    assert main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["name"] == "Agent League"
    assert status["your_team"]["id"] == 7

    assert main(["roster"]) == 0
    roster = json.loads(capsys.readouterr().out)
    assert roster["roster"][0]["name"] == "Starter RB"

    assert main(["lineup-advice"]) == 0
    advice = json.loads(capsys.readouterr().out)
    assert "recommended" in advice

    assert main(["add", "--player", "99", "--drop", "12"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["executed"] is False
    assert preview["payload"]["type"] == "FREEAGENT"
    assert preview["grade"]["window"]
    assert "blended" in preview["grade"]
    assert preview["grade"]["receive"][0]["id"] == 99
    assert preview["grade"]["send"][0]["id"] == 12

    assert main(["claim", "--player", "99", "--drop", "12"]) == 0
    claim = json.loads(capsys.readouterr().out)
    assert claim["executed"] is False
    assert claim["payload"]["type"] == "WAIVER"
    assert claim["grade"]["blended"] is not None
    assert "roster" in claim["grade"]
    assert "lineup" in claim["grade"]

    assert main(["waiver-advice", "--limit", "3"]) == 0
    waivers = json.loads(capsys.readouterr().out)
    assert waivers["window"]
    assert "st" in waivers["weights"]
    assert "lt" in waivers["weights"]
    assert "roster" in waivers["weights"]
    assert "lineup" in waivers["weights"]
    assert waivers["targets"][0]["id"] == 99
    assert waivers["targets"][0]["drop_candidate_id"] == 12
    assert "blended" in waivers["targets"][0]
    assert "roster" in waivers["targets"][0]
    assert "lineup" in waivers["targets"][0]

    assert main(["values"]) == 0
    values = json.loads(capsys.readouterr().out)
    assert values["players"][0]["st_vorp"] is not None
    assert "lt_vorp" in values["players"][0]

    assert main(["trade-grade", "--send", "11", "--receive", "21"]) == 0
    grade = json.loads(capsys.readouterr().out)
    assert grade["verdict"]
    assert grade["send"][0]["id"] == 11
    assert grade["receive"][0]["id"] == 21

    assert main(["opportunities"]) == 0
    market = json.loads(capsys.readouterr().out)
    assert market["sell_high"][0]["id"] == 11
    assert market["buy_low"][0]["id"] == 21

    assert main(["trade-search", "--kinds", "1:1", "--limit", "5"]) == 0
    searched = json.loads(capsys.readouterr().out)
    assert searched["considered"] >= 1
    assert "trades" in searched
    assert searched["sources"]["espn_remainder"] is True


def test_cli_auth_status_without_secrets(monkeypatch, capsys):
    monkeypatch.delenv("ESPN_S2", raising=False)
    monkeypatch.delenv("ESPN_SWID", raising=False)
    monkeypatch.delenv("ESPN_LEAGUE_ID", raising=False)
    monkeypatch.setattr("league_manager.config.load_dotenv_files", lambda: None)
    assert main(["auth-status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False
