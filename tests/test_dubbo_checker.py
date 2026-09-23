"""Dubbo checker tests with a fake ZooKeeper client (no kazoo needed)."""

from service_monitor.checkers.dubbo_checker import (
    DubboChecker,
    DubboError,
    parse_provider_url,
)
from service_monitor.config import DubboConfig, EnvironmentConfig
from service_monitor.models import Owner, Status


class FakeZkClient:
    """Minimal kazoo-compatible stand-in driven by a {path: [children]} map."""

    def __init__(self, tree):
        self.tree = tree
        self.started = False
        self.stopped = False

    def start(self, timeout=None):
        self.started = True

    def stop(self):
        self.stopped = True

    def exists(self, path):
        return path in self.tree

    def get_children(self, path):
        return list(self.tree.get(path, []))


#: DubboConfig dataclass fields that must not leak into `params`.
_FIELDS = ("include_interfaces", "exclude_interfaces", "applications", "owners", "app_descs")


def _cfg(root="/services", **overrides):
    base = dict(timeout=5, probe_port=False, fail_if_no_provider=True)
    fields = {}
    for key, value in overrides.items():
        if key in _FIELDS:
            fields[key] = value
        else:
            base[key] = value
    return DubboConfig(
        zk_hosts="zk1:2181", root_path=root, environment="env-a",
        env_label="Env A", owners={"demo-server": Owner(phone="13800138000")},
        app_descs={"demo-server": "Demo"}, params=base, **fields,
    )


def _checker(tree, tcp=lambda h, p, t: True):
    return DubboChecker(client_factory=lambda hosts, timeout: FakeZkClient(tree), tcp_probe=tcp)


PROVIDER = "dubbo://10.0.0.5:20880/com.example.DemoService?application=demo-server&interface=com.example.DemoService"


def test_provider_present_is_up():
    tree = {
        "/services": ["com.example.DemoService"],
        "/services/com.example.DemoService/providers": [PROVIDER],
        "/services/com.example.DemoService/consumers": ["consumer://10.0.0.9/com.example.DemoService"],
    }
    results = _checker(tree).check_dubbo(_cfg())
    assert len(results) == 1
    assert results[0].status is Status.UP
    assert results[0].target.application == "demo-server"
    assert results[0].target.owner.phone == "13800138000"


def test_consumers_without_provider_is_down():
    tree = {
        "/services": ["com.example.DemoService"],
        "/services/com.example.DemoService/providers": [],
        "/services/com.example.DemoService/consumers": ["consumer://10.0.0.9/x"],
    }
    results = _checker(tree).check_dubbo(_cfg())
    assert results[0].status is Status.DOWN
    assert "no provider" in results[0].message


def test_no_provider_flag_disabled_yields_unknown():
    tree = {
        "/services": ["com.example.DemoService"],
        "/services/com.example.DemoService/providers": [],
        "/services/com.example.DemoService/consumers": ["consumer://1/x"],
    }
    results = _checker(tree).check_dubbo(_cfg(fail_if_no_provider=False))
    assert results[0].status is Status.UNKNOWN


def test_neither_provider_nor_consumer_is_unknown():
    tree = {
        "/services": ["com.example.DemoService"],
        "/services/com.example.DemoService/providers": [],
        "/services/com.example.DemoService/consumers": [],
    }
    results = _checker(tree).check_dubbo(_cfg())
    assert results[0].status is Status.UNKNOWN


def test_tcp_probe_detects_dead_provider():
    tree = {
        "/services": ["com.example.DemoService"],
        "/services/com.example.DemoService/providers": [PROVIDER],
        "/services/com.example.DemoService/consumers": [],
    }
    results = _checker(tree, tcp=lambda h, p, t: False).check_dubbo(_cfg(probe_port=True))
    assert results[0].status is Status.DOWN
    assert "not accepting connections" in results[0].message


def test_exclude_interface_filter():
    tree = {
        "/services": ["com.example.DemoService", "com.example.internal.DebugService"],
        "/services/com.example.DemoService/providers": [PROVIDER],
        "/services/com.example.internal.DebugService/providers": [PROVIDER],
    }
    cfg = _cfg(exclude_interfaces=["*.internal.*"])
    results = _checker(tree).check_dubbo(cfg)
    assert [r.target.name for r in results] == ["com.example.DemoService"]


def test_include_interface_filter():
    tree = {
        "/services": ["com.example.DemoService", "com.example.OtherService"],
        "/services/com.example.DemoService/providers": [PROVIDER],
        "/services/com.example.OtherService/providers": [PROVIDER],
    }
    cfg = _cfg(include_interfaces=["*DemoService"])
    results = _checker(tree).check_dubbo(cfg)
    assert [r.target.name for r in results] == ["com.example.DemoService"]


def test_empty_root_returns_no_results():
    results = _checker({"/services": []}).check_dubbo(_cfg())
    assert results == []


def test_check_environment_without_dubbo_returns_empty():
    env = EnvironmentConfig(name="env-a", dubbo=None)
    assert DubboChecker().check_environment(env) == []


def test_zookeeper_failure_reports_error_not_crash():
    def boom(hosts, timeout):
        raise RuntimeError("connection refused")

    checker = DubboChecker(client_factory=boom)
    env = EnvironmentConfig(name="env-a", label="Env A", dubbo=_cfg())
    results = checker.check_environment(env)
    assert len(results) == 1
    assert results[0].status is Status.ERROR
    assert results[0].target.name == "zookeeper"


def test_missing_kazoo_raises_dubbo_error():
    # client_factory=None forces the real kazoo import path.
    checker = DubboChecker()
    env = EnvironmentConfig(name="env-a", dubbo=_cfg())
    results = checker.check_environment(env)
    # Either kazoo is installed (connect fails -> ERROR) or it is missing
    # (DubboError -> ERROR). Either way we get one ERROR result, never a crash.
    assert results[0].status is Status.ERROR


def test_inventory_collects_provider_metadata():
    tree = {
        "/services": ["com.example.DemoService"],
        "/services/com.example.DemoService/providers": [PROVIDER],
    }
    rows = _checker(tree).inventory(_cfg())
    assert len(rows) == 1
    assert rows[0]["interface"] == "com.example.DemoService"
    assert rows[0]["application"] == "demo-server"
    assert rows[0]["host"] == "10.0.0.5"
    assert rows[0]["port"] == 20880


# --- parse_provider_url -----------------------------------------------------
def test_parse_provider_url_full():
    meta = parse_provider_url(PROVIDER)
    assert meta["protocol"] == "dubbo"
    assert meta["host"] == "10.0.0.5"
    assert meta["port"] == 20880
    assert meta["interface"] == "com.example.DemoService"
    assert meta["application"] == "demo-server"


def test_parse_provider_url_encoded():
    from urllib import parse as up
    encoded = up.quote(PROVIDER, safe="")
    meta = parse_provider_url(encoded)
    assert meta["application"] == "demo-server"


def test_parse_provider_url_bare_host_port():
    meta = parse_provider_url("10.0.0.5:20880")
    assert meta["host"] == "10.0.0.5"
    assert meta["port"] == 20880


def test_parse_provider_url_empty_and_garbage():
    assert parse_provider_url("") == {}
    assert parse_provider_url("not-a-url").get("raw") == "not-a-url"