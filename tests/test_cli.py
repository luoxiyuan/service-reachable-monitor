"""CLI smoke tests using example config files (no network)."""

import json

import pytest

from service_monitor.cli import EXIT_FAILURES, EXIT_OK, EXIT_USAGE, build_parser, main


@pytest.fixture
def cfg_files(tmp_path):
    import textwrap
    targets = tmp_path / "targets.yaml"
    targets.write_text(textwrap.dedent("""
        environments:
          - name: env-a
            label: Env A
            http_targets:
              - name: api
                url: https://api.example.invalid/health
    """), encoding="utf-8")
    notify = tmp_path / "notify.yaml"
    notify.write_text("channels:\n  - type: console\n", encoding="utf-8")
    return targets, notify


def test_parser_defaults():
    from service_monitor.cli import _apply_defaults
    args = _apply_defaults(build_parser().parse_args([]))
    assert args.command is None
    assert args.config == "targets.yaml"
    assert args.notify_config == "notify.yaml"
    assert args.env == [] and args.kind == []
    assert args.include_disabled is False


def test_global_flags_accepted_after_subcommand():
    from service_monitor.cli import _apply_defaults
    args = _apply_defaults(build_parser().parse_args(
        ["run", "-c", "t.yaml", "-e", "env-a", "--no-alert", "--quiet"]))
    assert args.command == "run"
    assert args.config == "t.yaml"
    assert args.env == ["env-a"]
    assert args.no_alert is True


def test_global_flags_accepted_before_subcommand():
    from service_monitor.cli import _apply_defaults
    args = _apply_defaults(build_parser().parse_args(
        ["-c", "t.yaml", "run", "-e", "env-b"]))
    assert args.config == "t.yaml"
    assert args.env == ["env-b"]


def test_list_envs(cfg_files, capsys):
    targets, notify = cfg_files
    code = main(["-c", str(targets), "-n", str(notify), "list-envs"])
    assert code == EXIT_OK
    assert "env-a" in capsys.readouterr().out


def test_doctor_ok(cfg_files, capsys):
    targets, notify = cfg_files
    code = main(["-c", str(targets), "-n", str(notify), "doctor"])
    # No dubbo configured, so kazoo is not required -> doctor passes.
    assert code == EXIT_OK
    assert "configuration looks good" in capsys.readouterr().out


def test_missing_config_is_usage_error(tmp_path, capsys):
    code = main(["-c", str(tmp_path / "nope.yaml"), "list-envs"])
    assert code == EXIT_USAGE
    assert "config error" in capsys.readouterr().err


def test_run_unreachable_host_reports_failure(cfg_files, monkeypatch):
    """Force the HTTP layer to fail fast so no real network call happens."""
    import requests
    from service_monitor.checkers import http_checker

    def fake_request(self, **kwargs):
        raise requests.exceptions.ConnectionError("no network in tests")

    monkeypatch.setattr(http_checker.requests.Session, "request", fake_request)
    targets, notify = cfg_files
    code = main(["-c", str(targets), "-n", str(notify), "run", "--no-alert", "--quiet"])
    assert code == EXIT_FAILURES


def test_run_writes_json_report(cfg_files, tmp_path, monkeypatch):
    import requests
    from service_monitor.checkers import http_checker

    class R:
        status_code = 200
        text = "ok"

    monkeypatch.setattr(http_checker.requests.Session, "request",
                        lambda self, **kw: R())
    targets, notify = cfg_files
    out = tmp_path / "report.json"
    code = main(["-c", str(targets), "-n", str(notify), "run",
                 "--no-alert", "--json", str(out)])
    assert code == EXIT_OK
    assert json.loads(out.read_text(encoding="utf-8"))["total"] == 1