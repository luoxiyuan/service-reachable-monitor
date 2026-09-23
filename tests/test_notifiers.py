"""Notifier tests with a fake HTTP session (no real webhooks)."""

import io
import json

import pytest

from service_monitor.models import CheckResult, Owner, Status, Target
from service_monitor.notifiers import (
    AlarmCenterNotifier,
    ConsoleNotifier,
    DingTalkNotifier,
    FeishuNotifier,
    NotificationManager,
    RateLimiter,
    WebhookNotifier,
    WeComNotifier,
    build_notifier,
    collect_mobiles,
    format_failures,
    parse_notify,
)


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"errcode": 0, "errmsg": "ok"}

    def json(self):
        if isinstance(self._body, ValueError):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.posts = []

    def post(self, url, json=None, timeout=None, headers=None, **kw):
        self.posts.append({"url": url, "json": json, "headers": headers})
        outcome = self.outcomes.pop(0) if self.outcomes else FakeResponse()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _failure(name="svc", phone="13800138000"):
    return CheckResult(
        target=Target(kind="http", environment="env-a", name=name,
                      detail="https://x", owner=Owner(phone=phone)),
        status=Status.DOWN, message="timeout",
    )


# --- console ---------------------------------------------------------------
def test_console_notifier_writes_text():
    buf = io.StringIO()
    result = ConsoleNotifier(stream=buf).send("hello")
    assert result.ok
    assert "hello" in buf.getvalue()


# --- dingtalk --------------------------------------------------------------
def test_dingtalk_disabled_without_webhook():
    assert DingTalkNotifier(webhook="").enabled is False
    assert DingTalkNotifier(webhook="https://x?access_token=REPLACE_ME").enabled is False


def test_dingtalk_send_payload_and_mentions():
    session = FakeSession([FakeResponse(200, {"errcode": 0, "errmsg": "ok"})])
    notifier = DingTalkNotifier(webhook="https://oapi.example.com/robot/send?access_token=t",
                                session=session)
    result = notifier.send("alert", mobiles=["13800138000"])
    assert result.ok
    payload = session.posts[0]["json"]
    assert payload["msgtype"] == "text"
    assert payload["text"]["content"] == "alert"
    assert payload["at"]["atMobiles"] == ["13800138000"]


def test_dingtalk_signed_secret_appends_signature():
    session = FakeSession([FakeResponse(200)])
    notifier = DingTalkNotifier(webhook="https://oapi.example.com/robot/send?access_token=t",
                                secret="SECdummy", session=session)
    notifier.send("x")
    url = session.posts[0]["url"]
    assert "timestamp=" in url and "sign=" in url


def test_dingtalk_errcode_nonzero_is_failure():
    session = FakeSession([FakeResponse(200, {"errcode": 310000, "errmsg": "keyword not in content"})])
    result = DingTalkNotifier(webhook="https://oapi.example.com/robot/send?access_token=t",
                              session=session).send("x")
    assert result.ok is False
    assert "keyword not in content" in result.detail


def test_dingtalk_http_error_is_failure():
    session = FakeSession([FakeResponse(500, {"errcode": 0})])
    result = DingTalkNotifier(webhook="https://oapi.example.com/robot/send?access_token=t",
                              session=session).send("x")
    assert result.ok is False


def test_dingtalk_request_exception_is_failure():
    import requests
    session = FakeSession([requests.exceptions.ConnectionError("down")])
    result = DingTalkNotifier(webhook="https://oapi.example.com/robot/send?access_token=t",
                              session=session).send("x")
    assert result.ok is False
    assert "request failed" in result.detail


def test_dingtalk_long_text_is_truncated():
    session = FakeSession([FakeResponse(200)])
    DingTalkNotifier(webhook="https://oapi.example.com/robot/send?access_token=t",
                     session=session).send("x" * 9000)
    content = session.posts[0]["json"]["text"]["content"]
    assert len(content) <= 3800
    assert content.endswith("...(truncated)")


# --- wecom / feishu --------------------------------------------------------
def test_wecom_payload_uses_mentioned_mobile_list():
    session = FakeSession([FakeResponse(200, {"errcode": 0, "errmsg": "ok"})])
    WeComNotifier(webhook="https://qyapi.example.com/cgi-bin/webhook/send?key=k",
                  session=session).send("alert", mobiles=["139"])
    payload = session.posts[0]["json"]
    assert payload["mentioned_mobile_list"] == ["139"]


def test_wecom_at_all():
    session = FakeSession([FakeResponse(200)])
    WeComNotifier(webhook="https://qyapi.example.com/cgi-bin/webhook/send?key=k",
                  at_all=True, session=session).send("alert")
    assert session.posts[0]["json"]["mentioned_mobile_list"] == ["@all"]


def test_feishu_payload_shape():
    session = FakeSession([FakeResponse(200, {"code": 0, "msg": "success"})])
    FeishuNotifier(webhook="https://open.example-im.com/open-apis/bot/v2/hook/t",
                   session=session).send("alert")
    payload = session.posts[0]["json"]
    assert payload["msg_type"] == "text"
    assert payload["content"]["text"] == "alert"


def test_feishu_secret_adds_signature_fields():
    session = FakeSession([FakeResponse(200)])
    FeishuNotifier(webhook="https://open.example-im.com/open-apis/bot/v2/hook/t",
                   secret="s3cr3t", session=session).send("alert")
    payload = session.posts[0]["json"]
    assert "timestamp" in payload and "sign" in payload


# --- webhook / alarm center ------------------------------------------------
def test_webhook_posts_json_document():
    session = FakeSession([FakeResponse(204, {})])
    result = WebhookNotifier(url="https://alarm.example.com/hook", session=session).send(
        "alert", mobiles=["138"])
    assert result.ok
    assert session.posts[0]["json"] == {"text": "alert", "mobiles": ["138"]}


def test_webhook_disabled_without_url():
    assert WebhookNotifier(url="").enabled is False
    assert WebhookNotifier(url="").send("x").ok is False


def test_alarm_center_uses_fallback_number():
    session = FakeSession([FakeResponse(200, {})])
    notifier = AlarmCenterNotifier(url="https://alarm.example.com/call",
                                   fallback_number="13700137000", session=session)
    notifier.send("critical")
    assert session.posts[0]["json"]["phones"] == ["13700137000"]
    assert session.posts[0]["json"]["level"] == "critical"


# --- helpers ---------------------------------------------------------------
def test_format_failures_and_collect_mobiles():
    results = [_failure("a", "138"), _failure("b", "139"), _failure("c", "138")]
    text = format_failures(results, "Title")
    assert text.startswith("Title")
    assert text.count("- ") == 3
    assert collect_mobiles(results) == ["138", "139"]


# --- rate limiter ------------------------------------------------------------
def test_rate_limiter_blocks_after_budget():
    slept = []
    clock = [1000.0]

    def fake_clock():
        return clock[0]

    def fake_sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    limiter = RateLimiter(max_per_minute=2, sleep=fake_sleep, clock=fake_clock)
    limiter.acquire()
    limiter.acquire()
    assert slept == []
    limiter.acquire()  # third call within the same minute must sleep
    assert len(slept) == 1
    assert 0 < slept[0] <= 61


def test_rate_limiter_window_slides():
    clock = [0.0]
    limiter = RateLimiter(max_per_minute=1, sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                          clock=lambda: clock[0])
    limiter.acquire()
    clock[0] += 61  # window expired
    before = clock[0]
    limiter.acquire()
    assert clock[0] == before  # no sleep needed


# --- build_notifier / parse_notify ------------------------------------------
def test_build_notifier_unknown_type_returns_none():
    assert build_notifier({"type": "smoke-signal"}) is None


def test_build_notifier_disabled_returns_none():
    assert build_notifier({"type": "console", "enabled": False}) is None


def test_build_notifier_dingtalk():
    notifier = build_notifier({"type": "dingtalk", "webhook": "https://oapi.example.com/r?access_token=t"})
    assert isinstance(notifier, DingTalkNotifier)


def test_parse_notify_defaults_to_console():
    settings = parse_notify({})
    assert [c.name for c in settings.channels] == ["console"]
    assert settings.batch_size == 5


def test_parse_notify_skips_unconfigured_channels():
    settings = parse_notify({
        "channels": [
            {"type": "dingtalk", "enabled": True, "webhook": ""},
            {"type": "console", "enabled": True},
        ]
    })
    assert [c.name for c in settings.channels] == ["console"]


def test_parse_notify_alarm_center():
    settings = parse_notify({
        "channels": [{"type": "console"}],
        "alarm_center": {"enabled": True, "url": "https://alarm.example.com/call",
                         "fallback_number": "13700137000"},
    })
    assert settings.alarm_center is not None
    assert settings.alarm_center.fallback_number == "13700137000"


def test_manager_batches_and_alerts_only_failures():
    session = FakeSession([FakeResponse(200)] * 10)
    notifier = DingTalkNotifier(webhook="https://oapi.example.com/robot/send?access_token=t",
                                session=session)
    settings = parse_notify({})
    settings.channels = [notifier]
    settings.batch_size = 2
    manager = NotificationManager(settings)

    up = CheckResult(target=Target(kind="http", environment="e", name="ok", detail="u"),
                     status=Status.UP)
    failures = [_failure(f"f{i}") for i in range(5)]
    outcomes = manager.notify_results([up] + failures)
    # 5 failures / batch 2 -> 3 messages
    assert len(session.posts) == 3
    assert all(o.ok for o in outcomes)


def test_manager_no_failures_sends_nothing():
    session = FakeSession()
    settings = parse_notify({})
    settings.channels = [DingTalkNotifier(webhook="https://oapi.example.com/robot/send?access_token=t",
                                          session=session)]
    up = CheckResult(target=Target(kind="http", environment="e", name="ok", detail="u"),
                     status=Status.UP)
    assert NotificationManager(settings).notify_results([up]) == []
    assert session.posts == []


def test_manager_mentions_owner_mobiles():
    session = FakeSession([FakeResponse(200)])
    settings = parse_notify({})
    settings.channels = [DingTalkNotifier(webhook="https://oapi.example.com/robot/send?access_token=t",
                                          session=session)]
    NotificationManager(settings).notify_results([_failure(phone="13800138000")])
    assert session.posts[0]["json"]["at"]["atMobiles"] == ["13800138000"]


def test_manager_also_calls_alarm_center():
    session = FakeSession([FakeResponse(200)] * 5)
    settings = parse_notify({
        "channels": [{"type": "console"}],
        "alarm_center": {"enabled": True, "url": "https://alarm.example.com/call"},
    })
    settings.alarm_center._session = session
    outcomes = NotificationManager(settings).notify_results([_failure()])
    assert any(o.channel == "alarm_center" for o in outcomes)
    assert session.posts[0]["json"]["level"] == "critical"