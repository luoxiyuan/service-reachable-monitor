"""ZooKeeper-registered Dubbo service checker.

Distilled from the original ``monitor-dubbo.py`` / ``env_monitor.py`` scripts.

How the original decided a Dubbo service was down
-------------------------------------------------
Under a ZooKeeper root (for example ``/services_env_a``) Dubbo creates one node
per interface, and each interface node has ``providers`` and ``consumers``
children. The useful signal is the combination:

* ``providers`` non-empty  -> the service is up and serving.
* ``providers`` empty but ``consumers`` non-empty -> somebody still wants the
  service but nobody offers it: the provider crashed or was never started. This
  is the case worth alerting on.
* both empty -> nothing is wired to the interface; not actionable.

The original also parsed each provider URL (``dubbo://host:port/interface?...``)
to build an interface/application inventory and looked the owning team up in
MySQL. Both behaviours are kept here but made optional: ``kazoo`` is imported
lazily, and owner lookup falls back to ``targets.yaml``.

Nothing is hard-coded: hosts, roots, application filters and owners all come
from configuration.
"""

from __future__ import annotations

import fnmatch
import re
import socket
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib import parse

from ..config import DubboConfig, EnvironmentConfig
from ..models import CheckResult, Owner, Status, Target
from .base import BaseChecker

#: Matches the ``interface``/``application``/``version`` style query parameters
#: of a Dubbo provider URL.
_PROVIDER_URL_PREFIX = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


class DubboError(Exception):
    """Raised when ZooKeeper cannot be used at all (missing dep, no connection)."""


class DubboChecker(BaseChecker):
    """Check Dubbo services registered under a ZooKeeper root path."""

    kind = "dubbo"

    def __init__(self, client_factory: Any = None, tcp_probe: Any = None) -> None:
        """
        :param client_factory: callable ``(hosts, timeout) -> zk client`` used to
            inject a fake client in tests. Defaults to :mod:`kazoo`.
        :param tcp_probe: callable ``(host, port, timeout) -> bool`` overriding
            the built-in socket probe.
        """
        self._client_factory = client_factory
        self._tcp_probe = tcp_probe or self._default_tcp_probe

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def check_environment(self, environment: EnvironmentConfig) -> List[CheckResult]:
        cfg = environment.dubbo
        if cfg is None:
            return []
        try:
            return self.check_dubbo(cfg)
        except DubboError as exc:
            # ZooKeeper itself is unusable: report once for the environment
            # instead of hiding the problem behind an empty result list.
            return [
                CheckResult(
                    target=Target(
                        kind=self.kind,
                        environment=cfg.environment,
                        name="zookeeper",
                        detail=f"{cfg.zk_hosts}{cfg.zk_root}",
                        env_label=cfg.env_label,
                        env_ip=cfg.env_ip,
                    ),
                    status=Status.ERROR,
                    message=str(exc),
                )
            ]

    def check_dubbo(self, cfg: DubboConfig) -> List[CheckResult]:
        """Walk one ZooKeeper root and evaluate every selected interface."""
        results: List[CheckResult] = []
        with self._connect(cfg) as client:
            interfaces = self._children(client, cfg.zk_root)
            if not interfaces:
                return results

            for interface in sorted(interfaces):
                if not self._selected(interface, cfg):
                    continue
                results.append(self._check_interface(client, cfg, interface))
        return results

    def inventory(self, cfg: DubboConfig) -> List[Dict[str, Any]]:
        """Collect provider metadata (interface/application/host/port).

        Mirrors the original scripts' "insert interface table" mode, but returns
        plain dicts so callers can persist them wherever they like.
        """
        rows: List[Dict[str, Any]] = []
        with self._connect(cfg) as client:
            for interface in sorted(self._children(client, cfg.zk_root)):
                if not self._selected(interface, cfg):
                    continue
                for raw in self._children(client, self._path(cfg.zk_root, interface, "providers")):
                    meta = parse_provider_url(raw)
                    meta.update({"interface": interface, "environment": cfg.environment})
                    rows.append(meta)
        return rows

    # ------------------------------------------------------------------
    # per-interface evaluation
    # ------------------------------------------------------------------
    def _check_interface(self, client: Any, cfg: DubboConfig, interface: str) -> CheckResult:
        providers = self._children(client, self._path(cfg.zk_root, interface, "providers"))
        consumers = self._children(client, self._path(cfg.zk_root, interface, "consumers"))

        ref = self._owner_for_interface(cfg, interface, providers)
        target = Target(
            kind=self.kind,
            environment=cfg.environment,
            name=interface,
            detail=interface,
            owner=ref.owner if ref else None,
            application=ref.application if ref else None,
            env_label=cfg.env_label,
            env_ip=cfg.env_ip,
        )

        if providers:
            unreachable = self._unreachable_providers(providers, cfg)
            if unreachable:
                return CheckResult(
                    target=target,
                    status=Status.DOWN,
                    message=(
                        f"{len(unreachable)}/{len(providers)} provider(s) not "
                        f"accepting connections: {', '.join(unreachable[:3])}"
                    ),
                    meta={"providers": len(providers), "unreachable": unreachable},
                )
            return CheckResult(
                target=target,
                status=Status.UP,
                message=f"{len(providers)} provider(s) registered",
                meta={"providers": len(providers), "consumers": len(consumers)},
            )

        if consumers:
            if not cfg.fail_if_no_provider:
                return CheckResult(
                    target=target,
                    status=Status.UNKNOWN,
                    message=f"{len(consumers)} consumer(s) but provider check disabled",
                    meta={"consumers": len(consumers)},
                )
            return CheckResult(
                target=target,
                status=Status.DOWN,
                message=f"no provider while {len(consumers)} consumer(s) wait",
                meta={"providers": 0, "consumers": len(consumers)},
            )

        return CheckResult(
            target=target,
            status=Status.UNKNOWN,
            message="no provider and no consumer registered",
            meta={"providers": 0, "consumers": 0},
        )

    def _unreachable_providers(self, providers: Sequence[str], cfg: DubboConfig) -> List[str]:
        """TCP-probe each provider address; return the ones refusing connections."""
        if not cfg.probe_port:
            return []
        dead: List[str] = []
        for raw in providers:
            meta = parse_provider_url(raw)
            host = meta.get("host")
            port = meta.get("port")
            if not host or not port:
                continue
            if not self._tcp_probe(str(host), int(port), cfg.timeout):
                dead.append(f"{host}:{port}")
        return dead

    # ------------------------------------------------------------------
    # filtering + ownership
    # ------------------------------------------------------------------
    def _selected(self, interface: str, cfg: DubboConfig) -> bool:
        """Apply include/exclude/application filters from configuration."""
        if cfg.include_interfaces and not _matches_any(interface, cfg.include_interfaces):
            return False
        if cfg.exclude_interfaces and _matches_any(interface, cfg.exclude_interfaces):
            return False
        return True

    def _owner_for_interface(
        self, cfg: DubboConfig, interface: str, providers: Sequence[str]
    ) -> Optional["_OwnerRef"]:
        """Resolve the owning application, preferring explicit config.

        The original resolved ownership through a MySQL join
        (interface -> application -> owner). Here the application name is read
        from the provider URL and mapped through ``dubbo.owners`` in YAML, so no
        database is required.
        """
        application: Optional[str] = None
        for raw in providers:
            application = parse_provider_url(raw).get("application")
            if application:
                break

        if not application:
            application = _guess_application(interface, cfg.applications)

        if not application:
            return None

        owner = cfg.owners.get(application)
        desc = cfg.app_descs.get(application)
        if owner is None and desc:
            owner = Owner(name=desc)
        return _OwnerRef(application=application, owner=owner, desc=desc)

    # ------------------------------------------------------------------
    # ZooKeeper plumbing
    # ------------------------------------------------------------------
    def _connect(self, cfg: DubboConfig) -> "_ZkSession":
        return _ZkSession(cfg, self._client_factory)

    @staticmethod
    def _children(client: Any, path: str) -> List[str]:
        """List child nodes, tolerating a missing path."""
        try:
            if not client.exists(path):
                return []
            return [str(child) for child in (client.get_children(path) or [])]
        except Exception:
            return []

    @staticmethod
    def _path(root: str, *parts: str) -> str:
        joined = root.rstrip("/")
        for part in parts:
            joined += "/" + str(part).strip("/")
        return joined

    @staticmethod
    def _default_tcp_probe(host: str, port: int, timeout: float) -> bool:
        """Return True when a TCP connection to ``host:port`` succeeds."""
        try:
            with socket.create_connection((host, port), timeout=max(1.0, float(timeout))):
                return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class _OwnerRef:
    """Internal bundle of application name + contact details."""

    __slots__ = ("application", "owner", "desc")

    def __init__(self, application: str, owner: Optional[Owner], desc: Optional[str]) -> None:
        self.application = application
        self.owner = owner
        self.desc = desc


class _ZkSession:
    """Context manager around a lazily imported ZooKeeper client."""

    def __init__(self, cfg: DubboConfig, client_factory: Any = None) -> None:
        self.cfg = cfg
        self._factory = client_factory
        self._client: Any = None

    def __enter__(self) -> Any:
        self._client = self._build()
        return self._client

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self._client is not None:
            try:
                self._client.stop()
            except Exception:
                pass
            self._client = None
        return False

    def _build(self) -> Any:
        if self._factory is not None:
            try:
                client = self._factory(self.cfg.zk_hosts, self.cfg.timeout)
                client.start()
            except Exception as exc:
                raise DubboError(
                    f"cannot connect to ZooKeeper {self.cfg.zk_hosts}: {_short(exc)}"
                ) from exc
            return client
        try:
            from kazoo.client import KazooClient
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise DubboError(
                "kazoo is required for Dubbo checks: pip install kazoo "
                "(or pip install -r requirements-optional.txt)"
            ) from exc
        try:
            client = KazooClient(
                hosts=self.cfg.zk_hosts,
                timeout=self.cfg.timeout,
                connection_retry=3,
            )
            client.start(timeout=self.cfg.timeout)
        except Exception as exc:
            raise DubboError(
                f"cannot connect to ZooKeeper {self.cfg.zk_hosts}: {_short(exc)}"
            ) from exc
        return client


def parse_provider_url(raw: str) -> Dict[str, Any]:
    """Parse a Dubbo provider node name into a flat metadata dict.

    Node names look like::

        dubbo://10.0.0.5:20880/com.example.DemoService?application=demo-server
        &interface=com.example.DemoService&methods=list,get&version=1.0.0

    and may be URL-encoded. Unknown shapes degrade to ``{"raw": ...}`` instead
    of raising, because a single malformed node must not abort a whole scan.
    """
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        decoded = parse.unquote(text)
    except Exception:
        decoded = text

    if not _PROVIDER_URL_PREFIX.match(decoded):
        # Bare "host:port" style node.
        host, _, port = decoded.partition(":")
        meta: Dict[str, Any] = {"raw": text}
        if host and port.isdigit():
            meta.update({"host": host, "port": int(port)})
        return meta

    result = parse.urlparse(decoded)
    meta = {"raw": text, "protocol": result.scheme, "host": result.hostname, "port": result.port}
    path = (result.path or "").lstrip("/")
    if path:
        meta["interface"] = path

    for key, values in parse.parse_qs(result.query).items():
        if not values:
            continue
        meta[key] = values[0] if len(values) == 1 else values

    return {k: v for k, v in meta.items() if v is not None}


def _guess_application(interface: str, applications: Iterable[str]) -> Optional[str]:
    """Best-effort interface -> application mapping by name overlap."""
    apps = [a for a in applications if a]
    if not apps:
        return None
    tokens = {t for t in re.split(r"[.\-_]", interface.lower()) if len(t) > 3}
    for app in apps:
        app_tokens = {t for t in re.split(r"[.\-_]", app.lower()) if len(t) > 3}
        if tokens & app_tokens:
            return app
    return None


def _matches_any(value: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def _short(exc: BaseException, limit: int = 160) -> str:
    text = " ".join(str(exc).split())
    return text[:limit] + ("..." if len(text) > limit else "")