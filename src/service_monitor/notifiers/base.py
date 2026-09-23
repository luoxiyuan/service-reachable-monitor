"""Notifier interface and result type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..models import CheckResult


class NotifierError(Exception):
    """Raised when a channel cannot deliver a message."""


@dataclass
class NotificationResult:
    """Outcome of one delivery attempt."""

    channel: str
    ok: bool
    detail: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


class BaseNotifier(ABC):
    """Delivers human readable alert text to one destination."""

    #: unique channel name, also used in ``notify.yaml``.
    name: str = "base"

    @abstractmethod
    def send(self, text: str, mobiles: Optional[List[str]] = None) -> NotificationResult:
        """Send ``text``; ``mobiles`` are @-mentioned where supported."""
        raise NotImplementedError

    @property
    def enabled(self) -> bool:
        return True

    def send_results(self, results: List[CheckResult], title: str = "") -> NotificationResult:
        """Convenience helper: format failures and send them."""
        return self.send(format_failures(results, title))


def format_failures(results: List[CheckResult], title: str = "") -> str:
    """Render failures as a compact multi-line message."""
    lines: List[str] = [title or "Service reachability alert"]
    for result in results:
        lines.append(f"- {result.summary_line()}")
        owner = result.target.owner
        if owner and owner.phone:
            lines.append(f"  owner: {owner.label}")
    return "\n".join(lines)


def collect_mobiles(results: List[CheckResult]) -> List[str]:
    """De-duplicated owner phone numbers for @-mentions."""
    seen: Dict[str, None] = {}
    for result in results:
        owner = result.target.owner
        if owner and owner.phone:
            seen.setdefault(owner.phone, None)
    return list(seen.keys())