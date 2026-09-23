"""Failure notification channels.

The original scripts hard-coded chat-robot webhook tokens and owner phone
numbers in source. Here every channel is built from ``notify.yaml``, secrets
come from the environment, and channels degrade to a no-op when unconfigured.
"""

from .base import BaseNotifier, NotificationResult, NotifierError, collect_mobiles, format_failures
from .console import ConsoleNotifier
from .chat import DingTalkNotifier, FeishuNotifier, WeComNotifier
from .webhook import WebhookNotifier
from .alarm import AlarmCenterNotifier
from .manager import (
    NotificationManager,
    NotifySettings,
    RateLimiter,
    build_notifier,
    load_notify_config,
    parse_notify,
)

__all__ = [
    "BaseNotifier",
    "NotificationResult",
    "NotifierError",
    "ConsoleNotifier",
    "DingTalkNotifier",
    "FeishuNotifier",
    "WeComNotifier",
    "WebhookNotifier",
    "AlarmCenterNotifier",
    "NotificationManager",
    "NotifySettings",
    "RateLimiter",
    "build_notifier",
    "parse_notify",
    "load_notify_config",
    "collect_mobiles",
    "format_failures",
]