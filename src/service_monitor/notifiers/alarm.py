"""Optional alarm-center notifier.

The original scripts escalated critical failures to an internal alarm platform
that placed phone calls to the on-call engineer. That platform is deployment
specific, so this notifier only keeps the generic contract: POST a JSON
document to a URL you control, optionally with a fallback phone number.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from .base import BaseNotifier, NotificationResult


class AlarmCenterNotifier(BaseNotifier):
    """Forward critical alerts to an external alarm/phone-callout service."""

    name = "alarm_center"

    def __init__(
        self,
        url: str = "",
        fallback_number: str = "",
        timeout: float = 10.0,
        level: str = "critical",
        extra: Optional[Dict[str, Any]] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.url = (url or "").strip()
        self.fallback_number = (fallback_number or "").strip()
        self.timeout = float(timeout)
        self.level = level or "critical"
        self.extra = dict(extra or {})
        self._session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return self.url.startswith("http")

    def send(self, text: str, mobiles: Optional[List[str]] = None) -> NotificationResult:
        if not self.enabled:
            return NotificationResult(self.name, False, "alarm center url not configured")

        numbers = [str(m) for m in (mobiles or []) if m]
        if not numbers and self.fallback_number:
            numbers = [self.fallback_number]

        payload: Dict[str, Any] = {"level": self.level, "content": text, "phones": numbers}
        payload.update(self.extra)

        try:
            response = self._session.post(self.url, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            return NotificationResult(self.name, False, f"request failed: {exc}")

        ok = 200 <= response.status_code < 300
        return NotificationResult(
            self.name, ok, f"HTTP {response.status_code}",
            payload={"status_code": response.status_code},
        )


__all__ = ["AlarmCenterNotifier"]