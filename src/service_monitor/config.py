"""Configuration loading.

Two YAML files drive the tool:

* ``targets.yaml`` - what to monitor (environments, HTTP URLs, Dubbo roots).
* ``notify.yaml``  - how to report failures (channels, throttling).

Both support ``${VAR_NAME}`` interpolation so secrets stay in the environment
(or a git-ignored ``.env``) instead of in version control.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .models import Owner

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Fallbacks applied when a key is absent from the YAML file.
HTTP_DEFAULTS: Dict[str, Any] = {
    "timeout": 10,
    "retries": 1,
    "verify_tls": True,
    "follow_redirects": True,
    "expect_status": [200],
    "expect_body": None,
    "method": "GET",
    "headers": {},
}

DUBBO_DEFAULTS: Dict[str, Any] = {
    "timeout": 10,
    "probe_port": True,
    "fail_if_no_provider": True,
}


class ConfigError(Exception):
    """Raised when configuration files are missing or malformed."""


# ---------------------------------------------------------------------------
# env interpolation + .env loading
# ---------------------------------------------------------------------------
def load_dotenv(path: Optional[Path] = None) -> None:
    """Populate ``os.environ`` from a simple ``KEY=VALUE`` file.

    Existing environment variables always win, so real deployments can inject
    secrets through the platform instead of a file.
    """
    if path is None:
        path = Path.cwd() / ".env"
    path = Path(path)
    if not path.is_file():
        return

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read env file {path}: {exc}") from exc

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def interpolate(value: Any) -> Any:
    """Recursively replace ``${VAR}`` placeholders using ``os.environ``.

    Unknown variables become an empty string, which lets a missing webhook
    simply disable its channel rather than crash the run.
    """
    if isinstance(value, str):
        return _VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# raw yaml helpers
# ---------------------------------------------------------------------------
def read_yaml(path: Path) -> Dict[str, Any]:
    """Read a YAML mapping, raising :class:`ConfigError` on problems."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")
    return data


def resolve_path(candidate: Any, search_roots: Optional[List[Path]] = None) -> Optional[Path]:
    """Find a config file by relative or absolute path.

    Bare file names are also looked up in the usual places (cwd, ``./config``
    and the repo ``config/`` directory next to ``src/``).
    """
    if not candidate:
        return None
    path = Path(str(candidate)).expanduser()
    if path.is_absolute():
        return path if path.is_file() else None

    roots: List[Path] = []
    if search_roots:
        roots.extend(Path(r) for r in search_roots)
    cwd = Path.cwd()
    roots.extend([cwd, cwd / "config", Path(__file__).resolve().parents[2] / "config"])

    for root in roots:
        found = root / path
        if found.is_file():
            return found
    return None


def merge_defaults(defaults: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Shallow-merge ``overrides`` on top of ``defaults`` (copy, no mutation)."""
    merged = dict(defaults)
    for key, value in (overrides or {}).items():
        if value is not None:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# typed views
# ---------------------------------------------------------------------------
@dataclass
class HttpTargetConfig:
    """One HTTP(S) endpoint to probe."""

    name: str
    url: str
    environment: str
    env_label: str = ""
    env_ip: str = ""
    owner: Optional[Owner] = None
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def timeout(self) -> float:
        return float(self.params.get("timeout", HTTP_DEFAULTS["timeout"]))

    @property
    def retries(self) -> int:
        return int(self.params.get("retries", HTTP_DEFAULTS["retries"]))

    @property
    def verify_tls(self) -> bool:
        return bool(self.params.get("verify_tls", True))

    @property
    def follow_redirects(self) -> bool:
        return bool(self.params.get("follow_redirects", True))

    @property
    def expect_status(self) -> List[int]:
        value = self.params.get("expect_status") or [200]
        if isinstance(value, (int, str)):
            value = [value]
        return [int(v) for v in value]

    @property
    def expect_body(self) -> Optional[str]:
        return self.params.get("expect_body")

    @property
    def method(self) -> str:
        return str(self.params.get("method", "GET")).upper()

    @property
    def headers(self) -> Dict[str, str]:
        return dict(self.params.get("headers") or {})


@dataclass
class DubboConfig:
    """ZooKeeper discovery settings for one environment."""

    zk_hosts: str
    root_path: str
    environment: str
    env_label: str = ""
    env_ip: str = ""
    applications: List[str] = field(default_factory=list)
    include_interfaces: List[str] = field(default_factory=list)
    exclude_interfaces: List[str] = field(default_factory=list)
    owners: Dict[str, Owner] = field(default_factory=dict)
    app_descs: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def timeout(self) -> float:
        return float(self.params.get("timeout", DUBBO_DEFAULTS["timeout"]))

    @property
    def probe_port(self) -> bool:
        return bool(self.params.get("probe_port", True))

    @property
    def fail_if_no_provider(self) -> bool:
        return bool(self.params.get("fail_if_no_provider", True))

    @property
    def zk_root(self) -> str:
        """Normalised absolute ZooKeeper root path."""
        path = (self.root_path or "").strip()
        if not path:
            return "/"
        if not path.startswith("/"):
            path = "/" + path
        return path.rstrip("/") or "/"

    def owner_for(self, application: Optional[str]) -> Optional[Owner]:
        if not application:
            return None
        return self.owners.get(application)


@dataclass
class EnvironmentConfig:
    """One monitored environment: HTTP targets plus optional Dubbo root."""

    name: str
    label: str = ""
    ip: str = ""
    enabled: bool = True
    http_targets: List[HttpTargetConfig] = field(default_factory=list)
    dubbo: Optional[DubboConfig] = None

    @property
    def display(self) -> str:
        return self.label or self.name


@dataclass
class MonitorConfig:
    """Fully resolved configuration for a run."""

    environments: List[EnvironmentConfig] = field(default_factory=list)
    http_defaults: Dict[str, Any] = field(default_factory=lambda: dict(HTTP_DEFAULTS))
    dubbo_defaults: Dict[str, Any] = field(default_factory=lambda: dict(DUBBO_DEFAULTS))
    source: Optional[Path] = None

    def select(self, names: Optional[List[str]], include_disabled: bool = False) -> List[EnvironmentConfig]:
        """Pick environments by name; fall back to all enabled ones."""
        pool = self.environments
        if not include_disabled:
            pool = [e for e in pool if e.enabled]
        if names:
            wanted = {n.strip() for n in names if n and n.strip()}
            picked = [e for e in pool if e.name in wanted]
            missing = wanted - {e.name for e in picked}
            if missing:
                raise ConfigError(
                    "unknown environment(s): %s (available: %s)"
                    % (", ".join(sorted(missing)), ", ".join(e.name for e in self.environments) or "-")
                )
            return picked
        return pool


def _parse_owner(raw: Any) -> Optional[Owner]:
    """Accept ``"13800138000"`` or a mapping with phone/name/email keys."""
    if raw is None:
        return None
    if isinstance(raw, (str, int)):
        text = str(raw).strip()
        if not text:
            return None
        if "," in text:  # legacy "name,phone" form
            left, _, right = text.partition(",")
            return Owner(phone=right.strip() or None, name=left.strip() or None)
        return Owner(phone=text)
    if isinstance(raw, dict):
        return Owner(
            phone=(str(raw["phone"]).strip() if raw.get("phone") else None),
            name=(str(raw["name"]).strip() if raw.get("name") else None),
            email=(str(raw["email"]).strip() if raw.get("email") else None),
        )
    return None


def _parse_owners(raw: Any) -> Dict[str, Owner]:
    owners: Dict[str, Owner] = {}
    if not isinstance(raw, dict):
        return owners
    for application, spec in raw.items():
        owner = _parse_owner(spec)
        if owner is None and isinstance(spec, dict):
            owner = Owner(
                name=(str(spec["desc"]).strip() if spec.get("desc") else None),
            )
        if owner is not None:
            owners[str(application)] = owner
    return owners


def _application_descs(raw: Any) -> Dict[str, str]:
    descs: Dict[str, str] = {}
    if isinstance(raw, dict):
        for application, spec in raw.items():
            if isinstance(spec, dict) and spec.get("desc"):
                descs[str(application)] = str(spec["desc"])
    return descs


def parse_targets(data: Dict[str, Any], source: Optional[Path] = None) -> MonitorConfig:
    """Turn a raw ``targets.yaml`` mapping into a :class:`MonitorConfig`."""
    data = interpolate(data)

    defaults_block = data.get("defaults") or {}
    http_defaults = merge_defaults(HTTP_DEFAULTS, defaults_block.get("http"))
    dubbo_defaults = merge_defaults(DUBBO_DEFAULTS, defaults_block.get("dubbo"))

    environments: List[EnvironmentConfig] = []
    for env_raw in data.get("environments") or []:
        if not isinstance(env_raw, dict):
            continue
        name = str(env_raw.get("name") or "").strip()
        if not name:
            raise ConfigError("every environment needs a non-empty 'name'")
        label = str(env_raw.get("label") or "").strip() or name
        env_ip = str(env_raw.get("ip") or "").strip()

        http_targets: List[HttpTargetConfig] = []
        for item in env_raw.get("http_targets") or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue  # a target without a URL cannot be probed
            t_name = str(item.get("name") or "").strip() or url
            params = merge_defaults(http_defaults, item)
            params.pop("name", None)
            params.pop("url", None)
            params.pop("owner", None)
            http_targets.append(
                HttpTargetConfig(
                    name=t_name,
                    url=url,
                    environment=name,
                    env_label=label,
                    env_ip=env_ip,
                    owner=_parse_owner(item.get("owner")),
                    params=params,
                )
            )

        dubbo_cfg: Optional[DubboConfig] = None
        dubbo_raw = env_raw.get("dubbo")
        if isinstance(dubbo_raw, dict) and str(dubbo_raw.get("zk_hosts") or "").strip():
            dubbo_params = merge_defaults(dubbo_defaults, dubbo_raw)
            for key in ("zk_hosts", "root_path", "applications", "owners",
                        "include_interfaces", "exclude_interfaces"):
                dubbo_params.pop(key, None)
            dubbo_cfg = DubboConfig(
                zk_hosts=str(dubbo_raw.get("zk_hosts")).strip(),
                root_path=str(dubbo_raw.get("root_path") or "").strip(),
                environment=name,
                env_label=label,
                env_ip=env_ip,
                applications=[str(a) for a in (dubbo_raw.get("applications") or [])],
                include_interfaces=[str(a) for a in (dubbo_raw.get("include_interfaces") or [])],
                exclude_interfaces=[str(a) for a in (dubbo_raw.get("exclude_interfaces") or [])],
                owners=_parse_owners(dubbo_raw.get("owners")),
                app_descs=_application_descs(dubbo_raw.get("owners")),
                params=dubbo_params,
            )

        if not http_targets and dubbo_cfg is None:
            continue  # nothing to do for this environment

        environments.append(
            EnvironmentConfig(
                name=name,
                label=label,
                ip=env_ip,
                enabled=bool(env_raw.get("enabled", True)),
                http_targets=http_targets,
                dubbo=dubbo_cfg,
            )
        )

    return MonitorConfig(
        environments=environments,
        http_defaults=http_defaults,
        dubbo_defaults=dubbo_defaults,
        source=Path(source) if source else None,
    )


def load_monitor_config(path: Any = "targets.yaml", env_file: Any = None) -> MonitorConfig:
    """Load ``targets.yaml`` (creating a helpful error when absent)."""
    load_dotenv(Path(env_file) if env_file else None)
    resolved = resolve_path(path)
    if resolved is None:
        raise ConfigError(
            f"targets config not found: {path}\n"
            "Hint: copy config/targets.example.yaml to config/targets.yaml and edit it."
        )
    return parse_targets(read_yaml(resolved), source=resolved)