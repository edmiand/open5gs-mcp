"""Tests for read_nf_config tool."""

import pytest
import yaml

import tools.read_nf_config as mod
from tools.read_nf_config import read_nf_config, _resolve_path


# ── fixture: redirect _CONFIG_DIR to tmp_path ─────────────────────────────────

@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_CONFIG_DIR", tmp_path)
    return tmp_path


def write_yaml(config_dir, nf: str, data: dict):
    path = config_dir / f"{nf}.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


_AMF_CONFIG = {
    "logger": {"file": "/var/log/open5gs/amf.log", "level": "info"},
    "amf": {
        "sbi": {
            "server": [{"address": "127.0.0.5", "port": 7777}],
            "client": {"scp": [{"uri": "http://127.0.200.1:7777"}]},
        },
        "guami": [{"plmn_id": {"mcc": "999", "mnc": "70"}, "amf_id": {"region": 2, "set": 1}}],
        "plmn_support": [{"plmn_id": {"mcc": "999", "mnc": "70"}, "s_nssai": [{"sst": 1}]}],
    },
}


# ── _resolve_path unit tests ──────────────────────────────────────────────────

@pytest.mark.unit
class TestResolvePath:
    def test_single_key(self):
        data = {"a": {"b": 1}}
        assert _resolve_path(data, "a") == {"b": 1}

    def test_nested_keys(self):
        data = {"a": {"b": {"c": 42}}}
        assert _resolve_path(data, "a.b.c") == 42

    def test_list_index(self):
        data = {"servers": [{"port": 7777}, {"port": 8888}]}
        assert _resolve_path(data, "servers.0")["port"] == 7777
        assert _resolve_path(data, "servers.1")["port"] == 8888

    def test_missing_key_raises(self):
        data = {"a": 1}
        with pytest.raises(KeyError):
            _resolve_path(data, "b")

    def test_out_of_range_list_index(self):
        data = {"servers": [{"port": 7777}]}
        with pytest.raises(KeyError):
            _resolve_path(data, "servers.5")

    def test_non_integer_list_index(self):
        data = {"servers": [{"port": 7777}]}
        with pytest.raises(KeyError):
            _resolve_path(data, "servers.first")

    def test_traverse_into_scalar_raises(self):
        data = {"port": 7777}
        with pytest.raises(KeyError):
            _resolve_path(data, "port.sub")


# ── input validation ──────────────────────────────────────────────────────────

@pytest.mark.unit
class TestValidation:
    def test_unknown_nf(self, config_dir):
        r = read_nf_config(nf="bogus")
        assert r["ok"] is False
        assert "bogus" in r["error"]

    def test_webui_no_yaml(self, config_dir):
        r = read_nf_config(nf="webui")
        assert r["ok"] is False
        assert "Node.js" in r["error"]

    def test_config_file_not_found(self, config_dir):
        # config_dir is empty — no amf.yaml
        r = read_nf_config(nf="amf")
        assert r["ok"] is False
        assert "not found" in r["error"]

    def test_yaml_parse_error(self, config_dir):
        (config_dir / "amf.yaml").write_text(":\t: invalid yaml {{{{", encoding="utf-8")
        r = read_nf_config(nf="amf")
        assert r["ok"] is False
        assert "YAML" in r["error"]


# ── happy path ────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestHappyPath:
    def test_full_config(self, config_dir):
        write_yaml(config_dir, "amf", _AMF_CONFIG)
        r = read_nf_config(nf="amf")
        assert r["ok"] is True
        assert r["nf"] == "amf"
        assert "config" in r
        assert "config_file" in r
        assert r["path"] is None
        assert r["config"] == _AMF_CONFIG

    def test_dot_path_subtree(self, config_dir):
        write_yaml(config_dir, "amf", _AMF_CONFIG)
        r = read_nf_config(nf="amf", path="amf.sbi")
        assert r["ok"] is True
        assert r["path"] == "amf.sbi"
        assert "server" in r["config"]

    def test_dot_path_scalar(self, config_dir):
        write_yaml(config_dir, "amf", _AMF_CONFIG)
        r = read_nf_config(nf="amf", path="logger.level")
        assert r["ok"] is True
        assert r["config"] == "info"

    def test_list_index_path(self, config_dir):
        write_yaml(config_dir, "amf", _AMF_CONFIG)
        r = read_nf_config(nf="amf", path="amf.sbi.server.0")
        assert r["ok"] is True
        assert r["config"]["port"] == 7777

    def test_nf_case_insensitive(self, config_dir):
        write_yaml(config_dir, "amf", _AMF_CONFIG)
        r = read_nf_config(nf="AMF")
        assert r["ok"] is True
        assert r["nf"] == "amf"

    def test_bad_path_key_returns_error(self, config_dir):
        write_yaml(config_dir, "amf", _AMF_CONFIG)
        r = read_nf_config(nf="amf", path="amf.nonexistent")
        assert r["ok"] is False
        assert "nonexistent" in r["error"] or "not found" in r["error"].lower()

    def test_config_file_path_in_response(self, config_dir):
        write_yaml(config_dir, "smf", {"smf": {}})
        r = read_nf_config(nf="smf")
        assert r["ok"] is True
        assert "smf.yaml" in r["config_file"]
