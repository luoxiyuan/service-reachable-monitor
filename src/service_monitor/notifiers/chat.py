"""Chat-robot notifiers: DingTalk, WeCom (企业微信) and Feishu (飞书).

All three expose the same "post a JSON document to a webhook" shape, but they
differ in payload keys, @-mention semantics and optional request signing. The
original scripts hard-coded one webhook token per robot; here the token is
always supplied through configuration/environment variables.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any, Dict, List, Optional
from urllib import parse

import requests

from .base import BaseNotifier, NotificationResult

#: Chat robots truncate or reject oversized bodies; keep alerts compact.
MAX_TEXT_CHARS = 3800


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n...(truncated)"


def _short(exc: BaseException, limit: int = 160) -> str:
    body = " ".join(str(exc).split())
    return body[:limit] + ("..." if len(body) > limit else "")


class _ChatNotifier(BaseNotifier):
    """Shared HTTP plumbing for webhook style chat robots."""

    name = "chat"
    #: JSON key holding the robot's own error code.
    code_keys = ("errcode", "code", "StatusCode")

    def __init__(
        self,
        webhook: str = "",
        timeout: float = 10.0,
        headers: Optional[Dict[str, str]] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.webhook = (webhook or "").strip()
        self.timeout = float(timeout)
        self.headers = {"Content-Type": "application/json"}
        self.headers.update(headers or {})
        self._session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return self.webhook.startswith("http")

    # -- subclass hooks -------------------------------------------------
    def build_payload(self, text: str, mobiles: Optional[List[str]] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def sign_url(self, url: str) -> str:
        """Return ``url`` unchanged unless the robot needs a signature."""
        return url

    # -- delivery -------------------------------------------------------
    def send(self, text: str, mobiles: Optional[List[str]] = None) -> NotificationResult:
        if not self.enabled:
            return NotificationResult(self.name, False, "webhook not configured")

        payload = self.build_payload(_clip(text), mobiles)
        url = self.sign_url(self.webhook)

        try:
            response = self._session.post(
                url, json=payload, timeout=self.timeout, headers=self.headers
            )
        except requests.RequestException as exc:
            return NotificationResult(self.name, False, f"request failed: {_short(exc)}")

        http_ok = 200 <= response.status_code < 300
        body = _json(response)
        code = _error_code(body)
        ok = http_ok and code == 0

        detail = f"HTTP {response.status_code}"
        if body:
            message = body.get("errmsg") or body.get("msg")
            if message:
                detail += f", {message}"
        if not http_ok:
            return NotificationResult(self.name, False, detail, payload={"body": body})
        if code not in (0, None):
            return NotificationResult(self.name, False, detail, payload={"body": body})
        return NotificationResult(self.name, True, detail, payload={"body": body})


class DingTalkNotifier(_ChatNotifier):
    """DingTalk group robot (钉钉群机器人).

    Supports the optional "signed secret" mode: when ``secret`` is set the
    request URL gains ``timestamp``/``sign`` query parameters computed with
    HMAC-SHA256, exactly as the official robot protocol requires.
    """

    name = "dingtalk"

    def __init__(
        self,
        webhook: str = "",
        secret: str = "",
        at_all: bool = False,
        at_mobiles: Optional[List[str]] = None,
        timeout: float = 10.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        super().__init__(webhook=webhook, timeout=timeout, session=session)
        self.secret = (secret or "").strip()
        self.at_all = bool(at_all)
        self.at_mobiles = [str(m) for m in (at_mobiles or []) if str(m).strip()]

    @property
    def enabled(self) -> bool:
        return super().enabled and "REPLACE_ME" not in self.webhook

    def sign_url(self, url: str) -> str:
        if not self.secret:
            return url
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        digest = hmac.new(
            self.secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
        ).digest()
        sign = parse.quote_plus(base64.b64encode(digest))
        joiner = "&" if "?" in url else "?"
        return f"{url}{joiner}timestamp={timestamp}&sign={sign}"

    def build_payload(self, text: str, mobiles: Optional[List[str]] = None) -> Dict[str, Any]:
        mentions = list(dict.fromkeys(self.at_mobiles + [str(m) for m in (mobiles or []) if m]))
        return {
            "msgtype": "text",
            "text": {"content": text},
            "at": {"atMobiles": mentions, "isAtAll": self.at_all},
        }


class WeComNotifier(_ChatNotifier):
    """WeCom / 企业微信 group robot."""

    name = "wecom"

    def __init__(
        self,
        webhook: str = "",
        at_all: bool = False,
        at_mobiles: Optional[List[str]] = None,
        timeout: float = 10.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        super().__init__(webhook=webhook, timeout=timeout, session=session)
        self.at_all = bool(at_all)
        self.at_mobiles = [str(m) for m in (at_mobiles or []) if str(m).strip()]

    @property
    def enabled(self) -> bool:
        return super().enabled and "REPLACE_ME" not in self.webhook

    def build_payload(self, text: str, mobiles: Optional[List[str]] = None) -> Dict[str, Any]:
        mentions = list(dict.fromkeys(self.at_mobiles + [str(m) for m in (mobiles or []) if m]))
        if self.at_all:
            mentions = ["@all"]
        return {
            "msgtype": "text",
            "text": {"content": text},
            "mentioned_mobile_list": mentions or None,
        }


class FeishuNotifier(_ChatNotifier):
    """Feishu / 飞书 group robot.

    When ``secret`` is set the payload carries the ``timestamp``/``sign`` pair
    required by signature-verified bots.
    """

    name = "feishu"

    def __init__(
        self,
        webhook: str = "",
        secret: str = "",
        timeout: float = 10.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        super().__init__(webhook=webhook, timeout=timeout, session=session)
        self.secret = (secret or "").strip()

    @property
    def enabled(self) -> bool:
        return super().enabled and "REPLACE_ME" not in self.webhook

    def build_payload(self, text: str, mobiles: Optional[List[str]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
        if self.secret:
            timestamp = str(int(time.time()))
            string_to_sign = f"{timestamp}\n{self.secret}"
            digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
            payload["timestamp"] = timestamp
            payload["sign"] = base64.b64encode(digest).decode("utf-8")
        return payload


def _json(response: requests.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_code(body: Dict[str, Any]) -> Optional[int]:
    for key in _ChatNotifier.code_keys:
        if key in body:
            try:
                return int(body[key])
            except (TypeError, ValueError):
                return None
    return None


__all__ = ["DingTalkNotifier", "WeComNotifier", "FeishuNotifier"]