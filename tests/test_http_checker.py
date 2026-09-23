"""HTTP checker tests using a fake requests.Session (no network)."""

import pytest
import requests

from service_monitor.checkers.http_checker import HttpChecker
from service_monitor.config import HttpTargetConfig, EnvironmentConfig
from service_monitor.models import Owner, Status


class FakeResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class FakeSession:
    """Records calls and returns queued responses/exceptions."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else FakeResponse()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _cfg(url="https://api.example.com/health", **params):
    return HttpTargetConfig(
        name="api", url=url, environment="env-a", env_label="Env A",
        owner=Owner(phone="13800138000"), params=params or {"retries": 0},
    )


def test_up_on_expected_status():
    session = FakeSession([FakeResponse(200, "UP")])
    result = HttpChecker(session=session).check_target(_cfg())
    assert result.status is Status.UP
    assert result.meta["http_status"] == 200
    assert session.calls[0]["method"] == "GET"


def test_down_on_unexpected_status():
    session = FakeSession([FakeResponse(503)])
    result = HttpChecker(session=session).check_target(_cfg(retries=0))
    assert result.status is Status.DOWN
    assert "503" in result.message


def test_custom_expect_status():
    session = FakeSession([FakeResponse(204)])
    result = HttpChecker(session=session).check_target(_cfg(retries=0, expect_status=[204]))
    assert result.status is Status.UP


def test_expect_body_mismatch_is_down():
    session = FakeSession([FakeResponse(200, '{"status":"DOWN"}')])
    result = HttpChecker(session=session).check_target(
        _cfg(retries=0, expect_body='"status":"UP"')
    )
    assert result.status is Status.DOWN
    assert "body mismatch" in result.message


def test_expect_body_match_is_up():
    session = FakeSession([FakeResponse(200, '{"status":"UP"}')])
    result = HttpChecker(session=session).check_target(
        _cfg(retries=0, expect_body='"status":"UP"')
    )
    assert result.status is Status.UP


def test_timeout_is_down():
    session = FakeSession([requests.exceptions.Timeout("t")])
    result = HttpChecker(session=session).check_target(_cfg(retries=0))
    assert result.status is Status.DOWN
    assert "timeout" in result.message


def test_connection_error_is_down():
    session = FakeSession([requests.exceptions.ConnectionError("c")])
    result = HttpChecker(session=session).check_target(_cfg(retries=0))
    assert result.status is Status.DOWN
    assert "connection error" in result.message


def test_generic_request_error_is_error_status():
    session = FakeSession([requests.exceptions.RequestException("boom")])
    result = HttpChecker(session=session).check_target(_cfg(retries=0))
    assert result.status is Status.ERROR


def test_retries_until_success():
    session = FakeSession([
        requests.exceptions.ConnectionError("first"),
        FakeResponse(200),
    ])
    result = HttpChecker(session=session).check_target(_cfg(retries=1))
    assert result.status is Status.UP
    assert result.attempts == 2
    assert len(session.calls) == 2


def test_retries_exhausted_reports_last_failure():
    session = FakeSession([
        requests.exceptions.Timeout("1"),
        requests.exceptions.Timeout("2"),
    ])
    result = HttpChecker(session=session).check_target(_cfg(retries=1))
    assert result.status is Status.DOWN
    assert result.attempts == 2


def test_method_and_headers_are_passed_through():
    session = FakeSession([FakeResponse(200)])
    HttpChecker(session=session).check_target(
        _cfg(retries=0, method="POST", headers={"X-Test": "1"})
    )
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["headers"] == {"X-Test": "1"}


def test_check_environment_probes_all_targets():
    env = EnvironmentConfig(
        name="env-a", label="Env A",
        http_targets=[
            _cfg(url="https://a"), _cfg(url="https://b"),
        ],
    )
    session = FakeSession([FakeResponse(200), FakeResponse(500)])
    results = HttpChecker(session=session).check_environment(env)
    assert [r.status for r in results] == [Status.UP, Status.DOWN]


def test_owner_and_env_metadata_propagate():
    session = FakeSession([FakeResponse(200)])
    result = HttpChecker(session=session).check_target(_cfg())
    assert result.target.owner.phone == "13800138000"
    assert result.target.env_label == "Env A"
    assert result.target.kind == "http"