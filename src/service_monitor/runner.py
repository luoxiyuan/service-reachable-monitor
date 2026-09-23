"""Orchestration: load config, run every checker, build the report."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional

from .checkers import BaseChecker, DubboChecker, HttpChecker
from .config import EnvironmentConfig, MonitorConfig
from .models import CheckResult, RunReport, Status
from .notifiers.manager import NotificationManager, NotifySettings

logger = logging.getLogger(__name__)


class MonitorRunner:
    """Runs the configured checkers over the selected environments."""

    def __init__(
        self,
        config: MonitorConfig,
        notify: Optional[NotificationManager] = None,
        checkers: Optional[Iterable[BaseChecker]] = None,
        session: Any = None,
    ) -> None:
        self.config = config
        self.notify = notify
        self.checkers: List[BaseChecker] = list(checkers) if checkers else [
            HttpChecker(session=session),
            DubboChecker(),
        ]

    # ------------------------------------------------------------------
    def run(
        self,
        environments: Optional[List[str]] = None,
        kinds: Optional[List[str]] = None,
        include_disabled: bool = False,
        alert: bool = True,
    ) -> RunReport:
        """Check every selected environment and return the aggregated report."""
        report = RunReport(started_at=time.time())
        selected = self.config.select(environments, include_disabled=include_disabled)
        wanted_kinds = {k.strip().lower() for k in (kinds or []) if k and k.strip()}

        for environment in selected:
            for checker in self.checkers:
                if wanted_kinds and checker.kind not in wanted_kinds:
                    continue
                results = self._check_safely(checker, environment)
                report.extend(results)
                logger.info(
                    "%s/%s -> %s",
                    environment.display, checker.kind, _digest(results),
                )

        report.finished_at = time.time()

        if alert and self.notify is not None:
            self.notify.notify_results(report.results)
        return report

    # ------------------------------------------------------------------
    def inventory_dubbo(self, environments: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Collect Dubbo provider metadata without alerting.

        Mirrors the original scripts' "refresh the interface table" mode.
        """
        rows: List[Dict[str, Any]] = []
        checker = DubboChecker()
        for environment in self.config.select(environments):
            if environment.dubbo is None:
                continue
            try:
                rows.extend(checker.inventory(environment.dubbo))
            except Exception as exc:  # a broken root must not abort the scan
                logger.error("inventory failed for %s: %s", environment.display, exc)
        return rows

    def _check_safely(self, checker: BaseChecker, environment: EnvironmentConfig) -> List[CheckResult]:
        """Run one checker; convert unexpected crashes into ERROR results."""
        try:
            return checker.check_environment(environment) or []
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("checker %s failed on %s", checker.kind, environment.display)
            return [
                CheckResult(
                    target=_env_target(checker.kind, environment),
                    status=Status.ERROR,
                    message=f"checker crashed: {exc}",
                )
            ]


def _env_target(kind: str, environment: EnvironmentConfig):
    from .models import Target

    return Target(
        kind=kind,
        environment=environment.name,
        name=f"{environment.display} ({kind})",
        detail="",
        env_label=environment.label,
        env_ip=environment.ip,
    )


def _digest(results: List[CheckResult]) -> str:
    counts: Dict[str, int] = {}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    return " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty"


def build_runner(
    config: MonitorConfig,
    notify_settings: Optional[NotifySettings] = None,
    session: Any = None,
) -> MonitorRunner:
    manager = NotificationManager(notify_settings) if notify_settings else None
    return MonitorRunner(config=config, notify=manager, session=session)


__all__ = ["MonitorRunner", "build_runner"]