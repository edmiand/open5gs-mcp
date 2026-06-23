"""Update subscriber slice and session configuration in Open5GS MongoDB."""

from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from ._subscriber_util import (
	normalize_imsi, get_subscribers_col, serialize, redact, deep_merge
)


def subscriber_update_slices(imsi: str, slices: list) -> dict:
	"""
	Update subscriber slice and session configuration.

	This is separate from subscriber_update_profile because slices involve
	complex nested arrays and are conceptually distinct from profile parameters.

	Args:
		imsi: IMSI digits (10-15) or SUPI ("imsi-<digits>").

		slices: List of slice objects. Each slice:
		  {
			"sst": <int>,              # Slice Service Type (required)
			"sd": "<string>",          # Slice Differentiator (optional)
			"default_indicator": <bool>,  # Is this the default slice? (optional)
			"session": [
			  {
				"name": "<DNN or APN>",  # Data Network Name (required)
				"type": <int>,           # 1=IPv4, 2=IPv6, 3=IPv4v6 (optional)
				"qos": {
				  "index": <5QI>,        # 5G QoS Indicator (optional)
				  "arp": {
					"priority_level": <int>,
					"pre_emption_capability": <0-1>,
					"pre_emption_vulnerability": <0-1>
				  }
				},
				"ambr": {
				  "downlink": {"value": <int>, "unit": <0-3>},
				  "uplink": {"value": <int>, "unit": <0-3>}
				},
				"ue": {
				  "ipv4": "<IP>",        # UE assigned IPv4 (optional)
				  "ipv6": "<IP>"         # UE assigned IPv6 (optional)
				},
				"smf": {
				  "ipv4": "<IP>",        # SMF assigned IPv4 (optional)
				  "ipv6": "<IP>"         # SMF assigned IPv6 (optional)
				},
				"pcc_rule": [
				  {
					"flow": [
					  {
						"direction": <int>,  # 0=down, 1=up, 2=bidir
						"description": "<string>"
					  }
					],
					"qos": {...}           # Same as session.qos
				  }
				],
				"lbo_roaming_allowed": <bool>  # Local breakout roaming
			  }
			]
		  }

		The entire slice array is replaced (not merged). All existing slices
		are discarded; pass your full desired slice configuration.

	Returns:
		{"ok": True, "subscriber": {...}} on success (secrets redacted).
		{"ok": False, "error": str} on failure.

	Example:
		Add a second DNN to an existing slice:
		  slices = [
			{
			  "sst": 1,
			  "default_indicator": True,
			  "session": [
				{"name": "internet", "type": 3, ...},  # Keep existing
				{"name": "iotnet", "type": 3, ...}     # Add new
			  ]
			}
		  ]
		  subscriber_update_slices(imsi, slices)
	"""
	norm = normalize_imsi(imsi)
	if norm is None:
		_e = f"Invalid IMSI '{imsi}'. Expected 10-15 digits or 'imsi-<digits>'."
		return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

	if not isinstance(slices, list):
		return {"summary": "Error: slices must be a list.",
				"detail": {"ok": False, "error": "slices must be a list"}}

	if not slices:
		_e = "slices list cannot be empty; at least one slice with one session is required"
		return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

	try:
		col = get_subscribers_col()
		existing = col.find_one({"imsi": norm})
		if existing is None:
			_e = f"Subscriber {norm} not found"
			return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

		# Validate that each slice has at least one session with a name
		for i, slice_obj in enumerate(slices):
			if not isinstance(slice_obj, dict):
				_e = f"slice[{i}] must be a dict"
				return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}
			if "sst" not in slice_obj:
				_e = f"slice[{i}] missing required field 'sst'"
				return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}
			if "session" not in slice_obj or not isinstance(slice_obj["session"], list):
				_e = f"slice[{i}] missing or invalid 'session' array"
				return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}
			if not slice_obj["session"]:
				_e = f"slice[{i}].session cannot be empty; at least one session is required"
				return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}
			for j, session in enumerate(slice_obj["session"]):
				if not isinstance(session, dict):
					_e = f"slice[{i}].session[{j}] must be a dict"
					return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}
				if "name" not in session:
					_e = f"slice[{i}].session[{j}] missing required field 'name' (DNN)"
					return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

		# Update slices (replace entire array)
		merged = serialize(existing)
		merged["slice"] = slices
		merged.pop("_id", None)
		col.replace_one({"imsi": norm}, merged)

		return {"summary": f"Slice configuration updated for subscriber {norm}.",
				"detail": {"ok": True, "subscriber": redact(serialize(merged))}}

	except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
		_e = f"MongoDB connection failed: {exc}"
		return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}
	except Exception as exc:
		return {"summary": f"Error: {exc}", "detail": {"ok": False, "error": str(exc)}}
