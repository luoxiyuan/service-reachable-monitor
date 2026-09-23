"""Report rendering: console tables, JSON export and log files."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import CheckResult, RunReport, Status

logger = logging.getLogger("service_monitor")

_STATUS_MARKS = {
    Status.UP: "OK  ",
    Status.DOWN: "DOWN",
    Status.ERROR: "ERR ",
    Status.SKIPPED: "SKIP",
    Status.UNKNOWN: "????",
}


def render_table(results: List[CheckResult]) -> str:
    """Fixed-width text table, handy for terminals and CI logs."""
    if not results:
        return "(no targets checked)"

    rows = [
        [
            _STATUS_MARKS.get(r.status, r.status.value),
            r.target.kind,
            r.target.env_label or r.target.environment,
            r.target.name[:60],
            (r.target.owner.label if r.target.owner else "-")[:20],
            (r.message or "")[:80],
        ]
        for r in results
    ]
    header = ["ST", "KIND", "ENV", "TARGET", "OWNER", "MESSAGE"]
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(header)]
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(header))]
    lines.append("  ".join("-" * w for w in widths))
    lines.extend("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows)
    return "\n".join(lines)


def to_json(report: RunReport, indent: int = 2) -> str:
    return json.dumps(report.as_dict(), ensure_ascii=False, indent=indent)


def write_json(report: RunReport, path: Any) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_json(report), encoding="utf-8")
    return out


def setup_logging(level: str = "INFO", log_file: Optional[Any] = None) -> None:
    """Configure the root logger once; safe to call repeatedly."""
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def log_run(report: RunReport) -> None:
    """Emit one INFO summary plus one line per failure."""
    logger.info("run finished in %.1fs | %s", report.duration_seconds, report.digest())
    for result in report.failures:
        logger.warning("ALERT %s", result.summary_line())


def utc_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())