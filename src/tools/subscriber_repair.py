"""Diagnose and repair corrupted subscriber documents."""

from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from ._subscriber_util import (
	normalize_imsi, get_subscribers_col, serialize, redact, DEFAULT_SUBSCRIBER
)


def subscriber_repair(imsi: str, action: str = "diagnose") -> dict:
	"""
	Diagnose and repair corrupted subscriber documents.

	Args:
		imsi: IMSI digits (10-15) or SUPI ("imsi-<digits>").
		action: One of:
			"diagnose" — compare current doc against schema defaults, report gaps
			"restore_defaults" — reset to defaults, preserving only imsi + security
			"restore_full" — reset to complete defaults (loses all data; use carefully)

	Returns:
		{
			"ok": True,
			"action": "<action>",
			"imsi": "<normalized>",
			"diagnosis": {
				"current_fields": [...],
				"missing_fields": [...],
				"problematic_slices": [...]
			},
			"subscriber": {...}  (current or repaired doc, secrets redacted)
		}
		or
		{"ok": False, "error": str}
	"""
	norm = normalize_imsi(imsi)
	if norm is None:
		return {"ok": False, "error": f"Invalid IMSI '{imsi}'. Expected 10-15 digits or 'imsi-<digits>'."}

	if action not in {"diagnose", "restore_defaults", "restore_full"}:
		return {"ok": False, "error": f"Invalid action '{action}'. Must be one of: diagnose, restore_defaults, restore_full"}

	try:
		col = get_subscribers_col()
		current = col.find_one({"imsi": norm})
		if current is None:
			return {"ok": False, "error": f"Subscriber {norm} not found"}

		current_ser = serialize(current)

		# Diagnose: compare against defaults
		expected_keys = set(DEFAULT_SUBSCRIBER.keys())
		current_keys = set(current_ser.keys()) - {"_id"}
		missing_keys = expected_keys - current_keys

		# Check slices for common issues
		problematic_slices = []
		slices = current_ser.get("slice", [])
		for i, s in enumerate(slices):
			if not isinstance(s, dict):
				problematic_slices.append(f"slice[{i}] is not a dict: {type(s)}")
				continue
			if "sst" not in s:
				problematic_slices.append(f"slice[{i}] missing 'sst'")
			if "session" not in s or not isinstance(s["session"], list):
				problematic_slices.append(f"slice[{i}] missing or invalid 'session' array")
				continue
			for j, session in enumerate(s["session"]):
				if not isinstance(session, dict):
					problematic_slices.append(f"slice[{i}].session[{j}] is not a dict: {type(session)}")
					continue
				if "name" not in session:
					problematic_slices.append(f"slice[{i}].session[{j}] missing 'name' (DNN)")
				if "type" not in session:
					problematic_slices.append(f"slice[{i}].session[{j}] missing 'type' (IPv4/v6 indicator)")
				if "qos" not in session:
					problematic_slices.append(f"slice[{i}].session[{j}] missing 'qos' config")

		diagnosis = {
			"current_fields": sorted(current_keys),
			"missing_top_level_fields": sorted(missing_keys),
			"problematic_slices": problematic_slices,
		}

		if action == "diagnose":
			return {
				"ok": True,
				"action": "diagnose",
				"imsi": norm,
				"diagnosis": diagnosis,
				"subscriber": redact(current_ser),
			}

		if action == "restore_defaults":
			# Keep IMSI and security, reset everything else to defaults
			restored = dict(DEFAULT_SUBSCRIBER)
			restored["imsi"] = norm
			restored["schema_version"] = current_ser.get("schema_version", 1)
			if "security" in current_ser and isinstance(current_ser["security"], dict):
				restored["security"] = current_ser["security"]
			restored.pop("_id", None)
			col.replace_one({"imsi": norm}, restored)
			updated = col.find_one({"imsi": norm})
			return {
				"ok": True,
				"action": "restore_defaults",
				"imsi": norm,
				"note": "Restored to defaults, preserving IMSI and security credentials. Lost: all profile customizations.",
				"subscriber": redact(serialize(updated)),
			}

		if action == "restore_full":
			# Complete reset to defaults
			restored = dict(DEFAULT_SUBSCRIBER)
			restored["imsi"] = norm
			restored.pop("_id", None)
			col.replace_one({"imsi": norm}, restored)
			updated = col.find_one({"imsi": norm})
			return {
				"ok": True,
				"action": "restore_full",
				"imsi": norm,
				"warning": "FULL RESET — all customizations lost. You will need to re-add security credentials!",
				"subscriber": redact(serialize(updated)),
			}

	except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
		return {"ok": False, "error": f"MongoDB connection failed: {exc}"}
	except Exception as exc:
		return {"ok": False, "error": str(exc)}
