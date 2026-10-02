"""Tests for send_ue_notification tool."""

from unittest.mock import patch

import httpx
import pytest

from tools.send_ue_notification import send_ue_notification
from conftest import http_response, oam_page, unwrap


# ── sample data ───────────────────────────────────────────────────────────────

_SMF_UE_ACTIVE = {
    "supi": "imsi-999700000000001",
    "pdu": [
        {"psi": 1, "dnn": "internet", "ipv4": "10.45.0.2", "pdu_state": "active"},
    ],
}

_SMF_UE_RELEASED = {
    "supi": "imsi-999700000000001",
    "pdu": [
        {"psi": 1, "dnn": "internet", "ipv4": "10.45.0.2", "pdu_state": "released"},
    ],
}


def _mock_smf_get(smf_data):
    """Return a side_effect for httpx.get serving a single /pdu-info page."""
    def _get(url: str, **kwargs):
        assert "pdu-info" in url
        return http_response(oam_page(smf_data))
    return _get


# ── input validation ──────────────────────────────────────────────────────────

@pytest.mark.unit
class TestValidation:
    def test_missing_imsi(self):
        r = unwrap(send_ue_notification(imsi="", message="hi"))
        assert r["ok"] is False
        assert r["reason"] == "invalid_input"

    def test_invalid_imsi_format(self):
        r = unwrap(send_ue_notification(imsi="not-digits", message="hi"))
        assert r["ok"] is False
        assert r["reason"] == "invalid_input"

    def test_empty_message(self):
        r = unwrap(send_ue_notification(imsi="999700000000001", message=""))
        assert r["ok"] is False
        assert r["reason"] == "invalid_input"

    def test_message_too_long(self):
        r = unwrap(send_ue_notification(imsi="999700000000001", message="x" * 501))
        assert r["ok"] is False
        assert r["reason"] == "invalid_input"
        assert "500" in r["error"]

    def test_message_exactly_max_length_is_valid_input(self):
        # Should pass validation and proceed to session lookup (not an input error)
        with patch("tools.list_ue_sessions.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("refused")
            r = unwrap(send_ue_notification(imsi="999700000000001", message="x" * 500))
        assert r["reason"] == "no_session"

    def test_incident_id_wrong_type(self):
        r = unwrap(send_ue_notification(imsi="999700000000001", message="hi", incident_id=123))
        assert r["ok"] is False
        assert r["reason"] == "invalid_input"

    @pytest.mark.parametrize("bad_port", [0, -1, 65536, 999999])
    def test_invalid_port(self, bad_port):
        r = unwrap(send_ue_notification(imsi="999700000000001", message="hi", port=bad_port))
        assert r["ok"] is False
        assert r["reason"] == "invalid_input"


# ── happy path ────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestHappyPath:
    @patch("tools.send_ue_notification.httpx.post")
    @patch("tools.list_ue_sessions.httpx.get")
    def test_delivered_successfully(self, mock_get, mock_post):
        mock_get.side_effect = _mock_smf_get([_SMF_UE_ACTIVE])
        mock_post.return_value = http_response({"status": "received"}, status_code=200)

        r = unwrap(send_ue_notification(imsi="999700000000001", message="evacuate now"))

        assert r["ok"] is True
        assert r["imsi"] == "999700000000001"
        assert r["ue_ip"] == "10.45.0.2"
        assert r["port"] == 9000
        assert r["http_status"] == 200
        assert isinstance(r["round_trip_ms"], float)
        assert "incident_id" not in r

    @patch("tools.send_ue_notification.httpx.post")
    @patch("tools.list_ue_sessions.httpx.get")
    def test_incident_id_echoed_in_detail(self, mock_get, mock_post):
        mock_get.side_effect = _mock_smf_get([_SMF_UE_ACTIVE])
        mock_post.return_value = http_response({}, status_code=200)

        r = unwrap(send_ue_notification(
            imsi="999700000000001", message="hi", incident_id="INC-42",
        ))
        assert r["ok"] is True
        assert r["incident_id"] == "INC-42"

    @patch("tools.send_ue_notification.httpx.post")
    @patch("tools.list_ue_sessions.httpx.get")
    def test_custom_port_used_in_request(self, mock_get, mock_post):
        mock_get.side_effect = _mock_smf_get([_SMF_UE_ACTIVE])
        mock_post.return_value = http_response({}, status_code=200)

        r = unwrap(send_ue_notification(imsi="999700000000001", message="hi", port=12345))
        assert r["ok"] is True
        assert r["port"] == 12345
        called_url = mock_post.call_args.args[0]
        assert called_url == "http://10.45.0.2:12345/notify"

    @patch("tools.send_ue_notification.httpx.post")
    @patch("tools.list_ue_sessions.httpx.get")
    def test_request_body_shape(self, mock_get, mock_post):
        mock_get.side_effect = _mock_smf_get([_SMF_UE_ACTIVE])
        mock_post.return_value = http_response({}, status_code=200)

        send_ue_notification(imsi="999700000000001", message="hi", incident_id="INC-1")
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {"message": "hi", "incident_id": "INC-1"}
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"] == 5.0

    @patch("tools.send_ue_notification.httpx.post")
    @patch("tools.list_ue_sessions.httpx.get")
    def test_accepts_supi_format(self, mock_get, mock_post):
        mock_get.side_effect = _mock_smf_get([_SMF_UE_ACTIVE])
        mock_post.return_value = http_response({}, status_code=200)

        r = unwrap(send_ue_notification(imsi="imsi-999700000000001", message="hi"))
        assert r["ok"] is True
        assert r["imsi"] == "999700000000001"


# ── error handling ────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestErrorHandling:
    @patch("tools.list_ue_sessions.httpx.get")
    def test_no_matching_ue(self, mock_get):
        mock_get.side_effect = _mock_smf_get([])
        r = unwrap(send_ue_notification(imsi="999700000000001", message="hi"))
        assert r["ok"] is False
        assert r["reason"] == "no_session"

    @patch("tools.list_ue_sessions.httpx.get")
    def test_session_released_no_ip(self, mock_get):
        mock_get.side_effect = _mock_smf_get([_SMF_UE_RELEASED])
        r = unwrap(send_ue_notification(imsi="999700000000001", message="hi"))
        assert r["ok"] is False
        assert r["reason"] == "no_session"

    @patch("tools.list_ue_sessions.httpx.get")
    def test_smf_unreachable(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("refused")
        r = unwrap(send_ue_notification(imsi="999700000000001", message="hi"))
        assert r["ok"] is False
        assert r["reason"] == "no_session"

    @patch("tools.send_ue_notification.httpx.post")
    @patch("tools.list_ue_sessions.httpx.get")
    def test_connection_refused_by_ue(self, mock_get, mock_post):
        mock_get.side_effect = _mock_smf_get([_SMF_UE_ACTIVE])
        mock_post.side_effect = httpx.ConnectError("refused")

        r = unwrap(send_ue_notification(imsi="999700000000001", message="hi"))
        assert r["ok"] is False
        assert r["reason"] == "connection_refused"

    @patch("tools.send_ue_notification.httpx.post")
    @patch("tools.list_ue_sessions.httpx.get")
    def test_timeout_waiting_for_ue(self, mock_get, mock_post):
        mock_get.side_effect = _mock_smf_get([_SMF_UE_ACTIVE])
        mock_post.side_effect = httpx.TimeoutException("timed out")

        r = unwrap(send_ue_notification(imsi="999700000000001", message="hi"))
        assert r["ok"] is False
        assert r["reason"] == "timeout"

    @patch("tools.send_ue_notification.httpx.post")
    @patch("tools.list_ue_sessions.httpx.get")
    def test_other_request_error(self, mock_get, mock_post):
        mock_get.side_effect = _mock_smf_get([_SMF_UE_ACTIVE])
        mock_post.side_effect = httpx.RequestError("boom")

        r = unwrap(send_ue_notification(imsi="999700000000001", message="hi"))
        assert r["ok"] is False
        assert r["reason"] == "request_error"

    @patch("tools.send_ue_notification.httpx.post")
    @patch("tools.list_ue_sessions.httpx.get")
    def test_imsi_not_cached_between_calls(self, mock_get, mock_post):
        """Each call must re-resolve the UE IP — no caching across calls."""
        mock_post.return_value = http_response({}, status_code=200)

        mock_get.side_effect = _mock_smf_get([_SMF_UE_ACTIVE])
        r1 = unwrap(send_ue_notification(imsi="999700000000001", message="hi"))
        assert r1["ue_ip"] == "10.45.0.2"

        moved = {**_SMF_UE_ACTIVE, "pdu": [
            {"psi": 1, "dnn": "internet", "ipv4": "10.45.0.99", "pdu_state": "active"},
        ]}
        mock_get.side_effect = _mock_smf_get([moved])
        r2 = unwrap(send_ue_notification(imsi="999700000000001", message="hi"))
        assert r2["ue_ip"] == "10.45.0.99"
