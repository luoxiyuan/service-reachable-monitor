"""Notification manager: channel selection, batching and rate limiting.

Reproduces the throttling behaviour of the original monitor scripts (chat
robots reject bursts, so failures are merged into batches and the sender
pauses when a per-minute budget is exhausted) but keeps every endpoint
configurable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import ConfigError, interpolate, load_dotenv, read_yaml, resolve_path
from ..models import CheckResult
from .alarm import AlarmCenterNotifier
from .base import BaseNotifier, NotificationResult, collect_mobiles, format_failures
from .chat import DingTalkNotifier, FeishuNotifier, WeComNotifier
from .console import ConsoleNotifier
from .webhook import WebhookNotifier

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------
class RateLimiter:
    """Simple sliding-window limiter.

    ``acquire()`` blocks until sending one more message stays within
    ``max_per_minute``. Mirrors the "sleep 60s when the robot budget runs out"
    logic of the original DingTalk sender.
    """

    def __init__(self, max_per_minute: int = 20, sleep: Any = time.sleep, clock: Any = time.monotonic) -> None:
        self.max_per_minute = max(1, int(max_per_minute))
        self._sleep = sleep
        self._clock = clock
        self._stamps: List[float] = []

    def acquire(self) -> None:
        if self.max_per_minute <= 0:
            return
        now = self._clock()
        self._stamps = [t for t in self._stamps if now - t < 60.0]
        if len(self._stamps) >= self.max_per_minute:
            wait = 60.0 - (now - self._stamps[0]) + 0.1
            if wait > 0:
                logger.info("rate limit reached, sleeping %.1fs", wait)
                self._sleep(wait)
            now = self._clock()
            self._stamps = [t for t in self._stamps if now - t < 60.0]
        self._stamps.append(self._clock())


# ---------------------------------------------------------------------------
# channel factory
# ---------------------------------------------------------------------------
def build_notifier(spec: Dict[str, Any], session: Any = None) -> Optional[BaseNotifier]:
    """Instantiate one channel from a ``notify.yaml`` entry.

    Returns ``None`` for unknown types or disabled/empty channels so a typo in
    one block never breaks the whole run.
    """
    if not spec.get("enabled", True):
        return None
    kind = str(spec.get("type") or "").strip().lower()

    if kind == "console":
        return ConsoleNotifier()
    if kind == "dingtalk":
        return DingTalkNotifier(
            webhook=spec.get("webhook", ""),
            secret=spec.get("secret", ""),
            at_all=bool(spec.get("at_all", False)),
            at_mobiles=spec.get("at_mobiles") or [],
            timeout=float(spec.get("timeout", 10)),
            session=session,
        )
    if kind in ("wecom", "wework", "qywx"):
        return WeComNotifier(
            webhook=spec.get("webhook", ""),
            at_all=bool(spec.get("at_all", False)),
            at_mobiles=spec.get("at_mobiles") or [],
            timeout=float(spec.get("timeout", 10)),
            session=session,
        )
    if kind == "feishu":
        return FeishuNotifier(
            webhook=spec.get("webhook", ""),
            secret=spec.get("secret", ""),
            timeout=float(spec.get("timeout", 10)),
            session=session,
        )
    if kind == "webhook":
        return WebhookNotifier(
            url=spec.get("url", ""),
            timeout=float(spec.get("timeout", 10)),
            headers=spec.get("headers"),
            session=session,
        )
    if kind in ("alarm_center", "alarm"):
        return AlarmCenterNotifier(
            url=spec.get("url", ""),
            fallback_number=spec.get("fallback_number", ""),
            timeout=float(spec.get("timeout", 10)),
            level=spec.get("level", "critical"),
        )

    logger.warning("unknown notifier type %r, skipped", kind)
    return None


# ---------------------------------------------------------------------------
# manager
# ---------------------------------------------------------------------------
@dataclass
class NotifySettings:
    """Parsed ``notify.yaml``."""

    channels: List[BaseNotifier] = field(default_factory=list)
    alarm_center: Optional[AlarmCenterNotifier] = None
    rate_limit: RateLimiter = field(default_factory=RateLimiter)
    batch_size: int = 5
    title: str = "Service reachability alert"
    source: Optional[Path] = None


def parse_notify(data: Dict[str, Any], source: Optional[Path] = None) -> NotifySettings:
    """Turn a raw ``notify.yaml`` mapping into :class:`NotifySettings`."""
    data = interpolate(data)

    rl = data.get("rate_limit") or {}
    limiter = RateLimiter(max_per_minute=int(rl.get("max_per_minute", 20)))

    channels: List[BaseNotifier] = []
    for spec in data.get("channels") or []:
        if not isinstance(spec, dict):
            continue
        notifier = build_notifier(spec)
        if notifier is not None and notifier.enabled:
            channels.append(notifier)

    alarm: Optional[AlarmCenterNotifier] = None
    alarm_raw = data.get("alarm_center") or {}
    if isinstance(alarm_raw, dict) and alarm_raw.get("enabled", False):
        alarm = build_notifier(dict(alarm_raw, type="alarm_center", enabled=True))  # type: ignore[assignment]

    if not channels:
        channels.append(ConsoleNotifier())  # never run silently

    return NotifySettings(
        channels=channels,
        alarm_center=alarm,
        rate_limit=limiter,
        batch_size=max(1, int(rl.get("batch_size", 5))),
        title=str(data.get("title") or "Service reachability alert"),
        source=Path(source) if source else None,
    )


def load_notify_config(path: Any = "notify.yaml", env_file: Any = None) -> NotifySettings:
    """Load ``notify.yaml``; fall back to console-only when the file is absent."""
    load_dotenv(Path(env_file) if env_file else None)
    resolved = resolve_path(path)
    if resolved is None:
        logger.info("notify config %s not found, using console-only", path)
        return NotifySettings(channels=[ConsoleNotifier()])
    return parse_notify(read_yaml(resolved), source=resolved)


class NotificationManager:
    """Send failure alerts through every enabled channel, throttled and batched."""

    def __init__(self, settings: NotifySettings) -> None:
        self.settings = settings

    @property
    def channels(self) -> List[BaseNotifier]:
        return self.settings.channels

    # ------------------------------------------------------------------
    def notify_results(self, results: List[CheckResult]) -> List[NotificationResult]:
        """Alert on all failures; no-op (empty list) when everything is up."""
        failures = [r for r in results if r.should_alert]
        if not failures:
            return []
        outcomes: List[NotificationResult] = []
        for batch in _chunk(failures, self.settings.batch_size):
            text = format_failures(batch, self.settings.title)
            mobiles = collect_mobiles(batch)
            outcomes.extend(self._send_all(text, mobiles))
        return outcomes

    def notify_text(self, text: str, mobiles: Optional[List[str]] = None) -> List[NotificationResult]:
        return self._send_all(text, mobiles)

    def _send_all(self, text: str, mobiles: Optional[List[str]]) -> List[NotificationResult]:
        outcomes: List[NotificationResult] = []
        for channel in self.settings.channels:
            self.settings.rate_limit.acquire()
            result = channel.send(text, mobiles)
            outcomes.append(result)
            if not result.ok:
                logger.warning("channel %s failed: %s", channel.name, result.detail)
        if self.settings.alarm_center is not None and self.settings.alarm_center.enabled:
            outcomes.append(self.settings.alarm_center.send(text, mobiles))
        return outcomes


def _chunk(items: List[CheckResult], size: int) -> List[List[CheckResult]]:
    return [items[i : i + size] for i in range(0, len(items), size)] or [[]]


__all__ = [
    "RateLimiter",
    "NotifySettings",
    "NotificationManager",
    "build_notifier",
    "parse_notify",
    "load_notify_config",
]