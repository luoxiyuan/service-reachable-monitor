"""Generic JSON webhook notifier."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from .base import BaseNotifier, NotificationResult


class WebhookNotifier(BaseNotifier):
    """POST a small JSON document to any endpoint you control.

    Useful for forwarding alerts into an existing internal alarm platform
    without writing a dedicated channel.
    """

    name = "webhook"

    def __init__(
        self,
        url: str = "",
        timeout: float = 10.0,
        headers: Optional[Dict[str, str]] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.url = (url or "").strip()
        self.timeout = float(timeout)
        self.headers = {"Content-Type": "application/json"}
        self.headers.update(headers or {})
        self._session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def send(self, text: str, mobiles: Optional[List[str]] = None) -> NotificationResult:
        if not self.enabled:
            return NotificationResult(self.name, False, "no webhook url configured")

        payload: Dict[str, Any] = {"text": text}
        if mobiles:
            payload["mobiles"] = list(mobiles)

        try:
            response = self._session.post(
                self.url, json=payload, timeout=self.timeout, headers=self.headers
            )
        except requests.RequestException as exc:
            return NotificationResult(self.name, False, f"request failed: {_short(exc)}")

        ok = 200 <= response.status_code < 300
        return NotificationResult(
            self.name,
            ok,
            f"HTTP {response.status_code}",
            payload={"status_code": response.status_code},
        )


def _short(exc: BaseException, limit: int = 160) -> str:
    text = " ".join(str(exc).split())
    return text[:limit] + ("..." if len(text) > limit else "")