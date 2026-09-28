"""Tests for reading settings from the environment, including missing and malformed values."""

import pytest

from accessai import config as config_module
from accessai.config import DEFAULT_ORIGINS, Config


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    monkeypatch.setattr(config_module, "load_dotenv", lambda *a, **k: None)
    for name in ("CORS_ORIGINS", "SCANS_PER_HOUR", "TRUSTED_PROXIES",
                 "RUNS_DIR", "FLASK_DEBUG"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_work_for_local_development():
    config = Config.from_env()
    assert config.cors_origins == list(DEFAULT_ORIGINS)
    assert config.scans_per_hour == 20
    assert config.trusted_proxies == 0
    assert config.debug is False
    assert config.runs_dir.endswith("runs")


def test_values_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example/, https://b.example")
    monkeypatch.setenv("SCANS_PER_HOUR", "7")
    monkeypatch.setenv("TRUSTED_PROXIES", "1")
    monkeypatch.setenv("RUNS_DIR", "/tmp/runs")
    monkeypatch.setenv("FLASK_DEBUG", "true")

    config = Config.from_env()
    assert config.cors_origins == ["https://a.example", "https://b.example"], \
        "trailing slashes should be stripped"
    assert config.scans_per_hour == 7
    assert config.trusted_proxies == 1
    assert config.runs_dir == "/tmp/runs"
    assert config.debug is True


def test_a_malformed_number_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("SCANS_PER_HOUR", "lots")
    assert Config.from_env().scans_per_hour == 20
