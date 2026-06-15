"""Tests for nf_lifecycle tool."""

import subprocess
from unittest.mock import patch

import pytest

from tools.nf_lifecycle import nf_lifecycle, _parse_status, _parse_lifecycle
from conftest import completed


# ── fixture: a real (temp) script file so _SCRIPT.exists() is True ────────────

@pytest.fixture
def script(tmp_path, monkeypatch):
    import tools.nf_lifecycle as mod
    sh = tmp_path / "open5gs-ctl.sh"
    sh.write_text("#!/bin/bash\n")
    monkeypatch.setattr(mod, "_SCRIPT", sh)
    return sh


# ── parser unit tests ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestParsers:
    def test_parse_status_running(self):
        out = "amf running 1234 0:05:32\n"
        result = _parse_status(out)
        assert result["amf"]["status"] == "running"
        assert result["amf"]["pid"] == 1234

    def test_parse_status_stopped(self):
        out = "smf stopped\n"
        result = _parse_status(out)
        assert result["smf"]["status"] == "stopped"
        assert result["smf"]["pid"] is None

    def test_parse_status_multi_nf(self):
        out = "amf running 1234 0:01:00\nsmf running 5678 0:01:00\n"
        result = _parse_status(out)
        assert "amf" in result
        assert "smf" in result

    def test_parse_status_skips_unknown_nf(self):
        out = "notanf running 999 0:00:01\namf running 1 0:00:01\n"
        result = _parse_status(out)
        assert "notanf" not in result
        assert "amf" in result

    def test_parse_status_strips_ansi(self):
        out = "\x1b[32mamf\x1b[0m running 1234\n"
        result = _parse_status(out)
        assert "amf" in result

    def test_parse_lifecycle_started(self):
        out = "amf: started (pid 1234)\n"
        result = _parse_lifecycle(out)
        assert result["amf"]["result"] == "started"
        assert result["amf"]["pid"] == 1234

    def test_parse_lifecycle_error(self):
        out = "amf: ERROR - already running\n"
        result = _parse_lifecycle(out)
        assert result["amf"]["result"] == "error"
        assert "message" in result["amf"]

    def test_parse_lifecycle_stopped(self):
        out = "smf: stopped\n"
        result = _parse_lifecycle(out)
        assert result["smf"]["result"] == "stopped"


# ── input validation ──────────────────────────────────────────────────────────

@pytest.mark.unit
class TestValidation:
    def test_invalid_action(self):
        r = nf_lifecycle(action="nuke")
        assert r["ok"] is False
        assert "nuke" in r["error"]

    def test_invalid_nf(self):
        r = nf_lifecycle(action="status", nf=["bogus"])
        assert r["ok"] is False
        assert "bogus" in r["error"]

    def test_invalid_nf_in_mixed_list(self):
        r = nf_lifecycle(action="status", nf=["amf", "invalid"])
        assert r["ok"] is False

    def test_script_not_found(self, tmp_path, monkeypatch):
        import tools.nf_lifecycle as mod
        monkeypatch.setattr(mod, "_SCRIPT", tmp_path / "nonexistent.sh")
        r = nf_lifecycle(action="status")
        assert r["ok"] is False
        assert "not found" in r["error"]


# ── status action ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestStatus:
    @patch("tools.nf_lifecycle.subprocess.run")
    def test_status_all_nfs(self, mock_run, script):
        mock_run.return_value = completed(stdout=(
            "amf running 1234 0:01:00\n"
            "smf running 5678 0:01:00\n"
            "upf stopped\n"
        ))
        r = nf_lifecycle(action="status")
        assert r["ok"] is True
        assert r["action"] == "status"
        assert "amf" in r["nfs"]
        assert r["nfs"]["amf"]["status"] == "running"
        assert r["nfs"]["amf"]["pid"] == 1234
        assert r["nfs"]["upf"]["status"] == "stopped"

    @patch("tools.nf_lifecycle.subprocess.run")
    def test_status_single_nf(self, mock_run, script):
        mock_run.return_value = completed(stdout="amf running 1234 0:00:30\n")
        r = nf_lifecycle(action="status", nf="amf")
        assert r["ok"] is True
        assert "amf" in r["nfs"]

    @patch("tools.nf_lifecycle.subprocess.run")
    def test_status_nf_as_list(self, mock_run, script):
        mock_run.return_value = completed(stdout="amf running 1 0:00:01\nsmf running 2 0:00:01\n")
        r = nf_lifecycle(action="status", nf=["amf", "smf"])
        assert r["ok"] is True

    @patch("tools.nf_lifecycle.subprocess.run")
    def test_stderr_included_when_present(self, mock_run, script):
        mock_run.return_value = completed(stdout="amf stopped\n", stderr="some warning")
        r = nf_lifecycle(action="status")
        assert "stderr" in r
        assert r["stderr"] == "some warning"

    @patch("tools.nf_lifecycle.subprocess.run")
    def test_no_stderr_key_when_empty(self, mock_run, script):
        mock_run.return_value = completed(stdout="amf stopped\n", stderr="")
        r = nf_lifecycle(action="status")
        assert "stderr" not in r


# ── lifecycle actions ─────────────────────────────────────────────────────────

@pytest.mark.integration
class TestLifecycle:
    @patch("tools.nf_lifecycle.subprocess.run")
    def test_start(self, mock_run, script):
        mock_run.return_value = completed(stdout="amf: started (pid 9999)\n")
        r = nf_lifecycle(action="start", nf="amf")
        assert r["ok"] is True
        assert r["nfs"]["amf"]["result"] == "started"
        assert r["nfs"]["amf"]["pid"] == 9999

    @patch("tools.nf_lifecycle.subprocess.run")
    def test_stop(self, mock_run, script):
        mock_run.return_value = completed(stdout="amf: stopped\n")
        r = nf_lifecycle(action="stop", nf="amf")
        assert r["ok"] is True
        assert r["nfs"]["amf"]["result"] == "stopped"

    @patch("tools.nf_lifecycle.subprocess.run")
    def test_restart(self, mock_run, script):
        mock_run.return_value = completed(stdout="amf: started (pid 1111)\n")
        r = nf_lifecycle(action="restart", nf="amf")
        assert r["ok"] is True

    @patch("tools.nf_lifecycle.subprocess.run")
    def test_lifecycle_error_marks_ok_false(self, mock_run, script):
        mock_run.return_value = completed(stdout="amf: ERROR - not running\n", returncode=1)
        r = nf_lifecycle(action="stop", nf="amf")
        assert r["ok"] is False
        assert r["nfs"]["amf"]["result"] == "error"

    @patch("tools.nf_lifecycle.subprocess.run")
    def test_subprocess_timeout(self, mock_run, script):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="bash", timeout=60)
        r = nf_lifecycle(action="status")
        assert r["ok"] is False
        assert "timed out" in r["error"]
