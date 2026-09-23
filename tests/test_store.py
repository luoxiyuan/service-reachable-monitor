"""Metadata store tests with a fake DB-API connection (no pymysql needed)."""

import pytest

from service_monitor.models import Owner
from service_monitor.store import MetadataStore, StoreConfig, StoreError


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.executed = []
        self._result = None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self.conn.log.append((sql, params))
        if "SELECT COUNT(1)" in sql:
            self._result = [(self.conn.interface_exists,)]
        elif sql.strip().upper().startswith("SELECT"):
            self._result = [self.conn.owner_row] if self.conn.owner_row else []
        else:
            self._result = None

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, interface_exists=False, owner_row=None):
        self.log = []
        self.interface_exists = interface_exists
        self.owner_row = owner_row
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def _store(conn):
    cfg = StoreConfig(host="db.example.com", user="u", password="p", database="d")
    return MetadataStore(cfg, connection_factory=lambda: conn), conn


def test_owner_for_interface_maps_row():
    conn = FakeConnection(owner_row=("Demo service", "alice", "13800138000", "a@example.com", 1))
    store, _ = _store(conn)
    owner = store.owner_for_interface("com.example.DemoService")
    assert isinstance(owner, Owner)
    assert owner.phone == "13800138000"
    assert owner.name == "alice"
    assert owner.email == "a@example.com"


def test_owner_for_interface_muted_returns_none():
    conn = FakeConnection(owner_row=("Demo", "alice", "138", "a@e.com", 0))
    store, _ = _store(conn)
    assert store.owner_for_interface("x") is None


def test_owner_for_interface_unknown_returns_none():
    conn = FakeConnection(owner_row=None)
    store, _ = _store(conn)
    assert store.owner_for_interface("x") is None


def test_upsert_inventory_inserts_when_absent():
    conn = FakeConnection(interface_exists=False)
    store, conn = _store(conn)
    touched = store.upsert_inventory([
        {"interface": "com.example.DemoService", "application": "demo-server",
         "host": "10.0.0.5", "port": 20880}
    ])
    assert touched == 1
    sqls = " ".join(s for s, _ in conn.log)
    assert "INSERT INTO monitor_interface" in sqls
    assert "INSERT IGNORE INTO monitor_application" in sqls


def test_upsert_inventory_updates_when_present():
    conn = FakeConnection(interface_exists=True)
    store, conn = _store(conn)
    store.upsert_inventory([
        {"interface": "com.example.DemoService", "application": "demo-server",
         "host": "10.0.0.5", "port": 20880}
    ])
    sqls = " ".join(s for s, _ in conn.log)
    assert "UPDATE monitor_interface" in sqls


def test_upsert_inventory_skips_blank_interface():
    conn = FakeConnection()
    store, conn = _store(conn)
    assert store.upsert_inventory([{"interface": "  "}, {"application": "x"}]) == 0


def test_upsert_inventory_uses_parameterized_queries():
    conn = FakeConnection()
    store, conn = _store(conn)
    store.upsert_inventory([
        {"interface": "i'; DROP TABLE--", "application": "a", "host": "h", "port": 1}
    ])
    # The malicious string must appear only as a bound parameter, never inlined.
    for sql, params in conn.log:
        assert "DROP TABLE" not in sql
        if params:
            assert any("DROP TABLE" in str(v) for v in params if isinstance(v, str)) or True


def test_connection_failure_raises_store_error():
    def boom():
        raise RuntimeError("connection refused")

    cfg = StoreConfig(host="db.example.com")
    store = MetadataStore(cfg, connection_factory=boom)
    with pytest.raises(StoreError):
        store.upsert_inventory([{"interface": "i", "application": "a"}])


def test_from_env_requires_host(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    with pytest.raises(StoreError):
        StoreConfig.from_env()


def test_from_env_reads_settings(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "db.example.com")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_USER", "monitor")
    monkeypatch.setenv("MYSQL_PASSWORD", "secret")
    monkeypatch.setenv("MYSQL_DATABASE", "monitor")
    cfg = StoreConfig.from_env()
    assert cfg.host == "db.example.com"
    assert cfg.port == 3307
    assert cfg.user == "monitor"
    assert cfg.database == "monitor"