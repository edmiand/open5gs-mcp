"""Tests for subscriber_update_profile tool."""

from unittest.mock import patch

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from tools.subscriber_update_profile import subscriber_update_profile
from conftest import make_subscriber, make_mock_col, unwrap


IMSI = "999700000000001"


# ── input validation ──────────────────────────────────────────────────────────

@pytest.mark.unit
class TestValidation:
    def test_invalid_imsi(self):
        r = unwrap(subscriber_update_profile(imsi="bad-imsi"))
        assert r["ok"] is False

    def test_no_fields_supplied(self):
        # Need a real col lookup to get past IMSI validation, so mock it
        with patch("tools.subscriber_update_profile.get_subscribers_col") as mc:
            mc.return_value = make_mock_col([make_subscriber(IMSI)])
            r = unwrap(subscriber_update_profile(imsi=IMSI))
        assert r["ok"] is False
        assert "No profile fields" in r["error"]

    def test_subscriber_not_found(self):
        with patch("tools.subscriber_update_profile.get_subscribers_col") as mc:
            mc.return_value = make_mock_col(docs=[])
            r = unwrap(subscriber_update_profile(imsi=IMSI, subscriber_status=0))
        assert r["ok"] is False
        assert "not found" in r["error"]

    def test_mongodb_error(self):
        with patch("tools.subscriber_update_profile.get_subscribers_col") as mc:
            mc.side_effect = ServerSelectionTimeoutError("timeout")
            r = unwrap(subscriber_update_profile(imsi=IMSI, subscriber_status=0))
        assert r["ok"] is False
        assert "MongoDB" in r["error"]


# ── happy path ────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestHappyPath:
    def _setup_col(self, initial_doc, updated_doc=None):
        col = make_mock_col([initial_doc])
        # find_one returns initial on first call, updated on second
        call_count = [0]
        docs = [initial_doc, updated_doc or initial_doc]
        def _find(query, *a, **kw):
            imsi = query.get("imsi")
            doc = next((d for d in [initial_doc] if d.get("imsi") == imsi), None)
            if call_count[0] == 0:
                call_count[0] += 1
                return doc
            return updated_doc or doc
        col.find_one.side_effect = _find
        return col

    @patch("tools.subscriber_update_profile.get_subscribers_col")
    def test_update_subscriber_status(self, mock_get_col):
        initial = make_subscriber(IMSI)
        updated = {**initial, "subscriber_status": 1}
        col = self._setup_col(initial, updated)
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_profile(imsi=IMSI, subscriber_status=1))
        assert r["ok"] is True
        assert col.replace_one.called

    @patch("tools.subscriber_update_profile.get_subscribers_col")
    def test_update_ambr(self, mock_get_col):
        initial = make_subscriber(IMSI)
        col = self._setup_col(initial)
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_profile(
            imsi=IMSI,
            ambr={"downlink": {"value": 100, "unit": 2}, "uplink": {"value": 50, "unit": 2}},
        ))
        assert r["ok"] is True

    @patch("tools.subscriber_update_profile.get_subscribers_col")
    def test_secrets_redacted(self, mock_get_col):
        initial = make_subscriber(IMSI)
        col = self._setup_col(initial)
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_profile(imsi=IMSI, msisdn=["+1234567890"]))
        assert r["ok"] is True
        sec = r["subscriber"]["security"]
        assert sec["k"] == "***"
        assert sec["opc"] == "***"

    @patch("tools.subscriber_update_profile.get_subscribers_col")
    def test_supi_format_accepted(self, mock_get_col):
        initial = make_subscriber(IMSI)
        col = self._setup_col(initial)
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_profile(imsi=f"imsi-{IMSI}", subscriber_status=0))
        assert r["ok"] is True
