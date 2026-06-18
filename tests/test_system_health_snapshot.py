"""Tests for system_health_snapshot tool."""

from unittest.mock import patch, MagicMock

import pytest

from tools.system_health_snapshot import system_health_snapshot, _NFS
from conftest import unwrap


# ── helpers ───────────────────────────────────────────────────────────────────

def _all_pids_up(nf: str) -> int:
    return 1234


def _all_pids_down(nf: str) -> None:
    return None


def _no_errors(*args, **kwargs):
    return [], None, None


def _with_errors(*args, **kwargs):
    return [{"message": "ERROR: something bad"}], None, None


_MONGODB_OK = {"status": "ok", "subscribers": 5}
_MONGODB_DOWN = {"status": "down", "error": "connection refused"}
_TUN_OK = {"status": "ok", "device": "ogstun"}
_TUN_MISSING = {"status": "missing", "device": "ogstun"}
_RAN_OK = {"status": "ok", "gnbs_connected": 1}
_RAN_NO_GNB = {"status": "no_gnbs", "gnbs_connected": 0}
_RAN_UNREACHABLE = {"status": "unreachable", "gnbs_connected": 0}


# ── input validation ──────────────────────────────────────────────────────────

@pytest.mark.unit
class TestInputValidation:
    def test_log_minutes_zero(self):
        r = unwrap(system_health_snapshot(log_minutes=0))
        assert r["ok"] is False

    def test_log_minutes_too_large(self):
        r = unwrap(system_health_snapshot(log_minutes=1441))
        assert r["ok"] is False

    def test_log_minutes_boundary_valid(self):
        with (
            patch("tools.system_health_snapshot._get_nf_pid", return_value=None),
            patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK),
            patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK),
        ):
            r = unwrap(system_health_snapshot(log_minutes=1))
            assert r["ok"] is True

            r = unwrap(system_health_snapshot(log_minutes=1440))
            assert r["ok"] is True


# ── output schema ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestOutputSchema:
    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_top_level_keys(self, *_):
        r = unwrap(system_health_snapshot())
        assert r["ok"] is True
        assert "timestamp" in r
        assert "nfs" in r
        assert "mongodb" in r
        assert "tun" in r
        assert "ran" in r
        assert "summary" in r

    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_nf_entry_keys(self, *_):
        r = unwrap(system_health_snapshot())
        for nf_name, entry in r["nfs"].items():
            assert "status" in entry, f"{nf_name} missing status"
            assert "pid" in entry
            assert "recent_errors" in entry

    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_summary_keys(self, *_):
        r = unwrap(system_health_snapshot())
        summary = r["summary"]
        for key in ("overall", "nfs_green", "nfs_yellow", "nfs_red", "nfs_total", "mongodb", "tun", "ran"):
            assert key in summary

    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_all_nfs_covered(self, *_):
        r = unwrap(system_health_snapshot())
        assert set(r["nfs"].keys()) == set(_NFS)


# ── NF status classification ──────────────────────────────────────────────────

@pytest.mark.integration
class TestNFStatusClassification:
    @patch("tools.system_health_snapshot._get_nf_pid", return_value=None)
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    def test_nf_down_is_red(self, *_):
        r = unwrap(system_health_snapshot())
        assert r["ok"] is True
        for entry in r["nfs"].values():
            assert entry["status"] == "red"
        assert r["summary"]["nfs_red"] == len(_NFS)

    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_clean_nfs_are_green(self, *_):
        r = unwrap(system_health_snapshot())
        assert r["summary"]["nfs_green"] == len(_NFS)
        assert r["summary"]["nfs_red"] == 0

    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_with_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_nf_with_recent_errors_is_yellow(self, *_):
        r = unwrap(system_health_snapshot())
        for entry in r["nfs"].values():
            assert entry["status"] == "yellow"
        assert r["summary"]["nfs_yellow"] == len(_NFS)

    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="unreachable")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_nf_endpoint_unreachable_is_yellow(self, *_):
        r = unwrap(system_health_snapshot())
        # Only AMF and SMF have info endpoints — they go yellow
        for nf_name in ("amf", "smf"):
            assert r["nfs"][nf_name]["status"] == "yellow"

    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_recent_errors_list_is_strings(self, *_):
        r = unwrap(system_health_snapshot())
        for entry in r["nfs"].values():
            assert isinstance(entry["recent_errors"], list)
            for item in entry["recent_errors"]:
                assert isinstance(item, str)


# ── overall health classification ─────────────────────────────────────────────

@pytest.mark.integration
class TestOverallHealth:
    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_healthy_when_all_green(self, *_):
        r = unwrap(system_health_snapshot())
        assert r["summary"]["overall"] == "healthy"

    @patch("tools.system_health_snapshot._get_nf_pid", return_value=None)
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    def test_critical_when_nfs_down(self, *_):
        r = unwrap(system_health_snapshot())
        assert r["summary"]["overall"] == "critical"

    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_DOWN)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_degraded_when_mongodb_down(self, *_):
        r = unwrap(system_health_snapshot())
        assert r["summary"]["overall"] == "degraded"
        assert r["mongodb"]["status"] == "down"


# ── infrastructure checks ─────────────────────────────────────────────────────

@pytest.mark.integration
class TestInfrastructure:
    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_MISSING)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_OK)
    def test_tun_missing_reported(self, *_):
        r = unwrap(system_health_snapshot())
        assert r["tun"]["status"] == "missing"
        assert r["summary"]["tun"] == "missing"

    @patch("tools.system_health_snapshot._get_nf_pid", return_value=None)
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    def test_ran_skipped_when_amf_down(self, *_):
        r = unwrap(system_health_snapshot())
        # AMF is down so ran check is skipped — should default to unreachable
        assert r["ran"]["status"] == "unreachable"
        assert r["ran"]["gnbs_connected"] == 0

    @patch("tools.system_health_snapshot._get_nf_pid", side_effect=_all_pids_up)
    @patch("tools.system_health_snapshot._read_nf_log", side_effect=_no_errors)
    @patch("tools.system_health_snapshot._probe_nf_endpoint", return_value="n/a")
    @patch("tools.system_health_snapshot._check_mongodb", return_value=_MONGODB_OK)
    @patch("tools.system_health_snapshot._check_tun", return_value=_TUN_OK)
    @patch("tools.system_health_snapshot._check_ran", return_value=_RAN_NO_GNB)
    def test_ran_no_gnbs_reported(self, *_):
        r = unwrap(system_health_snapshot())
        assert r["ran"]["status"] == "no_gnbs"
        assert r["ran"]["gnbs_connected"] == 0
