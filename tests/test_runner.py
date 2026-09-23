"""Runner orchestration tests with stub checkers."""

from service_monitor.config import EnvironmentConfig, MonitorConfig, HttpTargetConfig
from service_monitor.models import CheckResult, Status, Target
from service_monitor.notifiers.base import BaseNotifier, NotificationResult
from service_monitor.runner import MonitorRunner, build_runner


class StubChecker:
    """Returns a canned result per environment for a given kind."""

    def __init__(self, kind, status, message="stub"):
        self.kind = kind
        self.status = status
        self.message = message
        self.seen = []

    def check_environment(self, environment):
        self.seen.append(environment.name)
        target = Target(kind=self.kind, environment=environment.name,
                        name=f"{environment.name}-{self.kind}", detail="d")
        return [CheckResult(target=target, status=self.status, message=self.message)]


class CrashChecker:
    kind = "crash"

    def check_environment(self, environment):
        raise RuntimeError("boom")


class RecordingNotifier:
    """Captures notify_results calls."""

    def __init__(self):
        self.calls = []

    def notify_results(self, results):
        self.calls.append(results)
        return [NotificationResult("recorder", True, "ok")]


def _config(*names):
    return MonitorConfig(environments=[
        EnvironmentConfig(name=n, label=n,
                          http_targets=[HttpTargetConfig(name="t", url="https://t", environment=n)])
        for n in names
    ])


def test_run_all_environments():
    config = _config("a", "b")
    http = StubChecker("http", Status.UP)
    runner = MonitorRunner(config, checkers=[http])
    report = runner.run()
    assert len(report.results) == 2
    assert http.seen == ["a", "b"]
    assert report.count(Status.UP) == 2


def test_run_respects_env_selection():
    config = _config("a", "b")
    runner = MonitorRunner(config, checkers=[StubChecker("http", Status.UP)])
    report = runner.run(environments=["b"])
    assert [r.target.environment for r in report.results] == ["b"]


def test_run_filters_by_kind():
    config = _config("a")
    http = StubChecker("http", Status.UP)
    dubbo = StubChecker("dubbo", Status.DOWN)
    runner = MonitorRunner(config, checkers=[http, dubbo])
    report = runner.run(kinds=["http"])
    assert [r.target.kind for r in report.results] == ["http"]
    assert dubbo.seen == []


def test_run_notifies_on_failures():
    config = _config("a")
    notifier = RecordingNotifier()
    runner = MonitorRunner(config, notify=notifier, checkers=[StubChecker("http", Status.DOWN)])
    report = runner.run()
    assert len(report.failures) == 1
    assert len(notifier.calls) == 1
    assert notifier.calls[0] == report.results


def test_run_no_alert_skips_notifier():
    config = _config("a")
    notifier = RecordingNotifier()
    runner = MonitorRunner(config, notify=notifier, checkers=[StubChecker("http", Status.DOWN)])
    runner.run(alert=False)
    assert notifier.calls == []


def test_checker_crash_becomes_error_result():
    config = _config("a")
    runner = MonitorRunner(config, checkers=[CrashChecker()])
    report = runner.run()
    assert report.results[0].status is Status.ERROR
    assert "crashed" in report.results[0].message


def test_build_runner_wires_manager():
    config = _config("a")
    from service_monitor.notifiers.manager import parse_notify
    settings = parse_notify({"channels": [{"type": "console"}]})
    runner = build_runner(config, settings)
    assert runner.notify is not None
    # build_runner injects the real checkers; override with a stub to avoid I/O.
    runner.checkers = [StubChecker("http", Status.UP)]
    report = runner.run()
    assert report.count(Status.UP) == 1


def test_build_runner_without_settings_has_no_notifier():
    runner = build_runner(_config("a"))
    assert runner.notify is None


def test_inventory_dubbo_uses_real_checker_safely():
    # No dubbo configured -> empty inventory, no crash.
    config = _config("a")
    runner = MonitorRunner(config)
    assert runner.inventory_dubbo() == []