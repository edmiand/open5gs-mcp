"""Tests for list_ue_sessions tool."""

from unittest.mock import patch, MagicMock

import httpx
import pytest

from tools.list_ue_sessions import list_ue_sessions
from conftest import http_response, oam_page, unwrap


# ── sample data ───────────────────────────────────────────────────────────────

_AMF_UE = {
    "supi": "imsi-999700000000001",
    "cm_state": "connected",
    "pdu_sessions": [
        {"psi": 1, "dnn": "internet", "snssai": {"sst": 1}, "n1_released": False, "n2_released": False},
    ],
    "requested_slices": [{"sst": 1}],
    "allowed_slices": [{"sst": 1}],
    "location": None,
    "ambr": None,
}

_SMF_UE = {
    "supi": "imsi-999700000000001",
    "ue_activity": "active",
    "pdu": [
        {"psi": 1, "dnn": "internet", "ipv4": "10.45.0.2", "pdu_state": "active"},
    ],
}


def _make_oam_get(amf_data, smf_data):
    """Return a side_effect function for httpx.get that serves AMF then SMF data."""
    def _get(url: str, **kwargs):
        if "pdu-info" in url:
            return http_response(oam_page(smf_data))
        return http_response(oam_page(amf_data))

    return _get


# ── input validation ──────────────────────────────────────────────────────────

@pytest.mark.unit
class TestValidation:
    def test_invalid_imsi_filter(self):
        r = unwrap(list_ue_sessions(imsi_filter="not-digits"))
        assert r["ok"] is False
        assert "imsi_filter" in r["error"].lower() or "Invalid" in r["error"]

    def test_imsi_filter_non_digit(self):
        r = unwrap(list_ue_sessions(imsi_filter="abc123"))
        assert r["ok"] is False


# ── happy path ────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestHappyPath:
    @patch("tools.list_ue_sessions.httpx.get")
    def test_single_ue_returned(self, mock_get):
        mock_get.side_effect = _make_oam_get([_AMF_UE], [_SMF_UE])
        r = unwrap(list_ue_sessions())
        assert r["ok"] is True
        assert r["ue_count"] == 1
        assert r["ues"][0]["supi"] == "imsi-999700000000001"
        assert r["ues"][0]["imsi"] == "999700000000001"

    @patch("tools.list_ue_sessions.httpx.get")
    def test_pdu_sessions_merged(self, mock_get):
        mock_get.side_effect = _make_oam_get([_AMF_UE], [_SMF_UE])
        r = unwrap(list_ue_sessions())
        assert r["ok"] is True
        sess = r["ues"][0]["pdu_sessions"][0]
        # SMF provides the IP
        assert sess["ipv4"] == "10.45.0.2"
        assert sess["dnn"] == "internet"

    @patch("tools.list_ue_sessions.httpx.get")
    def test_imsi_filter_matches(self, mock_get):
        mock_get.side_effect = _make_oam_get([_AMF_UE], [_SMF_UE])
        r = unwrap(list_ue_sessions(imsi_filter="9997000000000"))
        assert r["ok"] is True
        assert r["ue_count"] == 1

    @patch("tools.list_ue_sessions.httpx.get")
    def test_imsi_filter_no_match(self, mock_get):
        mock_get.side_effect = _make_oam_get([_AMF_UE], [_SMF_UE])
        # 10-digit prefix that doesn't match "999700000000001"
        r = unwrap(list_ue_sessions(imsi_filter="1111000000"))
        assert r["ok"] is True
        assert r["ue_count"] == 0

    @patch("tools.list_ue_sessions.httpx.get")
    def test_imsi_filter_supi_format(self, mock_get):
        mock_get.side_effect = _make_oam_get([_AMF_UE], [_SMF_UE])
        r = unwrap(list_ue_sessions(imsi_filter="imsi-9997000000000"))
        assert r["ok"] is True
        assert r["ue_count"] == 1

    @patch("tools.list_ue_sessions.httpx.get")
    def test_empty_core_no_ues(self, mock_get):
        mock_get.side_effect = _make_oam_get([], [])
        r = unwrap(list_ue_sessions())
        assert r["ok"] is True
        assert r["ue_count"] == 0
        assert r["ues"] == []

    @patch("tools.list_ue_sessions.httpx.get")
    def test_output_schema(self, mock_get):
        mock_get.side_effect = _make_oam_get([_AMF_UE], [_SMF_UE])
        r = unwrap(list_ue_sessions())
        assert r["ok"] is True
        assert "timestamp" in r
        assert "ue_count" in r
        assert "ues" in r
        assert "sources" in r
        ue = r["ues"][0]
        for key in ("supi", "imsi", "cm_state", "ue_activity", "pdu_sessions", "pdu_session_count"):
            assert key in ue, f"UE entry missing key: {key}"

    @patch("tools.list_ue_sessions.httpx.get")
    def test_include_idle_false_filters(self, mock_get):
        idle_ue = {**_AMF_UE, "pdu_sessions": []}
        idle_smf = {**_SMF_UE, "ue_activity": "idle", "pdu": []}
        mock_get.side_effect = _make_oam_get([idle_ue], [idle_smf])
        r = unwrap(list_ue_sessions(include_idle=False))
        assert r["ok"] is True
        assert r["ue_count"] == 0


# ── error handling ────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestErrorHandling:
    @patch("tools.list_ue_sessions.httpx.get")
    def test_amf_unreachable_returns_ok(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("refused")
        r = unwrap(list_ue_sessions())
        assert r["ok"] is True
        assert r["ue_count"] == 0
        assert r["sources"]["amf"] == "unreachable"

    @patch("tools.list_ue_sessions.httpx.get")
    def test_smf_unreachable_amf_data_present(self, mock_get):
        def _get(url, **kwargs):
            if "pdu-info" in url:
                raise httpx.ConnectError("refused")
            return http_response(oam_page([_AMF_UE]))
        mock_get.side_effect = _get
        r = unwrap(list_ue_sessions())
        assert r["ok"] is True
        assert r["ue_count"] == 1  # AMF data still returned
        assert r["sources"]["smf"] == "unreachable"
        # No SMF data so no IP
        assert r["ues"][0]["pdu_sessions"][0]["ipv4"] is None

    @patch("tools.list_ue_sessions.httpx.get")
    def test_timeout_reported_in_sources(self, mock_get):
        mock_get.side_effect = httpx.TimeoutException("timeout")
        r = unwrap(list_ue_sessions())
        assert r["ok"] is True
        assert r["sources"]["amf"] == "timeout"
