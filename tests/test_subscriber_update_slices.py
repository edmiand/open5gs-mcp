"""Tests for subscriber_update_slices tool."""

from unittest.mock import patch

import pytest

from tools.subscriber_update_slices import subscriber_update_slices
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
