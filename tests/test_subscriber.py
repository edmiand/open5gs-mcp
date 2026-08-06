"""Tests for subscriber tool (read / list / create / delete)."""

from unittest.mock import patch, MagicMock

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from tools.subscriber import subscriber
from conftest import make_subscriber, make_mock_col, unwrap


IMSI = "999700000000001"
SUPI = f"imsi-{IMSI}"


# ── unknown action ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_unknown_action():
    r = unwrap(subscriber(action="nuke"))
    assert r["ok"] is False
    assert "nuke" in r["error"]


# ── read ──────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestRead:
    def test_missing_imsi(self):
        r = unwrap(subscriber(action="read"))
        assert r["ok"] is False
        assert "imsi" in r["error"].lower()

    def test_invalid_imsi(self):
        r = unwrap(subscriber(action="read", imsi="abc"))
        assert r["ok"] is False

    @patch("tools.subscriber.get_subscribers_col")
    def test_not_found(self, mock_get_col):
        col = make_mock_col(docs=[])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="read", imsi=IMSI))
        assert r["ok"] is False
        assert "not found" in r["error"]

    @patch("tools.subscriber.get_subscribers_col")
    def test_happy_path_digits(self, mock_get_col):
        col = make_mock_col(docs=[make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="read", imsi=IMSI))
        assert r["ok"] is True
        assert "subscriber" in r

    @patch("tools.subscriber.get_subscribers_col")
    def test_happy_path_supi_format(self, mock_get_col):
        col = make_mock_col(docs=[make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="read", imsi=SUPI))
        assert r["ok"] is True

    @patch("tools.subscriber.get_subscribers_col")
    def test_secrets_redacted(self, mock_get_col):
        col = make_mock_col(docs=[make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="read", imsi=IMSI))
        assert r["ok"] is True
        sec = r["subscriber"]["security"]
        assert sec["k"] == "***"
        assert sec["opc"] == "***"

    @patch("tools.subscriber.get_subscribers_col")
    def test_mongodb_error(self, mock_get_col):
        mock_get_col.side_effect = ServerSelectionTimeoutError("timeout")
        r = unwrap(subscriber(action="read", imsi=IMSI))
        assert r["ok"] is False
        assert "MongoDB" in r["error"]


# ── list ──────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestList:
    def test_limit_too_low(self):
        r = unwrap(subscriber(action="list", limit=0))
        assert r["ok"] is False

    def test_limit_too_high(self):
        r = unwrap(subscriber(action="list", limit=1001))
        assert r["ok"] is False

    def test_unsupported_filter_key(self):
        r = unwrap(subscriber(action="list", filter={"imsi": "123"}))
        assert r["ok"] is False
        assert "filter" in r["error"].lower() or "Unsupported" in r["error"]

    def test_non_scalar_filter_value(self):
        r = unwrap(subscriber(action="list", filter={"subscriber_status": [0, 1]}))
        assert r["ok"] is False

    @patch("tools.subscriber.get_subscribers_col")
    def test_returns_all_subscribers(self, mock_get_col):
        docs = [make_subscriber(f"9997000000000{i:02d}") for i in range(3)]
        col = make_mock_col(docs=docs)
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="list"))
        assert r["ok"] is True
        assert r["count"] == 3
        assert r["returned"] == 3

    @patch("tools.subscriber.get_subscribers_col")
    def test_filter_by_status(self, mock_get_col):
        col = make_mock_col(docs=[make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="list", filter={"subscriber_status": 0}))
        assert r["ok"] is True

    @patch("tools.subscriber.get_subscribers_col")
    def test_secrets_redacted_in_list(self, mock_get_col):
        col = make_mock_col(docs=[make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="list"))
        assert r["ok"] is True
        for doc in r["subscribers"]:
            assert doc["security"]["k"] == "***"


# ── create ────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestCreate:
    def test_missing_imsi(self):
        r = unwrap(subscriber(action="create"))
        assert r["ok"] is False

    def test_invalid_imsi(self):
        r = unwrap(subscriber(action="create", imsi="bad"))
        assert r["ok"] is False

    @patch("tools.subscriber.get_subscribers_col")
    def test_already_exists(self, mock_get_col):
        col = make_mock_col(docs=[make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="create", imsi=IMSI))
        assert r["ok"] is False
        assert "already exists" in r["error"]

    @patch("tools.subscriber.get_subscribers_col")
    def test_happy_path(self, mock_get_col):
        col = make_mock_col(docs=[])  # no existing subscriber
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="create", imsi=IMSI, data={
            "security": {"k": "abc123", "opc": "def456"},
        }))
        assert r["ok"] is True
        assert "subscriber" in r
        assert col.insert_one.called

    @patch("tools.subscriber.get_subscribers_col")
    def test_data_merged_with_defaults(self, mock_get_col):
        col = make_mock_col(docs=[])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="create", imsi=IMSI))
        assert r["ok"] is True
        # Default slice should be present
        assert "slice" in r["subscriber"]


# ── delete ────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestDelete:
    def test_missing_imsi(self):
        r = unwrap(subscriber(action="delete"))
        assert r["ok"] is False

    def test_invalid_imsi(self):
        r = unwrap(subscriber(action="delete", imsi="!@#"))
        assert r["ok"] is False

    @patch("tools.subscriber.get_subscribers_col")
    def test_requires_confirm(self, mock_get_col):
        col = make_mock_col(docs=[make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="delete", imsi=IMSI))
        assert r["ok"] is False
        assert r["confirm_required"] is True
        assert r["subscriber"]["imsi"] == IMSI
        col.delete_one.assert_not_called()

    @patch("tools.subscriber.get_subscribers_col")
    def test_happy_path(self, mock_get_col):
        col = make_mock_col(docs=[make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="delete", imsi=IMSI, confirm=True))
        assert r["ok"] is True
        assert r["deleted"] is True
        assert r["imsi"] == IMSI

    @patch("tools.subscriber.get_subscribers_col")
    def test_not_found_returns_deleted_false(self, mock_get_col):
        col = make_mock_col(docs=[make_subscriber(IMSI)])
        col.delete_one.return_value = MagicMock(deleted_count=0)
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="delete", imsi="999700000000099", confirm=True))
        assert r["ok"] is True
        assert r["deleted"] is False

    @patch("tools.subscriber.get_subscribers_col")
    def test_not_found_does_not_require_confirm(self, mock_get_col):
        # Nothing to preview/delete — should resolve directly without confirm.
        col = make_mock_col(docs=[make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber(action="delete", imsi="999700000000099"))
        assert r["ok"] is True
        assert r["deleted"] is False
