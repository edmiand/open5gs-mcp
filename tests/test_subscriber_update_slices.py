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


def _sub_with_dnn(dnn: str) -> dict:
    """Subscriber whose sst=1 slice has a single session named dnn."""
    sub = make_subscriber(IMSI)
    sub["slice"][0]["session"][0]["name"] = dnn
    return sub


# ── replace: input validation ─────────────────────────────────────────────────

@pytest.mark.unit
class TestReplaceValidation:
    def test_missing_action_raises(self):
        with pytest.raises(TypeError):
            subscriber_update_slices(imsi=IMSI, slices=_VALID_SLICE)

    def test_unknown_action(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, action="delete_all"))
        assert r["ok"] is False
        assert "Unknown action" in r["error"]

    def test_invalid_imsi(self):
        r = unwrap(subscriber_update_slices(imsi="bad", action="replace", slices=_VALID_SLICE))
        assert r["ok"] is False

    def test_not_a_list(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, action="replace", slices={"sst": 1}))
        assert r["ok"] is False
        assert "list" in r["error"]

    def test_empty_slices(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, action="replace", slices=[]))
        assert r["ok"] is False
        assert "empty" in r["error"]

    def test_slice_not_a_dict(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, action="replace", slices=["not-a-dict"]))
        assert r["ok"] is False

    def test_slice_missing_sst(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="replace",
            slices=[{"session": [{"name": "internet"}]}],
        ))
        assert r["ok"] is False
        assert "sst" in r["error"]

    def test_slice_missing_session(self):
        r = unwrap(subscriber_update_slices(imsi=IMSI, action="replace", slices=[{"sst": 1}]))
        assert r["ok"] is False
        assert "session" in r["error"]

    def test_empty_session_array(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="replace", slices=[{"sst": 1, "session": []}],
        ))
        assert r["ok"] is False
        assert "empty" in r["error"]

    def test_session_not_a_dict(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="replace", slices=[{"sst": 1, "session": ["bad"]}],
        ))
        assert r["ok"] is False

    def test_session_missing_name(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="replace", slices=[{"sst": 1, "session": [{"type": 3}]}],
        ))
        assert r["ok"] is False
        assert "name" in r["error"]

    def test_subscriber_not_found(self):
        with patch("tools.subscriber_update_slices.get_subscribers_col") as mc:
            mc.return_value = make_mock_col(docs=[])
            r = unwrap(subscriber_update_slices(imsi=IMSI, action="replace", slices=_VALID_SLICE))
        assert r["ok"] is False
        assert "not found" in r["error"]


# ── replace: happy path ───────────────────────────────────────────────────────

@pytest.mark.integration
class TestReplaceHappyPath:
    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_valid_update(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(imsi=IMSI, action="replace", slices=_VALID_SLICE))
        assert r["ok"] is True
        assert col.replace_one.called

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_two_dnns(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="replace", slices=_TWO_DNNs,
        ))
        assert r["ok"] is True

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_secrets_redacted(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(imsi=IMSI, action="replace", slices=_VALID_SLICE))
        assert r["ok"] is True
        sec = r["subscriber"]["security"]
        assert sec["k"] == "***"
        assert sec["opc"] == "***"

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_supi_format_accepted(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(
            imsi=f"imsi-{IMSI}", action="replace", slices=_VALID_SLICE,
        ))
        assert r["ok"] is True


# ── rename_session ────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestRenameSessionValidation:
    def test_missing_sst(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="rename_session", old_name="data", new_name="internet",
        ))
        assert r["ok"] is False
        assert "sst" in r["error"]

    def test_missing_old_name(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="rename_session", sst=1, new_name="internet",
        ))
        assert r["ok"] is False
        assert "old_name" in r["error"]

    def test_missing_new_name(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="rename_session", sst=1, old_name="data",
        ))
        assert r["ok"] is False
        assert "new_name" in r["error"]

    def test_slice_not_found(self):
        with patch("tools.subscriber_update_slices.get_subscribers_col") as mc:
            mc.return_value = make_mock_col([make_subscriber(IMSI)])
            r = unwrap(subscriber_update_slices(
                imsi=IMSI, action="rename_session", sst=99, old_name="data", new_name="internet",
            ))
        assert r["ok"] is False
        assert "sst=99" in r["error"]

    def test_session_not_found(self):
        with patch("tools.subscriber_update_slices.get_subscribers_col") as mc:
            mc.return_value = make_mock_col([make_subscriber(IMSI)])
            r = unwrap(subscriber_update_slices(
                imsi=IMSI, action="rename_session", sst=1, old_name="nonexistent", new_name="internet",
            ))
        assert r["ok"] is False
        assert "nonexistent" in r["error"]

    def test_new_name_already_exists(self):
        sub = make_subscriber(IMSI)
        sub["slice"][0]["session"].append({"name": "internet", "type": 3})
        sub["slice"][0]["session"][0]["name"] = "data"
        with patch("tools.subscriber_update_slices.get_subscribers_col") as mc:
            mc.return_value = make_mock_col([sub])
            r = unwrap(subscriber_update_slices(
                imsi=IMSI, action="rename_session", sst=1, old_name="data", new_name="internet",
            ))
        assert r["ok"] is False
        assert "already exists" in r["error"]

    def test_ambiguous_sst_without_sd_rejected(self):
        """Two slices share sst=1 with different sd; omitting sd must not silently pick one."""
        sub = make_subscriber(IMSI)
        sub["slice"] = [
            {"sst": 1, "sd": "000001", "session": [{"name": "data", "type": 3}]},
            {"sst": 1, "sd": "000002", "session": [{"name": "other", "type": 3}]},
        ]
        with patch("tools.subscriber_update_slices.get_subscribers_col") as mc:
            mc.return_value = make_mock_col([sub])
            r = unwrap(subscriber_update_slices(
                imsi=IMSI, action="rename_session", sst=1, old_name="data", new_name="internet",
            ))
        assert r["ok"] is False
        assert "sd" in r["error"]


@pytest.mark.integration
class TestRenameSessionHappyPath:
    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_renames_dnn_preserving_qos(self, mock_get_col):
        sub = _sub_with_dnn("data")
        col = make_mock_col([sub])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="rename_session", sst=1, old_name="data", new_name="internet",
        ))
        assert r["ok"] is True
        sessions = r["subscriber"]["slice"][0]["session"]
        names = [s["name"] for s in sessions]
        assert "internet" in names
        assert "data" not in names
        assert sessions[0]["qos"]["index"] == 9

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_summary_contains_old_and_new_name(self, mock_get_col):
        col = make_mock_col([_sub_with_dnn("data")])
        mock_get_col.return_value = col
        result = subscriber_update_slices(
            imsi=IMSI, action="rename_session", sst=1, old_name="data", new_name="internet",
        )
        assert "data" in result["summary"]
        assert "internet" in result["summary"]

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_no_duplicate_session_created(self, mock_get_col):
        col = make_mock_col([_sub_with_dnn("data")])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="rename_session", sst=1, old_name="data", new_name="internet",
        ))
        assert r["ok"] is True
        assert len(r["subscriber"]["slice"][0]["session"]) == 1


# ── upsert_session ────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestUpsertSessionValidation:
    def test_missing_sst(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="upsert_session", session={"name": "iotnet"},
        ))
        assert r["ok"] is False
        assert "sst" in r["error"]

    def test_session_not_a_dict(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="upsert_session", sst=1, session="iotnet",
        ))
        assert r["ok"] is False

    def test_session_missing_name(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="upsert_session", sst=1, session={"type": 3},
        ))
        assert r["ok"] is False
        assert "name" in r["error"]

    def test_slice_not_found(self):
        with patch("tools.subscriber_update_slices.get_subscribers_col") as mc:
            mc.return_value = make_mock_col([make_subscriber(IMSI)])
            r = unwrap(subscriber_update_slices(
                imsi=IMSI, action="upsert_session", sst=99, session={"name": "iotnet"},
            ))
        assert r["ok"] is False
        assert "sst=99" in r["error"]


@pytest.mark.integration
class TestUpsertSessionHappyPath:
    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_adds_new_session(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="upsert_session", sst=1,
            session={"name": "iotnet", "type": 1},
        ))
        assert r["ok"] is True
        names = [s["name"] for s in r["subscriber"]["slice"][0]["session"]]
        assert "internet" in names
        assert "iotnet" in names

    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_merges_into_existing_session(self, mock_get_col):
        col = make_mock_col([make_subscriber(IMSI)])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="upsert_session", sst=1,
            session={"name": "internet", "type": 1},
        ))
        assert r["ok"] is True
        sessions = r["subscriber"]["slice"][0]["session"]
        assert len(sessions) == 1
        assert sessions[0]["name"] == "internet"
        assert sessions[0]["type"] == 1        # updated
        assert sessions[0]["qos"]["index"] == 9  # preserved


# ── remove_session ────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestRemoveSessionValidation:
    def test_missing_sst(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="remove_session", name="iotnet",
        ))
        assert r["ok"] is False
        assert "sst" in r["error"]

    def test_missing_name(self):
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="remove_session", sst=1,
        ))
        assert r["ok"] is False
        assert "name" in r["error"]

    def test_session_not_found(self):
        with patch("tools.subscriber_update_slices.get_subscribers_col") as mc:
            mc.return_value = make_mock_col([make_subscriber(IMSI)])
            r = unwrap(subscriber_update_slices(
                imsi=IMSI, action="remove_session", sst=1, name="ghost",
            ))
        assert r["ok"] is False
        assert "ghost" in r["error"]

    def test_refuses_to_remove_last_session(self):
        with patch("tools.subscriber_update_slices.get_subscribers_col") as mc:
            mc.return_value = make_mock_col([make_subscriber(IMSI)])
            r = unwrap(subscriber_update_slices(
                imsi=IMSI, action="remove_session", sst=1, name="internet",
            ))
        assert r["ok"] is False
        assert "last session" in r["error"]


@pytest.mark.integration
class TestRemoveSessionHappyPath:
    @patch("tools.subscriber_update_slices.get_subscribers_col")
    def test_removes_one_of_two_sessions(self, mock_get_col):
        sub = make_subscriber(IMSI)
        sub["slice"][0]["session"].append({"name": "iotnet", "type": 1})
        col = make_mock_col([sub])
        mock_get_col.return_value = col
        r = unwrap(subscriber_update_slices(
            imsi=IMSI, action="remove_session", sst=1, name="iotnet",
        ))
        assert r["ok"] is True
        names = [s["name"] for s in r["subscriber"]["slice"][0]["session"]]
        assert "iotnet" not in names
        assert "internet" in names
