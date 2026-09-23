"""Cron-style scheduling, replacing the original APScheduler wiring.

The legacy scripts called ``BlockingScheduler.add_job(..., 'cron',
day_of_week='mon-fri', hour='10,14,16,18', minute='0/10')`` with the job
arguments hard-coded. Here the schedule comes from configuration and the job
is simply :meth:`service_monitor.runner.MonitorRunner.run`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

#: Defaults matching the historical monitoring window.
DEFAULT_CRON: Dict[str, str] = {
    "day_of_week": "mon-fri",
    "hour": "10,14,16,18",
    "minute": "0/10",
}


def start_scheduler(
    job: Callable[[], Any],
    cron: Optional[Dict[str, str]] = None,
    job_id: str = "reachability-check",
) -> Any:
    """Block forever, running ``job`` on the given cron schedule.

    Requires ``APScheduler`` (see ``requirements-optional.txt``). Raises a
    clear error instead of an ImportError traceback when it is missing.
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "APScheduler is required for 'monitor schedule': pip install APScheduler"
        ) from exc

    settings = dict(DEFAULT_CRON)
    settings.update({k: v for k, v in (cron or {}).items() if v})

    timezone = settings.pop("timezone", None)
    scheduler = BlockingScheduler(timezone=timezone) if timezone else BlockingScheduler()
    scheduler.add_job(job, "cron", id=job_id, **settings)
    logger.info("scheduler started: cron %s", settings)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover - interactive
        logger.info("scheduler stopped by user")
        scheduler.shutdown(wait=False)
    return scheduler


def parse_cron(value: Optional[str]) -> Dict[str, str]:
    """Parse a 5-field cron expression into APScheduler kwargs.

    ``"*/10 10,14,16,18 * * mon-fri"`` -> minute/hour/day/month/day_of_week.
    """
    if not value or not str(value).strip():
        return dict(DEFAULT_CRON)
    fields = str(value).split()
    if len(fields) != 5:
        raise ValueError(f"expected a 5-field cron expression, got: {value!r}")
    minute, hour, day, month, day_of_week = fields
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }


__all__ = ["start_scheduler", "parse_cron", "DEFAULT_CRON"]