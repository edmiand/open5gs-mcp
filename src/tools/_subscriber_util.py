"""Shared utilities for subscriber tools."""

import copy
import re
from typing import Any, TypedDict

from typing_extensions import NotRequired

import bson
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

_MONGO_URI = "mongodb://localhost:27017"
_DB = "open5gs"
_COL = "subscribers"
_IMSI_RE = re.compile(r"^\d{10,15}$")


# ── structured output schema, shared across all three subscriber tools ────────

class SubscriberDoc(TypedDict):
    """The subscriber document shape, as returned to callers (security.k/opc redacted).

    Only `imsi` is guaranteed — read/list/create fetch or produce documents
    that may originate outside this tool (WebUI, older schema versions), and
    the two update tools' responses never carry `_id` (popped before save).
    Every other DEFAULT_SUBSCRIBER template field is therefore NotRequired.
    """
    imsi: str
    _id: NotRequired[str]
    schema_version: NotRequired[int]
    msisdn: NotRequired[list[str]]
    imeisv: NotRequired[list[str] | str]  # observed as a bare string on ≥1 real document
    mme_host: NotRequired[list[str]]
    mme_realm: NotRequired[list[str]]
    purge_flag: NotRequired[list[bool]]
    security: NotRequired[dict]
    ambr: NotRequired[dict]
    slice: NotRequired[list[dict]]
    access_restriction_data: NotRequired[int]
    subscriber_status: NotRequired[int]
    operator_determined_barring: NotRequired[int]
    network_access_mode: NotRequired[int]
    subscribed_rau_tau_timer: NotRequired[int]


def normalize_imsi(raw: str) -> str | None:
	"""Normalize IMSI: accept raw digits or 'imsi-<digits>' format."""
	s = raw.strip().lower().removeprefix("imsi-")
	return s if _IMSI_RE.match(s) else None


def normalize_supi(raw: str) -> tuple[str, str]:
	"""Return (full_supi, bare_imsi_digits).

	Accepts: '999700000000001', 'imsi-999700000000001', 'IMSI:999700000000001'
	Raises ValueError on invalid format.
	"""
	s = raw.strip()
	if re.match(r"(?i)imsi[-:]", s):
		digits = re.sub(r"(?i)^imsi[-:]", "", s).strip()
	else:
		digits = s
	if not _IMSI_RE.match(digits):
		raise ValueError(f"Invalid SUPI/IMSI '{raw}': expected 10-15 digits after prefix")
	return f"imsi-{digits}", digits


def get_subscribers_col():
	"""Get MongoDB subscribers collection; raises on connection failure."""
	return MongoClient(_MONGO_URI, serverSelectionTimeoutMS=3000)[_DB][_COL]


def serialize(doc: Any) -> Any:
	"""Recursively convert BSON types to JSON-safe Python primitives."""
	if isinstance(doc, dict):
		return {k: serialize(v) for k, v in doc.items()}
	if isinstance(doc, list):
		return [serialize(i) for i in doc]
	if isinstance(doc, bson.ObjectId):
		return str(doc)
	if isinstance(doc, bson.Int64):
		return int(doc)
	return doc


def redact(doc: Any) -> Any:
	"""Redact security.k and security.opc before returning to callers."""
	if not isinstance(doc, dict):
		return doc
	result = dict(doc)
	if "security" in result and isinstance(result["security"], dict):
		sec = dict(result["security"])
		if "k" in sec:
			sec["k"] = "***"
		if "opc" in sec:
			sec["opc"] = "***"
		result["security"] = sec
	return result


def deep_merge(base: dict, override: dict) -> dict:
	"""Merge override into a deep copy of base; override wins on scalar conflicts."""
	result = copy.deepcopy(base)
	for k, v in override.items():
		if k in result and isinstance(result[k], dict) and isinstance(v, dict):
			result[k] = deep_merge(result[k], v)
		else:
			result[k] = copy.deepcopy(v)
	return result


# Default subscriber document (matches Mongoose schema defaults)
DEFAULT_SUBSCRIBER: dict[str, Any] = {
	"schema_version": 1,
	"msisdn": [],
	"imeisv": [],
	"mme_host": [],
	"mme_realm": [],
	"purge_flag": [],
	"security": {
		"k":   "",
		"op":  None,
		"opc": "",
		"amf": "8000",
		"rand": None,
		"sqn":  0,
	},
	"ambr": {
		"downlink": {"value": 1, "unit": 3},  # 1 Gbps
		"uplink":   {"value": 1, "unit": 3},
	},
	"slice": [
		{
			"sst": 1,
			"default_indicator": True,
			"session": [
				{
					"name": "internet",
					"type": 3,  # IPv4v6
					"qos": {
						"index": 9,  # 5QI-9
						"arp": {
							"priority_level": 8,
							"pre_emption_capability": 1,
							"pre_emption_vulnerability": 1,
						},
					},
					"ambr": {
						"downlink": {"value": 1, "unit": 3},
						"uplink":   {"value": 1, "unit": 3},
					},
					"pcc_rule": [],
				}
			],
		}
	],
	"access_restriction_data":   32,
	"subscriber_status":          0,
	"operator_determined_barring": 0,
	"network_access_mode":         0,
	"subscribed_rau_tau_timer":   12,
}
