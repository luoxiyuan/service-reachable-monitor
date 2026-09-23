"""Report rendering tests."""

import json

from service_monitor.models import CheckResult, Owner, RunReport, Status, Target
from service_monitor.reporting import render_table, to_json, write_json


def _report():
    r = RunReport(started_at=0.0, finished_at=1.5)
    r.add(CheckResult(target=Target(kind="http", environment="env-a", name="api",
                                    detail="https://api", owner=Owner(name="alice")),
                      status=Status.UP, message="200 in 12ms"))
    r.add(CheckResult(target=Target(kind="dubbo", environment="env-a", name="com.x.Demo",
                                    detail="com.x.Demo", owner=Owner(phone="138")),
                      status=Status.DOWN, message="no provider"))
    return r


def test_render_table_has_header_and_rows():
    text = render_table(_report().results)
    assert "TARGET" in text
    assert "api" in text
    assert "DOWN" in text


def test_render_table_empty():
    assert render_table([]) == "(no targets checked)"


def test_to_json_is_valid_and_complete():
    data = json.loads(to_json(_report()))
    assert data["total"] == 2
    assert data["up"] == 1 and data["down"] == 1
    assert len(data["results"]) == 2


def test_write_json_creates_file(tmp_path):
    out = tmp_path / "nested" / "report.json"
    path = write_json(_report(), out)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["total"] == 2