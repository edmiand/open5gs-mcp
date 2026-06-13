"""Create new subscriber records in Open5GS MongoDB."""

from pymongo.errors import ConnectionFailure, DuplicateKeyError, ServerSelectionTimeoutError

from ._subscriber_util import (
	normalize_imsi, get_subscribers_col, serialize, redact, deep_merge,
	DEFAULT_SUBSCRIBER,
)


def subscriber_create(imsi: str, data: dict | None = None) -> dict:
	"""
	Create a new subscriber record.

	Args:
		imsi: IMSI digits (10-15) or SUPI ("imsi-<digits>"). Must not already exist.
		data: Subscriber fields dict, merged with defaults. Optional.
			Minimum useful payload:
			  {"security": {"k": "<Ki>", "opc": "<OPc>"}}
			Can override any field:
			  {
				"security": {"k": "...", "opc": "...", "sqn": 0},
				"ambr": {"downlink": {"value": 1, "unit": 3}, ...},
				"msisdn": ["+1234567890"],
				"imeisv": ["12345678901234567"],
				"slice": [{"sst": 1, "session": [{"name": "internet", ...}]}],
				"access_restriction_data": 32,
				"subscriber_status": 0,
				"network_access_mode": 0,
				"operator_determined_barring": 0,
				"subscribed_rau_tau_timer": 12
			  }

	Returns:
		{"ok": True, "subscriber": {...}} on success (secrets redacted).
		{"ok": False, "error": str} on failure.
	"""
	norm = normalize_imsi(imsi)
	if norm is None:
		return {"ok": False, "error": f"Invalid IMSI '{imsi}'. Expected 10-15 digits or 'imsi-<digits>'."}

	try:
		col = get_subscribers_col()

		# Check for duplicate
		if col.find_one({"imsi": norm}, {"_id": 1}):
			return {"ok": False, "error": f"Subscriber {norm} already exists. Use subscriber_update_profile to modify it."}

		# Merge data into defaults and insert
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
