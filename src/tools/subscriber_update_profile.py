"""Update subscriber profile parameters in Open5GS MongoDB."""

from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from ._subscriber_util import (
	normalize_imsi, get_subscribers_col, serialize, redact, deep_merge
)


def subscriber_update_profile(
	imsi: str,
	security: dict | None = None,
	ambr: dict | None = None,
	msisdn: list | None = None,
	imeisv: list | None = None,
	mme_host: list | None = None,
	mme_realm: list | None = None,
	purge_flag: list | None = None,
	access_restriction_data: int | None = None,
	subscriber_status: int | None = None,
	network_access_mode: int | None = None,
	operator_determined_barring: int | None = None,
	subscribed_rau_tau_timer: int | None = None,
) -> dict:
	"""
	Update subscriber profile parameters (non-slice fields).

	Args:
		imsi: IMSI digits (10-15) or SUPI ("imsi-<digits>").

		security: Update security credentials. Fields:
		  {
			"k": "<Ki hex string>",
			"opc": "<OPc hex string>",
			"amf": "<AMF hex string>",
			"sqn": <sequence number>,
			"rand": "<random challenge hex string>"
		  }
		  Only supplied fields are updated (deep merge).

		ambr: Update aggregate max bitrate. Fields:
		  {
			"downlink": {"value": <int>, "unit": <0-3>},
			"uplink": {"value": <int>, "unit": <0-3>}
		  }
		  Units: 0=bps, 1=Kbps, 2=Mbps, 3=Gbps

		msisdn: List of phone numbers. E.g., ["+1234567890"]

		imeisv: List of IMEI-SV device identifiers.
		  E.g., ["12345678901234567"]

		mme_host: List of legacy MME hostnames (4G integration).

		mme_realm: List of legacy MME realms (4G integration).

		purge_flag: List of boolean purge indicators (per IMSI).

		access_restriction_data: Integer (default 32).
		  Bitmask of access restrictions (3GPP spec).
		  32 = Handover to Non-3GPP Not Allowed (typical).

		subscriber_status: Integer (default 0).
		  0 = service_granted (normal operation)
		  1 = operator_barring (subscriber cannot register)

		network_access_mode: Integer (default 0).
		  0 = packet_and_circuit
		  1 = only_packet (LTE/5G only)
		  2 = only_circuit (2G/3G only)

		operator_determined_barring: Integer (default 0).
		  0 = no barring
		  Other values per 3GPP spec.

		subscribed_rau_tau_timer: Integer (default 12).
		  Timer value in minutes for RAU/TAU (routing area update / tracking area update).
		  Legacy 4G parameter; mostly unused in 5G.

	Returns:
		{"ok": True, "subscriber": {...}} on success (secrets redacted).
		{"ok": False, "error": str} on failure.

	Note:
		Only supplied parameters are updated (deep merge for nested fields).
		To update slice/session configuration, use subscriber_update_slices instead.
	"""
	norm = normalize_imsi(imsi)
	if norm is None:
		_e = f"Invalid IMSI '{imsi}'. Expected 10-15 digits or 'imsi-<digits>'."
		return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

	try:
		col = get_subscribers_col()
		existing = col.find_one({"imsi": norm})
		if existing is None:
			_e = f"Subscriber {norm} not found"
			return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

		# Build update data from supplied kwargs (skip None values)
		update_data: dict = {}
		if security is not None:
			update_data["security"] = security
		if ambr is not None:
			update_data["ambr"] = ambr
		if msisdn is not None:
			update_data["msisdn"] = msisdn
		if imeisv is not None:
			update_data["imeisv"] = imeisv
		if mme_host is not None:
			update_data["mme_host"] = mme_host
		if mme_realm is not None:
			update_data["mme_realm"] = mme_realm
		if purge_flag is not None:
			update_data["purge_flag"] = purge_flag
		if access_restriction_data is not None:
			update_data["access_restriction_data"] = access_restriction_data
		if subscriber_status is not None:
			update_data["subscriber_status"] = subscriber_status
		if network_access_mode is not None:
			update_data["network_access_mode"] = network_access_mode
		if operator_determined_barring is not None:
			update_data["operator_determined_barring"] = operator_determined_barring
		if subscribed_rau_tau_timer is not None:
			update_data["subscribed_rau_tau_timer"] = subscribed_rau_tau_timer

		if not update_data:
			return {"summary": "Error: No profile fields supplied to update.",
					"detail": {"ok": False, "error": "No profile fields supplied to update"}}

		# Deep merge into existing doc
		merged = deep_merge(serialize(existing), update_data)
		merged.pop("_id", None)  # Remove MongoDB internal ID before replace
		col.replace_one({"imsi": norm}, merged)

		return {"summary": f"Profile updated for subscriber {norm}.",
				"detail": {"ok": True, "subscriber": redact(serialize(merged))}}

	except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
		_e = f"MongoDB connection failed: {exc}"
		return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}
	except Exception as exc:
		return {"summary": f"Error: {exc}", "detail": {"ok": False, "error": str(exc)}}
