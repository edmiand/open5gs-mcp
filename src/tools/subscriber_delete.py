"""Delete subscriber records from Open5GS MongoDB."""

from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from ._subscriber_util import normalize_imsi, get_subscribers_col


def subscriber_delete(imsi: str) -> dict:
	"""
	Delete a subscriber record by IMSI.

	Args:
		imsi: IMSI digits (10-15) or SUPI ("imsi-<digits>").

	Returns:
		{"ok": True, "deleted": bool, "imsi": str} on success.
		  deleted=True if subscriber existed and was removed,
		  deleted=False if subscriber did not exist.
		{"ok": False, "error": str} on failure.
	"""
	norm = normalize_imsi(imsi)
	if norm is None:
		return {"ok": False, "error": f"Invalid IMSI '{imsi}'. Expected 10-15 digits or 'imsi-<digits>'."}

	try:
		col = get_subscribers_col()
		result = col.delete_one({"imsi": norm})
		return {
			"ok": True,
			"deleted": result.deleted_count == 1,
			"imsi": norm,
		}

	except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
		return {"ok": False, "error": f"MongoDB connection failed: {exc}"}
	except Exception as exc:
		return {"ok": False, "error": str(exc)}
