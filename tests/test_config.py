import pytest

from league_manager.config import ConfigError, auth_status, load_settings


def test_auth_status_reports_missing(monkeypatch):
    monkeypatch.delenv("ESPN_S2", raising=False)
    monkeypatch.delenv("ESPN_SWID", raising=False)
    monkeypatch.delenv("ESPN_LEAGUE_ID", raising=False)
    monkeypatch.setattr("league_manager.config.load_dotenv_files", lambda: None)
    status = auth_status()
    assert status["ready"] is False
    assert "ESPN_S2" in status["missing"]


def test_load_settings_requires_secrets(monkeypatch):
    monkeypatch.delenv("ESPN_S2", raising=False)
    monkeypatch.delenv("ESPN_SWID", raising=False)
    monkeypatch.delenv("ESPN_LEAGUE_ID", raising=False)
    monkeypatch.setattr("league_manager.config.load_dotenv_files", lambda: None)
    with pytest.raises(ConfigError, match="Missing required ESPN credentials"):
        load_settings()


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setattr("league_manager.config.load_dotenv_files", lambda: None)
    monkeypatch.setenv("ESPN_S2", "s2-token")
    monkeypatch.setenv("ESPN_SWID", "{ABC-123}")
    monkeypatch.setenv("ESPN_LEAGUE_ID", "424242")
    monkeypatch.setenv("ESPN_TEAM_ID", "3")
    monkeypatch.setenv("ESPN_SEASON", "2026")
    monkeypatch.setenv("ESPN_SPORT", "nfl")
    monkeypatch.delenv("ESPN_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("ESPN_DRY_RUN", raising=False)
    settings = load_settings()
    assert settings.league_id == 424242
    assert settings.team_id == 3
    assert settings.season == 2026
    assert settings.espn_sport_code == "ffl"
    assert settings.cookies == {"espn_s2": "s2-token", "SWID": "{ABC-123}"}
    assert settings.dry_run is True
    assert settings.writes_enabled is False


def test_invalid_league_id(monkeypatch):
    monkeypatch.setattr("league_manager.config.load_dotenv_files", lambda: None)
    monkeypatch.setenv("ESPN_S2", "s2")
    monkeypatch.setenv("ESPN_SWID", "{X}")
    monkeypatch.setenv("ESPN_LEAGUE_ID", "not-a-number")
    with pytest.raises(ConfigError, match="integer"):
        load_settings()
