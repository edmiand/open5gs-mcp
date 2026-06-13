"""Read subscriber records from Open5GS MongoDB."""

from pymongo import ASCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from ._subscriber_util import (
	normalize_imsi, get_subscribers_col, serialize, redact
)

_SAFE_LIST_FILTER_KEYS = frozenset({
	"subscriber_status",
	"network_access_mode",
	"access_restriction_data",
	"operator_determined_barring",
})


def subscriber_read(imsi: str) -> dict:
	"""
	Read a single subscriber record by IMSI.

	Args:
		imsi: IMSI digits (10-15) or SUPI ("imsi-<digits>").

	Returns:
		{"ok": True, "subscriber": {...}} on success.
		{"ok": False, "error": str} on failure.
	"""
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


def subscriber_list(limit: int = 100, filter: dict | None = None) -> dict:
	"""
	List subscribers with optional filtering.

	Args:
		limit: Max documents to return (1–1000, default 100).
		filter: Optional equality filter. Allowed keys (only):
			subscriber_status (0=service_granted, 1=operator_barring)
			network_access_mode (0=packet_and_circuit, 1=only_packet, 2=only_circuit)
			access_restriction_data (e.g., 32)
			operator_determined_barring (0=no_barring, other values barred)
			Only scalar (str/int/bool) values allowed.

	Returns:
		{"ok": True, "subscribers": [...], "count": int} on success.
		{"ok": False, "error": str} on failure.
	"""
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

		docs = list(col.find(mongo_filter, limit=limit).sort("imsi", ASCENDING))
		return {
			"ok": True,
			"subscribers": [redact(serialize(d)) for d in docs],
			"count": len(docs),
		}

	except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
		return {"ok": False, "error": f"MongoDB connection failed: {exc}"}
	except Exception as exc:
		return {"ok": False, "error": str(exc)}
