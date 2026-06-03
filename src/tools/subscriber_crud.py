"""subscriber_crud — full CRUD against the Open5GS subscribers collection."""

import copy
from typing import Any, Literal

import bson
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, DuplicateKeyError, ServerSelectionTimeoutError
import re

_MONGO_URI = "mongodb://localhost:27017"
_DB = "open5gs"
_COL = "subscribers"
_IMSI_RE = re.compile(r"^\d{10,15}$")

# ── default subscriber document ────────────────────────────────────────────────
# Matches the Mongoose schema defaults. Callers only need to supply
# security.k and security.opc (and optionally slice/ambr overrides).

_DEFAULT: dict[str, Any] = {
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
                    "type": 3,           # IPv4v6
                    "qos": {
                        "index": 9,      # 5QI-9
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

# ── helpers ────────────────────────────────────────────────────────────────────

def _normalize_imsi(raw: str) -> str | None:
    s = raw.strip().lower().removeprefix("imsi-")
    return s if _IMSI_RE.match(s) else None


def _col():
    return MongoClient(_MONGO_URI, serverSelectionTimeoutMS=3000)[_DB][_COL]


def _serialize(doc: Any) -> Any:
    """Recursively convert BSON types to JSON-safe Python primitives."""
    if isinstance(doc, dict):
        return {k: _serialize(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_serialize(i) for i in doc]
    if isinstance(doc, bson.ObjectId):
        return str(doc)
    if isinstance(doc, bson.Int64):
        return int(doc)
    return doc


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into a deep copy of base; override wins on scalar conflicts."""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


# ── main ───────────────────────────────────────────────────────────────────────

def subscriber_crud(
    operation: Literal["create", "read", "update", "delete", "list"],
    imsi: str | None = None,
    data: dict | None = None,
    limit: int = 100,
) -> dict:
    """
    Full CRUD against the Open5GS subscribers MongoDB collection.

    Args:
        operation: create | read | update | delete | list
        imsi:  IMSI digits (10-15) or SUPI ("imsi-<digits>").
               Required for create / read / update / delete.
        data:  Subscriber fields dict for create or update.
               For create, merged with defaults — minimum useful payload:
                 {"security": {"k": "<Ki>", "opc": "<OPc>"}}
               Accepts any subset of the subscriber schema (security, ambr,
               slice, msisdn, access_restriction_data, …).
               For update, only supplied keys are changed (deep-merge).
        limit: Max documents returned by list (1–1000, default 100).

    AMBR unit codes: 0 = bps, 1 = Kbps, 2 = Mbps, 3 = Gbps
    Session type:    1 = IPv4, 2 = IPv6, 3 = IPv4v6

    Returns:
        create / read / update → {"ok": True, "operation": str, "subscriber": {doc}}
        delete                 → {"ok": True, "operation": "delete", "deleted": bool, "imsi": str}
        list                   → {"ok": True, "operation": "list",   "subscribers": [...], "count": int}
        error                  → {"ok": False, "error": str}
    """
    valid_ops = {"create", "read", "update", "delete", "list"}
    if operation not in valid_ops:
        return {"ok": False, "error": f"Invalid operation '{operation}'. Must be one of: {sorted(valid_ops)}"}

    if not (1 <= limit <= 1000):
        return {"ok": False, "error": "limit must be between 1 and 1000"}

    # Normalise & validate IMSI for single-document operations
    norm: str | None = None
    if operation in {"create", "read", "update", "delete"}:
        if not imsi:
            return {"ok": False, "error": f"'imsi' is required for '{operation}'"}
        norm = _normalize_imsi(imsi)
        if norm is None:
            return {"ok": False, "error": f"Invalid IMSI '{imsi}'. Expected 10-15 digits or 'imsi-<digits>'."}

    try:
        col = _col()

        if operation == "list":
            docs = list(col.find({}, limit=limit).sort("imsi", ASCENDING))
            return {
                "ok": True, "operation": "list",
                "subscribers": [_serialize(d) for d in docs],
                "count": len(docs),
            }

        if operation == "read":
            doc = col.find_one({"imsi": norm})
            if doc is None:
                return {"ok": False, "error": f"Subscriber {norm} not found"}
            return {"ok": True, "operation": "read", "subscriber": _serialize(doc)}

        if operation == "delete":
            result = col.delete_one({"imsi": norm})
            return {
                "ok": True, "operation": "delete",
                "deleted": result.deleted_count == 1,
                "imsi": norm,
            }

        if operation == "create":
            doc = _deep_merge(_DEFAULT, data or {})
            doc["imsi"] = norm
            col.insert_one(doc)
            return {"ok": True, "operation": "create", "subscriber": _serialize(doc)}

        if operation == "update":
            existing = col.find_one({"imsi": norm})
            if existing is None:
                return {"ok": False, "error": f"Subscriber {norm} not found"}
            merged = _deep_merge(_serialize(existing), data or {})
            merged.pop("_id", None)
            col.replace_one({"imsi": norm}, merged)
            updated = col.find_one({"imsi": norm})
            return {"ok": True, "operation": "update", "subscriber": _serialize(updated)}

    except DuplicateKeyError:
        return {"ok": False, "error": f"Subscriber {norm} already exists"}
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        return {"ok": False, "error": f"MongoDB connection failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": False, "error": "unreachable"}
