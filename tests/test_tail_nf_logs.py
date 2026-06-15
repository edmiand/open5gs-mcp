"""Tests for tail_nf_logs tool."""

import pytest

import tools.tail_nf_logs as mod
from tools.tail_nf_logs import tail_nf_logs
from conftest import log_line, write_nf_log


# ── fixture: redirect _LOG_DIR to tmp_path ────────────────────────────────────

@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_LOG_DIR", tmp_path)
    return tmp_path


# ── input validation ──────────────────────────────────────────────────────────

@pytest.mark.unit
class TestInputValidation:
    def test_unknown_nf(self):
        r = tail_nf_logs(nf="bogus")
        assert r["ok"] is False
        assert "bogus" in r["error"]

    def test_unknown_nf_in_list(self):
        r = tail_nf_logs(nf=["amf", "fake"])
        assert r["ok"] is False
        assert "fake" in r["error"]

    def test_lines_too_low(self):
        r = tail_nf_logs(lines=0)
        assert r["ok"] is False

    def test_lines_too_high(self):
        r = tail_nf_logs(lines=501)
        assert r["ok"] is False

    def test_bad_level(self):
        r = tail_nf_logs(level="critical")
        assert r["ok"] is False
        assert "level" in r["error"]

    def test_invalid_grep_regex(self):
        r = tail_nf_logs(grep="[unclosed")
        assert r["ok"] is False
        assert "grep" in r["error"].lower() or "pattern" in r["error"].lower()

    def test_bad_since_format(self):
        r = tail_nf_logs(since="not-a-time")
        assert r["ok"] is False


# ── happy path ────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestHappyPath:
    def test_single_nf_returns_lines(self, log_dir):
        write_nf_log(log_dir, "amf", [
            log_line("amf", "INFO", "Registration Request received"),
            log_line("amf", "INFO", "Registration Accept sent"),
        ])
        r = tail_nf_logs(nf="amf", level="info", lines=10)
        assert r["ok"] is True
        assert r["total_matched"] == 2
        assert len(r["lines"]) == 2

    def test_all_nf_merges_results(self, log_dir):
        write_nf_log(log_dir, "amf", [log_line("amf", "INFO", "amf message")])
        write_nf_log(log_dir, "smf", [log_line("smf", "INFO", "smf message")])
        r = tail_nf_logs(nf="all", level="info")
        assert r["ok"] is True
        nfs_present = {entry["nf"] for entry in r["lines"]}
        assert "amf" in nfs_present
        assert "smf" in nfs_present

    def test_list_of_nfs(self, log_dir):
        write_nf_log(log_dir, "amf", [log_line("amf", "INFO", "msg")])
        write_nf_log(log_dir, "ausf", [log_line("ausf", "INFO", "msg")])
        r = tail_nf_logs(nf=["amf", "ausf"])
        assert r["ok"] is True
        assert set(r["nf_counts"].keys()) == {"amf", "ausf"}

    def test_output_fields_present(self, log_dir):
        write_nf_log(log_dir, "amf", [log_line("amf", "INFO", "some event")])
        r = tail_nf_logs(nf="amf")
        assert r["ok"] is True
        required = {"nf", "timestamp", "component", "level", "message", "source"}
        for entry in r["lines"]:
            assert required.issubset(entry.keys()), f"missing fields: {entry}"

    def test_results_sorted_chronologically(self, log_dir):
        from conftest import today as _today
        d = __import__("datetime").date.today().strftime("%m/%d")
        lines = [
            f"{d} 10:00:02.000: [amf] INFO: second\n",
            f"{d} 10:00:01.000: [amf] INFO: first\n",
        ]
        write_nf_log(log_dir, "amf", lines)
        r = tail_nf_logs(nf="amf", level="info")
        assert r["ok"] is True
        msgs = [e["message"] for e in r["lines"]]
        assert msgs == ["first", "second"]

    def test_lines_cap_respected(self, log_dir):
        many = [log_line("amf", "INFO", f"msg {i}") for i in range(20)]
        write_nf_log(log_dir, "amf", many)
        r = tail_nf_logs(nf="amf", lines=5)
        assert r["ok"] is True
        assert len(r["lines"]) <= 5


# ── filtering ─────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestFiltering:
    def test_level_filter_excludes_debug(self, log_dir):
        write_nf_log(log_dir, "amf", [
            log_line("amf", "DEBUG", "noisy debug"),
            log_line("amf", "INFO", "info line"),
            log_line("amf", "ERROR", "error line"),
        ])
        r = tail_nf_logs(nf="amf", level="info")
        levels = {e["level"] for e in r["lines"]}
        assert "DEBUG" not in levels
        assert "INFO" in levels

    def test_level_error_only(self, log_dir):
        write_nf_log(log_dir, "amf", [
            log_line("amf", "INFO", "normal"),
            log_line("amf", "WARNING", "warn"),
            log_line("amf", "ERROR", "boom"),
        ])
        r = tail_nf_logs(nf="amf", level="error")
        assert r["ok"] is True
        assert all(e["level"] in ("ERROR", "CRIT", "FATAL") for e in r["lines"])

    def test_grep_keyword(self, log_dir):
        write_nf_log(log_dir, "amf", [
            log_line("amf", "INFO", "Registration Request"),
            log_line("amf", "INFO", "some other event"),
        ])
        r = tail_nf_logs(nf="amf", grep="Registration")
        assert r["ok"] is True
        assert all("Registration" in e["message"] for e in r["lines"])

    def test_grep_regex(self, log_dir):
        write_nf_log(log_dir, "amf", [
            log_line("amf", "INFO", "PDU Session"),
            log_line("amf", "INFO", "Registration Request"),
            log_line("amf", "INFO", "noise"),
        ])
        r = tail_nf_logs(nf="amf", grep="PDU|Registration")
        matched_msgs = {e["message"] for e in r["lines"]}
        assert "noise" not in matched_msgs
        assert len(r["lines"]) == 2

    def test_since_relative_filters_old_lines(self, log_dir):
        import datetime
        now = datetime.datetime.now()
        two_hrs_ago = (now - datetime.timedelta(hours=2)).strftime("%m/%d %H:%M:%S.000")
        # Timestamp 10 seconds ago — guaranteed within a 1m window
        ten_sec_ago = (now - datetime.timedelta(seconds=10)).strftime("%m/%d %H:%M:%S.000")
        write_nf_log(log_dir, "amf", [
            f"{two_hrs_ago}: [amf] INFO: old message\n",
            f"{ten_sec_ago}: [amf] INFO: recent message\n",
        ])
        r = tail_nf_logs(nf="amf", since="1m")
        assert r["ok"] is True
        msgs = [e["message"] for e in r["lines"]]
        assert "old message" not in msgs
        assert "recent message" in msgs


# ── error handling ────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestErrorHandling:
    def test_missing_log_file_reported(self, log_dir):
        # amf.log doesn't exist in log_dir
        r = tail_nf_logs(nf="amf")
        assert r["ok"] is True  # tool succeeds overall
        assert "amf" in r.get("errors", {})

    def test_missing_file_zero_count(self, log_dir):
        r = tail_nf_logs(nf="amf")
        assert r["nf_counts"].get("amf", 0) == 0

    def test_one_nf_error_doesnt_block_others(self, log_dir):
        # amf.log exists; smf.log does not
        write_nf_log(log_dir, "amf", [log_line("amf", "INFO", "amf event")])
        r = tail_nf_logs(nf=["amf", "smf"])
        assert r["ok"] is True
        assert r["nf_counts"]["amf"] == 1
        assert "smf" in r.get("errors", {})
