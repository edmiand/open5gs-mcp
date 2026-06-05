"""Tests for get_ue_trace tool."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools.ue_trace import get_ue_trace, _normalize_supi, _infer_event


# ── Sample log snippets ───────────────────────────────────────────────────────
# Timestamps use today's date (06/05) so they fall within any reasonable window.

_AMF_LOG = (
    "06/05 10:00:00.100: [amf] INFO: Registration Request [imsi-999700000000001]"
    " (amf_ue_ngap_id=1 ran_ue_ngap_id=1) (nr-gnb.c:123)\n"
    "06/05 10:00:00.200: [amf] INFO: [999700000000001] Authentication Request (amf-sm.c:456)\n"
    "06/05 10:00:00.300: [amf] INFO: [999700000000001] Authentication Response (amf-sm.c:457)\n"
    "06/05 10:00:00.400: [amf] INFO: [999700000000001] Security Mode Command (amf-sm.c:500)\n"
    "06/05 10:00:00.500: [amf] INFO: [999700000000001] Security Mode Complete (amf-sm.c:501)\n"
    "06/05 10:00:00.600: [amf] INFO: [999700000000001] Registration Accept (amf-sm.c:600)\n"
    "06/05 10:00:00.700: [amf] INFO: [999700000000001] PDU Session Establishment Request"
    " pdu_session_id=1 (amf-sm.c:700)\n"
    "06/05 10:00:00.800: [amf] INFO: [999700000000001] PDU Session Establishment Accept (amf-sm.c:800)\n"
)

_AUSF_LOG = (
    "06/05 10:00:00.150: [ausf] INFO: Nausf-UEAuthentication for imsi-999700000000001 (ausf-sm.c:100)\n"
)

_UDM_LOG = (
    "06/05 10:00:00.160: [udm] INFO: Nudm-Authentication for 999700000000001 (udm-sm.c:100)\n"
)

_SMF_LOG = (
    "06/05 10:00:00.750: [smf] INFO: PDU Session Establishment dnn=internet"
    " seid:0x1234 (smf-sm.c:100)\n"
    "06/05 10:00:00.760: [smf] INFO: PFCP Session Establishment seid:0x1234 (smf-sm.c:200)\n"
    "06/05 10:00:00.770: [smf] INFO: UE IP assigned 10.45.0.2 (smf-sm.c:300)\n"
)


def _fake_log(nf: str) -> tuple[str | None, str | None]:
    mapping = {
        "amf": _AMF_LOG,
        "ausf": _AUSF_LOG,
        "udm": _UDM_LOG,
        "smf": _SMF_LOG,
    }
    return mapping.get(nf, ""), None


# ── SUPI normalisation ────────────────────────────────────────────────────────

class TestNormalizeSUPI:
    def test_imsi_dash_prefix(self):
        full, bare = _normalize_supi("imsi-999700000000001")
        assert full == "imsi-999700000000001"
        assert bare == "999700000000001"

    def test_bare_digits(self):
        full, bare = _normalize_supi("999700000000001")
        assert full == "imsi-999700000000001"
        assert bare == "999700000000001"

    def test_imsi_colon_prefix(self):
        full, bare = _normalize_supi("IMSI:999700000000001")
        assert full == "imsi-999700000000001"
        assert bare == "999700000000001"

    def test_uppercase_imsi_dash(self):
        full, bare = _normalize_supi("IMSI-999700000000001")
        assert bare == "999700000000001"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _normalize_supi("not-an-imsi")

    def test_too_short_raises(self):
        with pytest.raises(ValueError):
            _normalize_supi("12345")


# ── Event inference ───────────────────────────────────────────────────────────

class TestInferEvent:
    def test_registration_request(self):
        direction, from_e, to_e, msg_type = _infer_event("amf", "Registration Request received")
        assert msg_type == "Registration Request"
        assert from_e == "UE"
        assert to_e == "AMF"
        assert direction == "inbound"

    def test_registration_accept(self):
        direction, from_e, to_e, msg_type = _infer_event("amf", "Registration Accept sent")
        assert msg_type == "Registration Accept"
        assert from_e == "AMF"
        assert to_e == "UE"
        assert direction == "outbound"

    def test_authentication_request(self):
        _, from_e, to_e, msg_type = _infer_event("amf", "Authentication Request")
        assert msg_type == "Authentication Request"
        assert from_e == "AMF"
        assert to_e == "UE"

    def test_authentication_response(self):
        _, from_e, to_e, msg_type = _infer_event("amf", "Authentication Response")
        assert from_e == "UE"
        assert to_e == "AMF"

    def test_nausf_ue_authentication(self):
        _, from_e, to_e, msg_type = _infer_event("amf", "Nausf-UEAuthentication request sent")
        assert msg_type == "Nausf-UEAuthentication"
        assert from_e == "AMF"
        assert to_e == "AUSF"

    def test_nudm_authentication(self):
        _, from_e, to_e, msg_type = _infer_event("ausf", "Nudm-Authentication call")
        assert msg_type == "Nudm-UEAuthentication"
        assert from_e == "AUSF"
        assert to_e == "UDM"

    def test_pfcp_session_establishment(self):
        _, from_e, to_e, msg_type = _infer_event("smf", "PFCP Session Establishment")
        assert msg_type == "PFCP Session Establishment"
        assert from_e == "SMF"
        assert to_e == "UPF"

    def test_pdu_session_establishment_accept(self):
        _, from_e, to_e, msg_type = _infer_event("amf", "PDU Session Establishment Accept")
        assert from_e == "AMF"
        assert to_e == "UE"

    def test_security_mode_command(self):
        _, from_e, to_e, _ = _infer_event("amf", "Security Mode Command")
        assert from_e == "AMF"
        assert to_e == "UE"

    def test_security_mode_complete(self):
        _, from_e, to_e, _ = _infer_event("amf", "Security Mode Complete")
        assert from_e == "UE"
        assert to_e == "AMF"

    def test_unknown_message_is_internal(self):
        direction, from_e, to_e, _ = _infer_event("amf", "some random debug noise")
        assert direction == "internal"
        assert from_e == "AMF"
        assert to_e == "AMF"

    def test_internal_direction_when_from_eq_to(self):
        direction, from_e, to_e, _ = _infer_event("smf", "PDU Session Modification internal update")
        assert direction == "internal"
        assert from_e == to_e


# ── Full tool tests ───────────────────────────────────────────────────────────

class TestGetUETrace:

    @patch("tools.ue_trace._read_log_tail")
    def test_events_sorted_by_timestamp(self, mock_read):
        mock_read.side_effect = _fake_log
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        timestamps = [e["timestamp"] for e in result["events"]]
        assert timestamps == sorted(timestamps), "events are not in chronological order"

    @patch("tools.ue_trace._read_log_tail")
    def test_registration_success_detected(self, mock_read):
        mock_read.side_effect = _fake_log
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        assert result["summary"]["registration_success"] is True

    @patch("tools.ue_trace._read_log_tail")
    def test_pdu_session_success_detected(self, mock_read):
        mock_read.side_effect = _fake_log
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        assert result["summary"]["pdu_session_success"] is True

    @patch("tools.ue_trace._read_log_tail")
    def test_ue_ip_extracted(self, mock_read):
        mock_read.side_effect = _fake_log
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        assert result["summary"]["ue_ip_assigned"] == "10.45.0.2"

    @patch("tools.ue_trace._read_log_tail")
    def test_mermaid_hint_has_sequencediagram(self, mock_read):
        mock_read.side_effect = _fake_log
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        assert "sequenceDiagram" in result["mermaid_hint"]

    @patch("tools.ue_trace._read_log_tail")
    def test_mermaid_hint_contains_expected_participants(self, mock_read):
        mock_read.side_effect = _fake_log
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        hint = result["mermaid_hint"]
        assert "participant AMF" in hint
        assert "participant UE" in hint
        assert "participant AUSF" in hint

    @patch("tools.ue_trace._read_log_tail")
    def test_bare_imsi_normalized_in_output(self, mock_read):
        mock_read.side_effect = _fake_log
        result = get_ue_trace("999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        assert result["supi"] == "imsi-999700000000001"

    @patch("tools.ue_trace._read_log_tail")
    def test_colon_supi_format_accepted(self, mock_read):
        mock_read.side_effect = _fake_log
        result = get_ue_trace("IMSI:999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        assert result["supi"] == "imsi-999700000000001"

    @patch("tools.ue_trace._read_log_tail")
    def test_missing_log_handled_gracefully(self, mock_read):
        def fake_read_with_upf_error(nf: str):
            if nf == "upf":
                return None, "permission denied"
            return _fake_log(nf)

        mock_read.side_effect = fake_read_with_upf_error
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=1440)
        assert result["ok"] is True, "tool should not fail when a single NF is unreadable"
        assert "upf" in result.get("nf_errors", {})

    @patch("tools.ue_trace._read_log_tail")
    def test_all_logs_missing_returns_empty_events(self, mock_read):
        mock_read.return_value = (None, "log file not found")
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        assert result["events"] == []
        assert result["summary"]["registration_success"] is False
        assert result["summary"]["pdu_session_success"] is False

    def test_invalid_supi_returns_error(self):
        result = get_ue_trace("not-a-valid-imsi")
        assert result["ok"] is False
        assert "error" in result

    def test_invalid_nf_returns_error(self):
        result = get_ue_trace("imsi-999700000000001", include_nfs=["fake_nf"])
        assert result["ok"] is False
        assert "Unknown NF" in result["error"]

    def test_time_window_out_of_range(self):
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=0)
        assert result["ok"] is False

    @patch("tools.ue_trace._read_log_tail")
    def test_events_contain_required_fields(self, mock_read):
        mock_read.side_effect = _fake_log
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        required = {"timestamp", "nf", "level", "direction", "message_type", "from", "to", "raw"}
        for event in result["events"]:
            assert required.issubset(event.keys()), f"event missing fields: {event}"

    @patch("tools.ue_trace._read_log_tail")
    def test_raw_log_lines_keyed_by_nf(self, mock_read):
        mock_read.side_effect = _fake_log
        result = get_ue_trace(
            "imsi-999700000000001",
            time_window_minutes=1440,
            include_nfs=["amf", "ausf"],
        )
        assert result["ok"] is True
        assert "amf" in result["raw_log_lines"]
        assert "ausf" in result["raw_log_lines"]

    @patch("tools.ue_trace._read_log_tail")
    def test_no_registration_when_log_empty(self, mock_read):
        def no_amf_log(nf: str):
            if nf == "amf":
                return "", None
            return _fake_log(nf)

        mock_read.side_effect = no_amf_log
        result = get_ue_trace("imsi-999700000000001", time_window_minutes=1440)
        assert result["ok"] is True
        assert result["summary"]["registration_success"] is False
