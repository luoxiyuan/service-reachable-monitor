"""Scheduler cron parsing tests."""

import pytest

from service_monitor.scheduler import DEFAULT_CRON, parse_cron


def test_parse_cron_default_when_empty():
    assert parse_cron(None) == DEFAULT_CRON
    assert parse_cron("") == DEFAULT_CRON


def test_parse_cron_five_fields():
    result = parse_cron("*/10 10,14,16,18 * * mon-fri")
    assert result["minute"] == "*/10"
    assert result["hour"] == "10,14,16,18"
    assert result["day_of_week"] == "mon-fri"


def test_parse_cron_rejects_wrong_field_count():
    with pytest.raises(ValueError):
        parse_cron("*/10 10 *")


def test_default_cron_matches_legacy_window():
    assert DEFAULT_CRON["day_of_week"] == "mon-fri"
    assert DEFAULT_CRON["hour"] == "10,14,16,18"
    assert DEFAULT_CRON["minute"] == "0/10"


def test_start_scheduler_without_apscheduler_raises_clear_error(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("apscheduler"):
            raise ImportError("no apscheduler")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from service_monitor.scheduler import start_scheduler
    with pytest.raises(RuntimeError, match="APScheduler"):
        start_scheduler(lambda: None)