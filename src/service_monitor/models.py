"""Data model shared by the checkers, the reporter and the notifiers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Status(str, Enum):
    """Outcome of a single reachability probe."""

    UP = "UP"                # probe succeeded
    DOWN = "DOWN"            # probe failed: service is not reachable
    ERROR = "ERROR"          # probe itself broke (bad config, DNS, exception)
    SKIPPED = "SKIPPED"      # deliberately not probed (disabled / filtered)
    UNKNOWN = "UNKNOWN"      # could not decide, e.g. no consumers registered


#: Statuses that should raise an alert.
ALERT_STATUSES = (Status.DOWN, Status.ERROR)


@dataclass
class Owner:
    """Who to contact when a target is unreachable."""

    phone: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None

    @property
    def label(self) -> str:
        return self.name or self.phone or self.email or "unassigned"

    def as_dict(self) -> Dict[str, Any]:
        return {"phone": self.phone, "name": self.name, "email": self.email}


@dataclass
class Target:
    """One thing to probe, independent of the checker that handles it."""

    kind: str                          # "http" | "dubbo"
    environment: str                   # environment `name`
    name: str                          # human readable identifier
    detail: str = ""                   # url / interface, for reports
    owner: Optional[Owner] = None
    application: Optional[str] = None  # Dubbo application name
    env_label: str = ""
    env_ip: str = ""
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        """Stable key used to de-duplicate alerts."""
        return f"{self.kind}:{self.environment}:{self.name}:{self.detail}"


@dataclass
class CheckResult:
    """Outcome of probing one :class:`Target`."""

    target: Target
    status: Status
    message: str = ""
    latency_ms: Optional[float] = None
    attempts: int = 1
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is Status.UP

    @property
    def should_alert(self) -> bool:
        return self.status in ALERT_STATUSES

    def summary_line(self) -> str:
        """One-line, human readable description used in alert bodies."""
        label = self.target.env_label or self.target.environment
        parts = [f"{label}: {self.target.name} [{self.status.value}]"]
        if self.target.detail:
            parts.append(self.target.detail)
        if self.message:
            parts.append(self.message)
        return " | ".join(parts)

    def as_dict(self) -> Dict[str, Any]:
        t = self.target
        return {
            "kind": t.kind,
            "environment": t.environment,
            "env_label": t.env_label,
            "env_ip": t.env_ip,
            "name": t.name,
            "detail": t.detail,
            "application": t.application,
            "owner": t.owner.as_dict() if t.owner else None,
            "status": self.status.value,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "meta": self.meta,
        }


@dataclass
class RunReport:
    """Aggregated result of one monitoring run."""

    results: List[CheckResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def extend(self, results: List[CheckResult]) -> None:
        self.results.extend(results)

    @property
    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if r.should_alert]

    @property
    def duration_seconds(self) -> float:
        return round(self.finished_at - self.started_at, 3)

    def count(self, status: Status) -> int:
        return sum(1 for r in self.results if r.status is status)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "total": len(self.results),
            "up": self.count(Status.UP),
            "down": self.count(Status.DOWN),
            "error": self.count(Status.ERROR),
            "skipped": self.count(Status.SKIPPED),
            "unknown": self.count(Status.UNKNOWN),
            "results": [r.as_dict() for r in self.results],
        }

    def digest(self) -> str:
        return (
            f"total={len(self.results)} up={self.count(Status.UP)} "
            f"down={self.count(Status.DOWN)} error={self.count(Status.ERROR)} "
            f"skipped={self.count(Status.SKIPPED)} "
            f"unknown={self.count(Status.UNKNOWN)}"
        )