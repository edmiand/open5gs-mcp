"""Tests for amf_ran_query tool."""

import json
from unittest.mock import patch

import httpx
import pytest

from tools.amf_ran_query import amf_ran_query
from conftest import completed, oam_page, http_response


# ── sample data ───────────────────────────────────────────────────────────────

_PLMNS_RESPONSE = {
    "connected_gnbs": 1,
    "registered_ues": 2,
    "total_plmns": 1,
    "plmns": [{"mcc": "999", "mnc": "70", "s_nssai": [{"sst": 1}]}],
}

_GNB = {
    "gnb_id": "0x001",
    "plmn": {"mcc": "999", "mnc": "70"},
    "ng": {"sctp": {"peer": "192.168.1.1:38412"}},
    "supported_ta_list": [{"tac": 1}],
    "num_connected_ues": 2,
}


# ── happy path ────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestHappyPath:
    @patch("tools.amf_ran_query.subprocess.run")
    @patch("tools.amf_ran_query.httpx.get")
    def test_returns_plmns_and_gnbs(self, mock_httpx, mock_run):
        mock_run.return_value = completed(stdout=json.dumps(_PLMNS_RESPONSE))
        mock_httpx.return_value = http_response(oam_page([_GNB]))
        r = amf_ran_query()
        assert r["ok"] is True
        assert r["connected_gnbs"] == 1
        assert r["registered_ues"] == 2
        assert len(r["plmns"]) == 1
        assert len(r["gnbs"]) == 1

    @patch("tools.amf_ran_query.subprocess.run")
    @patch("tools.amf_ran_query.httpx.get")
    def test_gnb_fields(self, mock_httpx, mock_run):
        mock_run.return_value = completed(stdout=json.dumps(_PLMNS_RESPONSE))
        mock_httpx.return_value = http_response(oam_page([_GNB]))
        r = amf_ran_query()
        assert r["ok"] is True
        gnb = r["gnbs"][0]
        for key in ("gnb_id", "plmn", "sctp_peer", "supported_ta_list", "num_connected_ues"):
            assert key in gnb, f"gNB missing field: {key}"

    @patch("tools.amf_ran_query.subprocess.run")
    @patch("tools.amf_ran_query.httpx.get")
    def test_output_schema(self, mock_httpx, mock_run):
        mock_run.return_value = completed(stdout=json.dumps(_PLMNS_RESPONSE))
        mock_httpx.return_value = http_response(oam_page([_GNB]))
        r = amf_ran_query()
        for key in ("ok", "connected_gnbs", "registered_ues", "total_plmns", "plmns", "gnbs", "gnbs_status"):
            assert key in r, f"missing key: {key}"

    @patch("tools.amf_ran_query.subprocess.run")
    @patch("tools.amf_ran_query.httpx.get")
    def test_no_gnbs(self, mock_httpx, mock_run):
        plmns_no_gnbs = {**_PLMNS_RESPONSE, "connected_gnbs": 0}
        mock_run.return_value = completed(stdout=json.dumps(plmns_no_gnbs))
        mock_httpx.return_value = http_response(oam_page([]))
        r = amf_ran_query()
        assert r["ok"] is True
        assert r["gnbs"] == []
        assert r["connected_gnbs"] == 0


# ── error handling ────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestErrorHandling:
    @patch("tools.amf_ran_query.subprocess.run")
    def test_curl_failure_returns_error(self, mock_run):
        mock_run.return_value = completed(returncode=1, stderr="connection refused")
        r = amf_ran_query()
        assert r["ok"] is False
        assert "error" in r

    @patch("tools.amf_ran_query.subprocess.run")
    def test_invalid_json_from_curl(self, mock_run):
        mock_run.return_value = completed(stdout="not json")
        r = amf_ran_query()
        assert r["ok"] is False

    @patch("tools.amf_ran_query.subprocess.run")
    @patch("tools.amf_ran_query.httpx.get")
    def test_gnb_endpoint_unreachable_still_ok(self, mock_httpx, mock_run):
        mock_run.return_value = completed(stdout=json.dumps(_PLMNS_RESPONSE))
        mock_httpx.side_effect = httpx.ConnectError("refused")
        r = amf_ran_query()
        assert r["ok"] is True
        assert r["gnbs"] == []
        assert r["gnbs_status"] == "unreachable"

    @patch("tools.amf_ran_query.subprocess.run")
    @patch("tools.amf_ran_query.httpx.get")
    def test_gnb_endpoint_timeout(self, mock_httpx, mock_run):
        mock_run.return_value = completed(stdout=json.dumps(_PLMNS_RESPONSE))
        mock_httpx.side_effect = httpx.TimeoutException("timed out")
        r = amf_ran_query()
        assert r["ok"] is True
        assert r["gnbs_status"] == "timeout"

    @patch("tools.amf_ran_query.subprocess.run")
    def test_subprocess_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="curl", timeout=5)
        r = amf_ran_query()
        assert r["ok"] is False
        assert "timed out" in r["error"]
