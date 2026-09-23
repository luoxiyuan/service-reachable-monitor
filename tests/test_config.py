"""Unit tests for configuration loading and interpolation."""

import textwrap

import pytest

from service_monitor.config import (
    ConfigError,
    HTTP_DEFAULTS,
    interpolate,
    load_dotenv,
    merge_defaults,
    parse_targets,
    resolve_path,
)


def test_interpolate_replaces_env_vars(monkeypatch):
    monkeypatch.setenv("SRM_TOKEN", "abc123")
    assert interpolate("${SRM_TOKEN}") == "abc123"
    assert interpolate({"a": ["${SRM_TOKEN}", 1]}) == {"a": ["abc123", 1]}


def test_interpolate_unknown_var_becomes_empty(monkeypatch):
    monkeypatch.delenv("SRM_MISSING", raising=False)
    assert interpolate("x-${SRM_MISSING}-y") == "x--y"


def test_merge_defaults_does_not_mutate():
    base = {"timeout": 10, "retries": 1}
    merged = merge_defaults(base, {"retries": 3})
    assert merged == {"timeout": 10, "retries": 3}
    assert base == {"timeout": 10, "retries": 1}


def test_merge_defaults_ignores_none_overrides():
    assert merge_defaults({"a": 1}, {"a": None}) == {"a": 1}


def test_load_dotenv_does_not_override_existing(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('SRM_A=from_file\nSRM_B="quoted"\n# comment\n', encoding="utf-8")
    monkeypatch.setenv("SRM_A", "from_env")
    monkeypatch.delenv("SRM_B", raising=False)
    load_dotenv(env)
    import os
    assert os.environ["SRM_A"] == "from_env"
    assert os.environ["SRM_B"] == "quoted"


def test_resolve_path_finds_file(tmp_path):
    f = tmp_path / "targets.yaml"
    f.write_text("environments: []\n", encoding="utf-8")
    assert resolve_path(f) == f
    assert resolve_path(f.name, search_roots=[tmp_path]) == f
    assert resolve_path("does-not-exist.yaml", search_roots=[tmp_path]) is None


def _write(tmp_path, text):
    p = tmp_path / "targets.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_parse_targets_minimal(tmp_path):
    cfg = parse_targets(
        {
            "environments": [
                {
                    "name": "env-a",
                    "label": "Env A",
                    "http_targets": [{"name": "api", "url": "https://api.example.com/h"}],
                }
            ]
        }
    )
    assert len(cfg.environments) == 1
    env = cfg.environments[0]
    assert env.name == "env-a"
    assert env.display == "Env A"
    assert len(env.http_targets) == 1
    target = env.http_targets[0]
    assert target.url == "https://api.example.com/h"
    assert target.timeout == HTTP_DEFAULTS["timeout"]


def test_parse_targets_owner_forms(tmp_path):
    cfg = parse_targets(
        {
            "environments": [
                {
                    "name": "e",
                    "http_targets": [
                        {"name": "a", "url": "https://a", "owner": "13800138000"},
                        {"name": "b", "url": "https://b", "owner": {"phone": "139", "name": "bob"}},
                        {"name": "c", "url": "https://c", "owner": "alice,137"},
                    ],
                }
            ]
        }
    )
    owners = [t.owner for t in cfg.environments[0].http_targets]
    assert owners[0].phone == "13800138000"
    assert owners[1].name == "bob" and owners[1].phone == "139"
    assert owners[2].name == "alice" and owners[2].phone == "137"


def test_parse_targets_dubbo_block(tmp_path):
    cfg = parse_targets(
        {
            "environments": [
                {
                    "name": "e",
                    "dubbo": {
                        "zk_hosts": "zk1:2181,zk2:2181",
                        "root_path": "services_e",
                        "applications": ["demo-server"],
                        "exclude_interfaces": ["*.Debug*"],
                        "owners": {"demo-server": {"desc": "Demo", "phone": "138"}},
                    },
                }
            ]
        }
    )
    dubbo = cfg.environments[0].dubbo
    assert dubbo.zk_hosts == "zk1:2181,zk2:2181"
    assert dubbo.zk_root == "/services_e"
    assert dubbo.owners["demo-server"].phone == "138"
    assert dubbo.app_descs["demo-server"] == "Demo"
    assert dubbo.owner_for("demo-server").phone == "138"
    assert dubbo.owner_for("unknown") is None


def test_parse_targets_skips_empty_and_urlless(tmp_path):
    cfg = parse_targets(
        {
            "environments": [
                {"name": "empty"},
                {"name": "urlless", "http_targets": [{"name": "x"}]},
            ]
        }
    )
    assert cfg.environments == []


def test_parse_targets_requires_name():
    with pytest.raises(ConfigError):
        parse_targets({"environments": [{"http_targets": []}]})


def test_select_filters_disabled_and_validates_names():
    cfg = parse_targets(
        {
            "environments": [
                {"name": "a", "http_targets": [{"url": "https://a"}]},
                {"name": "b", "enabled": False, "http_targets": [{"url": "https://b"}]},
            ]
        }
    )
    assert [e.name for e in cfg.select(None)] == ["a"]
    assert [e.name for e in cfg.select(None, include_disabled=True)] == ["a", "b"]
    assert [e.name for e in cfg.select(["b"], include_disabled=True)] == ["b"]
    with pytest.raises(ConfigError):
        cfg.select(["nope"])