"""Console notifier: prints alerts to stdout.

Always available, needs no secrets, and is the default in CI.
"""

from __future__ import annotations

from typing import List, Optional

from .base import BaseNotifier, NotificationResult


class ConsoleNotifier(BaseNotifier):
    """Write alerts to stdout (or any file-like object)."""

    name = "console"

    def __init__(self, stream: object = None) -> None:
        import sys

        self._stream = stream or sys.stdout

    def send(self, text: str, mobiles: Optional[List[str]] = None) -> NotificationResult:
        try:
            self._stream.write(text + "\n")  # type: ignore[attr-defined]
            self._stream.flush()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - defensive
            return NotificationResult(self.name, False, str(exc))
        return NotificationResult(self.name, True, "printed to console")