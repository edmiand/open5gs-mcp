"""Tests for subscriber_update_slices tool."""

import copy
from unittest.mock import patch

import pytest

from tools.subscriber_update_slices import subscriber_update_slices, _merge_slices, _merge_sessions
from conftest import make_subscriber, make_mock_col, unwrap


IMSI = "999700000000001"

_VALID_SLICE = [
    {
        "sst": 1,
        "default_indicator": True,
        "session": [{"name": "internet", "type": 3}],
    }
]

_TWO_DNNs = [
    {
        "sst": 1,
        "session": [
            {"name": "internet", "type": 3},
            {"name": "iotnet", "type": 3},
        ],
    }
]


# ── input validation ──────────────────────────────────────────────────────────

@pytest.mark.unit
class TestValidation:
    def test_invalid_imsi(self):
        r = unwrap(subscriber_update_slices(imsi="bad", slices=_VALID_SLICE))
        assert r["ok"] is False

    def test_not_a_list(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices={"sst": 1}))
        assert r["ok"] is False
        assert "list" in r["error"]

    def test_empty_slices(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=[]))
        assert r["ok"] is False
        assert "empty" in r["error"]

    def test_slice_not_a_dict(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=["not-a-dict"]))
        assert r["ok"] is False

    def test_slice_missing_sst(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI,
            slices=[{"session": [{"name": "internet"}]}],
        ))
        assert r["ok"] is False
        assert "sst" in r["error"]

    def test_slice_missing_session(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=[{"sst": 1}]))
        assert r["ok"] is False
        assert "session" in r["error"]

    def test_empty_session_array(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=[{"sst": 1, "session": []}]))
        assert r["ok"] is False
        assert "empty" in r["error"]

    def test_session_not_a_dict(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=[{"sst": 1, "session": ["bad"]}]))
        assert r["ok"] is False

    def test_session_missing_name(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=[{"sst": 1, "session": [{"type": 3}]}]))
        assert r["ok"] is False
        assert "name" in r["error"]

    def test_subscriber_not_found(self):
        with patch("tools.subscriber_update_slices.get_subscribers_col") as mc:
            mc.return_value = make_mock_col(docs=[])
            r = unwrap(subscriber_update_slices(imsi=IMSI, slices=_VALID_SLICE))
        assert r["ok"] is False
        assert "not found" in r["error"]


# ── unit tests for merge helpers ──────────────────────────────────────────────

@pytest.mark.unit
class TestMergeHelpers:
    def test_merge_sessions_preserves_existing_fields(self):
        existing = [{"name": "internet", "type": 3, "ambr": {"downlink": {"value": 1, "unit": 3}}}]
        incoming = [{"name": "internet", "type": 1}]
        result = _merge_sessions(existing, incoming)
        assert len(result) == 1
        assert result[0]["type"] == 1
        assert result[0]["ambr"] == {"downlink": {"value": 1, "unit": 3}}

    def test_merge_sessions_adds_new_session(self):
        existing = [{"name": "internet", "type": 3}]
        incoming = [{"name": "iotnet", "type": 1}]
        result = _merge_sessions(existing, incoming)
        assert len(result) == 2
        assert result[0]["name"] == "internet"
        assert result[1]["name"] == "iotnet"

    def test_merge_sessions_existing_order_preserved(self):
        existing = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        incoming = [{"name": "b", "type": 1}]
        result = _merge_sessions(existing, incoming)
        assert [s["name"] for s in result] == ["a", "b", "c"]
        assert result[1]["type"] == 1

    def test_merge_slices_preserves_unmentioned_slice(self):
        existing = [
            {"sst": 1, "session": [{"name": "internet", "type": 3}]},
            {"sst": 2, "session": [{"name": "ims", "type": 1}]},
        ]
        incoming = [{"sst": 1, "session": [{"name": "internet", "type": 1}]}]
        result = _merge_slices(existing, incoming)
        assert len(result) == 2
        assert result[1]["sst"] == 2

    def test_merge_slices_adds_new_slice(self):
        existing = [{"sst": 1, "session": [{"name": "internet", "type": 3}]}]
        incoming = [{"sst": 2, "session": [{"name": "ims", "type": 1}]}]
        result = _merge_slices(existing, incoming)
        assert len(result) == 2
        assert result[0]["sst"] == 1
        assert result[1]["sst"] == 2

    def test_merge_slices_sd_distinguishes_slices(self):
        existing = [
            {"sst": 1, "sd": "000001", "session": [{"name": "internet"}]},
            {"sst": 1, "sd": "000002", "session": [{"name": "ims"}]},
        ]
        incoming = [{"sst": 1, "sd": "000001", "session": [{"name": "internet", "type": 1}]}]
        result = _merge_slices(existing, incoming)
        assert len(result) == 2
        assert result[0]["session"][0]["type"] == 1
        # sd=000002 slice untouched
        assert result[1]["session"][0]["name"] == "ims"


# ── happy path ────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestHappyPath:
    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_valid_update(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=_VALID_SLICE))
        assert r["ok"] is True
        assert col.replace_one.called

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_two_dnns(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=_TWO_DNNs))
        assert r["ok"] is True

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_secrets_redacted(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=_VALID_SLICE))
        assert r["ok"] is True
        sec = r["subscriber"]["security"]
        assert sec["k"] == "***"
        assert sec["opc"] == "***"

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_supi_format_accepted(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(imsi=f"imsi-{IMSI}", slices=_VALID_SLICE))
        assert r["ok"] is True


# ── merge mode (default) ──────────────────────────────────────────────────────

@pytest.mark.integration
class TestMergeMode:
    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_partial_session_preserves_qos_and_ambr(self, mock_get_col):
        """Agent sending only name+type must not wipe existing QoS/AMBR."""
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        minimal = [{"sst": 1, "session": [{"name": "internet", "type": 3}]}]
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=minimal))
        assert r["ok"] is True
        slices = r["subscriber"]["slice"]
        session = slices[0]["session"][0]
        assert "qos" in session, "qos was wiped by merge"
        assert "ambr" in session, "ambr was wiped by merge"

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_add_second_dnn_keeps_first(self, mock_get_col):
        """Adding iotnet session must leave existing internet session intact."""
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(
            imsi=IMSI,
            slices=[{"sst": 1, "session": [{"name": "iotnet", "type": 1}]}],
        ))
        assert r["ok"] is True
        sessions = r["subscriber"]["slice"][0]["session"]
        names = [s["name"] for s in sessions]
        assert "internet" in names
        assert "iotnet" in names

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_summary_says_merged(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        result = subscriber_update_slices(imsi=IMSI, slices=_VALID_SLICE)
        assert "merged" in result["summary"]


# ── replace mode ──────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestReplaceMode:
    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_replace_discards_existing_sessions(self, mock_get_col):
        """replace=True must discard existing sessions not in the new config."""
        sub = make_subscriber(IMSI)
        # Add a second session to the existing subscriber
        sub["slice"][0]["session"].append({"name": "iotnet", "type": 1})
        col = make_mock_col([sub])
        mock_get_col.return_value = col
        new_slices = [{"sst": 1, "session": [{"name": "internet", "type": 3}]}]
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=new_slices, replace=True))
        assert r["ok"] is True
        sessions = r["subscriber"]["slice"][0]["session"]
        assert len(sessions) == 1
        assert sessions[0]["name"] == "internet"

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_replace_strips_unspecified_session_fields(self, mock_get_col):
        """replace=True: session written as-is, no field inheritance."""
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        bare = [{"sst": 1, "session": [{"name": "internet", "type": 3}]}]
        r = unwrap(subscriber_update_slices(imsi=IMSI, slices=bare, replace=True))
        assert r["ok"] is True
        session = r["subscriber"]["slice"][0]["session"][0]
        assert "qos" not in session
        assert "ambr" not in session

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_summary_says_replaced(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        result = subscriber_update_slices(imsi=IMSI, slices=_VALID_SLICE, replace=True)
        assert "replaced" in result["summary"]
