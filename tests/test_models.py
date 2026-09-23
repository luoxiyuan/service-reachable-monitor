"""Unit tests for the shared data models."""

from service_monitor.models import (
    ALERT_STATUSES,
    CheckResult,
    Owner,
    RunReport,
    Status,
    Target,
)


def _target(**kw):
    base = dict(kind="http", environment="env-a", name="svc", detail="https://x")
    base.update(kw)
    return Target(**base)


def test_status_values():
    assert Status.UP.value == "UP"
    assert Status.DOWN in ALERT_STATUSES
    assert Status.ERROR in ALERT_STATUSES
    assert Status.UP not in ALERT_STATUSES


def test_owner_label_prefers_name_then_phone():
    assert Owner(name="alice", phone="13800138000").label == "alice"
    assert Owner(phone="13800138000").label == "13800138000"
    assert Owner().label == "unassigned"


def test_target_identity_is_stable_and_unique():
    a = _target(name="svc", detail="https://x")
    b = _target(name="svc", detail="https://y")
    assert a.identity != b.identity
    assert a.identity == "http:env-a:svc:https://x"


def test_check_result_should_alert_and_summary():
    down = CheckResult(target=_target(), status=Status.DOWN, message="timeout")
    assert down.should_alert is True
    assert down.ok is False
    assert "DOWN" in down.summary_line()
    assert "timeout" in down.summary_line()

    up = CheckResult(target=_target(), status=Status.UP, message="200")
    assert up.should_alert is False
    assert up.ok is True


def test_run_report_counts_and_failures():
    report = RunReport()
    report.add(CheckResult(target=_target(name="a"), status=Status.UP))
    report.add(CheckResult(target=_target(name="b"), status=Status.DOWN))
    report.add(CheckResult(target=_target(name="c"), status=Status.ERROR))
    assert report.count(Status.UP) == 1
    assert len(report.failures) == 2
    assert "total=3" in report.digest()
    assert report.as_dict()["total"] == 3


def test_run_report_extend_and_duration():
    report = RunReport(started_at=100.0, finished_at=102.5)
    report.extend([CheckResult(target=_target(), status=Status.UNKNOWN)])
    assert report.duration_seconds == 2.5
    assert report.count(Status.UNKNOWN) == 1