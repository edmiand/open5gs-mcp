"""Subscriber lifecycle — read, list, create, delete."""

from typing import Annotated, Literal
from typing_extensions import TypedDict

from pydantic import Field as _PField
from pymongo import ASCENDING
from pymongo.errors import ConnectionFailure, DuplicateKeyError, ServerSelectionTimeoutError

from ._schema_util import ErrorDetail
from ._subscriber_util import (
    normalize_imsi, get_subscribers_col, serialize, redact, deep_merge,
    DEFAULT_SUBSCRIBER, SubscriberDoc,
)


# ── structured output schema ─────────────────────────────────────────────────

class SubscriberReadCreateDetail(TypedDict):
    ok: Literal[True]
    subscriber: SubscriberDoc


class SubscriberListDetail(TypedDict):
    ok: Literal[True]
    subscribers: list[SubscriberDoc]
    count: int
    returned: int


class SubscriberDeleteDetail(TypedDict):
    ok: Literal[True]
    deleted: bool
    imsi: str


class DeleteConfirmRequiredDetail(TypedDict):
    """Returned instead of deleting when confirm=False — irreversible op, ask first."""
    ok: Literal[False]
    error: str
    confirm_required: Literal[True]
    subscriber: SubscriberDoc  # preview of what would be deleted


class SubscriberResult(TypedDict):
    summary: str
    # DeleteConfirmRequiredDetail.ok is Literal[False] too, overlapping
    # ErrorDetail's shape — force left-to-right so the richer, more-specific
    # match (confirm_required/subscriber preserved) wins over the generic one.
    detail: Annotated[
        SubscriberReadCreateDetail
        | SubscriberListDetail
        | SubscriberDeleteDetail
        | DeleteConfirmRequiredDetail
        | ErrorDetail,
        _PField(union_mode="left_to_right"),
    ]

def _wrap_subscriber(action: str, result: dict) -> dict:
    if result.get("confirm_required"):
        summary = f"Confirmation required: {result['error']}"
    elif not result.get("ok"):
        summary = f"Error: {result.get('error', 'unknown error')}"
    elif action == "read":
        summary = f"Subscriber {result['subscriber']['imsi']} found."
    elif action == "list":
        summary = f"Listed {result['returned']} of {result['count']} subscriber(s)."
    elif action == "create":
        summary = f"Subscriber {result['subscriber']['imsi']} created."
    else:  # delete
        summary = (
            f"Subscriber {result['imsi']} deleted." if result.get("deleted")
            else f"Subscriber {result['imsi']} not found; nothing deleted."
        )
    return {"summary": summary, "detail": result}


_SAFE_LIST_FILTER_KEYS = frozenset({
    "subscriber_status",
    "network_access_mode",
    "access_restriction_data",
    "operator_determined_barring",
})


def subscriber(
    action: str,
    imsi: str | None = None,
    data: dict | None = None,
    limit: int = 100,
    filter: dict | None = None,
    confirm: bool = False,
) -> SubscriberResult:
    """
    Manage subscriber lifecycle.

    Args:
        action: One of "read", "list", "create", "delete".

        imsi:   Required for read/create/delete.
                IMSI digits (10-15) or SUPI ("imsi-<digits>").

        data:   For create only. Subscriber fields deep-merged with defaults.
                Example:
                  {
                    "security": {"k": "<Ki>", "opc": "<OPc>", "sqn": 0},
                    "ambr": {"downlink": {"value": 1, "unit": 3},
                             "uplink":   {"value": 1, "unit": 3}},
                    "msisdn": ["+1234567890"],
                    "slice": [{"sst": 1, "session": [{"name": "internet"}]}],
                    "subscriber_status": 0,
                    "network_access_mode": 0,
                    "operator_determined_barring": 0,
                    "subscribed_rau_tau_timer": 12
                  }

        limit:  For list only. Max documents to return (1-1000, default 100).

        filter: For list only. Optional equality filter dict. Allowed keys:
                  subscriber_status         (0=SERVICE_GRANTED, 1=OPERATOR_DETERMINED_BARRING)
                  network_access_mode       (0=PACKET_AND_CIRCUIT, 1=RESERVED, 2=ONLY_PACKET)
                  access_restriction_data   (int)
                  operator_determined_barring (int)
                Example: {"subscriber_status": 1} lists all barred subscribers.

        confirm: For delete only. Deletion is irreversible — the first call
                (confirm=False, the default) does not delete anything; it
                returns the subscriber that would be deleted so the caller
                can review it, then must re-call with confirm=True to
                actually delete.

    Returns:
        read:   {"ok": True, "subscriber": {...}} — secrets redacted
        list:   {"ok": True, "subscribers": [...], "count": int, "returned": int}
                  count = total matching documents in DB; returned = documents in this page (≤ limit)
        create: {"ok": True, "subscriber": {...}} — secrets redacted
        delete (confirm=False): {"ok": False, "confirm_required": True,
                  "subscriber": {...}, "error": str} — nothing deleted yet
        delete (confirm=True):  {"ok": True, "deleted": bool, "imsi": str}
        error:  {"ok": False, "error": str}
    """
    if action == "read":
        return _wrap_subscriber("read",   _read(imsi))
    if action == "list":
        return _wrap_subscriber("list",   _list(limit, filter))
    if action == "create":
        return _wrap_subscriber("create", _create(imsi, data))
    if action == "delete":
        return _wrap_subscriber("delete", _delete(imsi, confirm))
    _e = f"Unknown action '{action}'. Valid: read, list, create, delete"
    return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}


def _read(imsi: str | None) -> dict:
    if not imsi:
        return {"ok": False, "error": "imsi is required for action='read'"}
    norm = normalize_imsi(imsi)
    if norm is None:
        return {"ok": False, "error": f"Invalid IMSI '{imsi}'. Expected 10-15 digits or 'imsi-<digits>'."}
    try:
        col = get_subscribers_col()
        doc = col.find_one({"imsi": norm})
        if doc is None:
            return {"ok": False, "error": f"Subscriber {norm} not found"}
        return {"ok": True, "subscriber": redact(serialize(doc))}
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        return {"ok": False, "error": f"MongoDB connection failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _list(limit: int, filter: dict | None) -> dict:
    if not (1 <= limit <= 1000):
        return {"ok": False, "error": "limit must be between 1 and 1000"}
    try:
        col = get_subscribers_col()
        mongo_filter: dict = {}
        if filter:
            bad_keys = [k for k in filter if k not in _SAFE_LIST_FILTER_KEYS]
            if bad_keys:
                return {"ok": False, "error": f"Unsupported filter key(s): {bad_keys}. Allowed: {sorted(_SAFE_LIST_FILTER_KEYS)}"}
            bad_vals = [k for k, v in filter.items() if not isinstance(v, (str, int, float, bool))]
            if bad_vals:
                return {"ok": False, "error": f"Filter values must be scalar (str/int/bool), got non-scalar for: {bad_vals}"}
            mongo_filter = dict(filter)
        total = col.count_documents(mongo_filter)
        docs = list(col.find(mongo_filter, limit=limit).sort("imsi", ASCENDING))
        return {"ok": True, "subscribers": [redact(serialize(d)) for d in docs], "count": total, "returned": len(docs)}
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        return {"ok": False, "error": f"MongoDB connection failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _create(imsi: str | None, data: dict | None) -> dict:
    if not imsi:
        return {"ok": False, "error": "imsi is required for action='create'"}
    norm = normalize_imsi(imsi)
    if norm is None:
        return {"ok": False, "error": f"Invalid IMSI '{imsi}'. Expected 10-15 digits or 'imsi-<digits>'."}
    try:
        col = get_subscribers_col()
        if col.find_one({"imsi": norm}, {"_id": 1}):
            return {"ok": False, "error": f"Subscriber {norm} already exists. Use subscriber_update_profile to modify it."}
        doc = deep_merge(DEFAULT_SUBSCRIBER, data or {})
        doc["imsi"] = norm
        col.insert_one(doc)
        return {"ok": True, "subscriber": redact(serialize(doc))}
    except DuplicateKeyError:
        return {"ok": False, "error": f"Subscriber {norm} already exists"}
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        return {"ok": False, "error": f"MongoDB connection failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _delete(imsi: str | None, confirm: bool) -> dict:
    if not imsi:
        return {"ok": False, "error": "imsi is required for action='delete'"}
    norm = normalize_imsi(imsi)
    if norm is None:
        return {"ok": False, "error": f"Invalid IMSI '{imsi}'. Expected 10-15 digits or 'imsi-<digits>'."}
    try:
        col = get_subscribers_col()
        doc = col.find_one({"imsi": norm})
        if doc is None:
            return {"ok": True, "deleted": False, "imsi": norm}

        if not confirm:
            return {
                "ok": False,
                "confirm_required": True,
                "subscriber": redact(serialize(doc)),
                "error": (
                    f"Deleting subscriber {norm} is irreversible. Review the "
                    f"subscriber above, then re-call with confirm=True to delete it."
                ),
            }

        result = col.delete_one({"imsi": norm})
        return {"ok": True, "deleted": result.deleted_count == 1, "imsi": norm}
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        return {"ok": False, "error": f"MongoDB connection failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
