"""HTTP(S) reachability checker.

Distilled from the original ``monitor_http.py`` / ``env_monitor_http.py``
scripts: probe one URL per service, treat a configured set of status codes as
healthy, retry a few times, and report anything else as unreachable.

The original per-environment hard-coded URL tables and inlined owner phone
numbers are replaced by ``targets.yaml`` plus owner metadata, so the same code
works for any estate.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

import requests

from ..config import EnvironmentConfig, HttpTargetConfig
from ..models import CheckResult, Status, Target
from .base import BaseChecker


class HttpChecker(BaseChecker):
    """Probe HTTP(S) endpoints declared in ``targets.yaml``."""

    kind = "http"

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        #: Inject a session in tests; defaults to a plain one.
        self.session = session or requests.Session()

    # ------------------------------------------------------------------
    def check_environment(self, environment: EnvironmentConfig) -> List[CheckResult]:
        return [self.check_target(cfg) for cfg in environment.http_targets]

    def check_target(self, cfg: HttpTargetConfig) -> CheckResult:
        """Probe one endpoint with retries. Never raises."""
        target = Target(
            kind=self.kind,
            environment=cfg.environment,
            name=cfg.name,
            detail=cfg.url,
            owner=cfg.owner,
            env_label=cfg.env_label,
            env_ip=cfg.env_ip,
            params=dict(cfg.params),
        )

        attempts = max(1, cfg.retries + 1)
        last_status = Status.ERROR
        last_message = "no attempt made"
        last_latency: Optional[float] = None

        for attempt in range(1, attempts + 1):
            status, message, latency, meta = self._single_request(cfg)
            last_status, last_message, last_latency = status, message, latency
            if status is Status.UP:
                return CheckResult(
                    target=target,
                    status=Status.UP,
                    message=message,
                    latency_ms=latency,
                    attempts=attempt,
                    meta=meta,
                )

        return CheckResult(
            target=target,
            status=last_status,
            message=last_message,
            latency_ms=last_latency,
            attempts=attempts,
            meta=meta,
        )

    # ------------------------------------------------------------------
    def _single_request(
        self, cfg: HttpTargetConfig
    ) -> Tuple[Status, str, Optional[float], dict]:
        """One HTTP attempt. Returns ``(status, message, latency_ms, meta)``."""
        started = time.monotonic()
        try:
            response = self.session.request(
                method=cfg.method,
                url=cfg.url,
                timeout=cfg.timeout,
                verify=cfg.verify_tls,
                allow_redirects=cfg.follow_redirects,
                headers=cfg.headers or None,
            )
        except requests.exceptions.Timeout:
            return Status.DOWN, f"timeout after {cfg.timeout}s", _elapsed_ms(started), {}
        except requests.exceptions.SSLError as exc:
            return Status.DOWN, f"TLS error: {_short(exc)}", _elapsed_ms(started), {}
        except requests.exceptions.ConnectionError as exc:
            return Status.DOWN, f"connection error: {_short(exc)}", _elapsed_ms(started), {}
        except requests.exceptions.RequestException as exc:
            return Status.ERROR, f"request failed: {_short(exc)}", _elapsed_ms(started), {}

        latency = _elapsed_ms(started)
        code = response.status_code
        meta = {"http_status": code}

        if code not in cfg.expect_status:
            return Status.DOWN, f"unexpected status {code}", latency, meta

        if cfg.expect_body:
            if cfg.expect_body not in _safe_text(response):
                return Status.DOWN, f"status {code} but body mismatch", latency, meta

        return Status.UP, f"{code} in {latency}ms", latency, meta


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 2)


def _short(exc: BaseException, limit: int = 160) -> str:
    """Compact single-line exception text for logs and alert bodies."""
    text = " ".join(str(exc).split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _safe_text(response: requests.Response, limit: int = 4096) -> str:
    try:
        return response.text[:limit]
    except Exception:  # pragma: no cover - defensive
        return ""