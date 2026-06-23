"""Update subscriber slice and session configuration in Open5GS MongoDB."""

import copy

from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from ._subscriber_util import (
    normalize_imsi, get_subscribers_col, serialize, redact, deep_merge
)


def _slice_key(s: dict) -> tuple:
    """Composite key for matching slices: (sst, sd). sd defaults to None."""
    return (s.get("sst"), s.get("sd"))


def _merge_sessions(existing: list, incoming: list) -> list:
    """Merge incoming sessions onto existing ones, matched by DNN name."""
    by_name = {s["name"]: copy.deepcopy(s) for s in existing}
    for sess in incoming:
        name = sess["name"]
        if name in by_name:
            by_name[name] = deep_merge(by_name[name], sess)
        else:
            by_name[name] = copy.deepcopy(sess)
    # Existing order first, then any brand-new sessions
    seen: set = set()
    result = []
    for s in existing:
        result.append(by_name[s["name"]])
        seen.add(s["name"])
    for s in incoming:
        if s["name"] not in seen:
            result.append(copy.deepcopy(s))
            seen.add(s["name"])
    return result


def _merge_slices(existing: list, incoming: list) -> list:
    """Merge incoming slices onto existing ones, matched by (sst, sd)."""
    by_key = {_slice_key(s): copy.deepcopy(s) for s in existing}
    for inc in incoming:
        key = _slice_key(inc)
        if key in by_key:
            ex_slice = by_key[key]
            ex_sessions = ex_slice.pop("session", [])
            inc_sessions = inc.get("session", [])
            # Merge non-session fields, then handle sessions separately
            merged = deep_merge(ex_slice, {k: v for k, v in inc.items() if k != "session"})
            merged["session"] = _merge_sessions(ex_sessions, inc_sessions) if inc_sessions else ex_sessions
            by_key[key] = merged
        else:
            by_key[key] = copy.deepcopy(inc)
    # Existing order first, then brand-new slices
    seen: set = set()
    result = []
    for s in existing:
        k = _slice_key(s)
        result.append(by_key[k])
        seen.add(k)
    for s in incoming:
        k = _slice_key(s)
        if k not in seen:
            result.append(by_key[k])
            seen.add(k)
    return result


def subscriber_update_slices(imsi: str, slices: list, replace: bool = False) -> dict:
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

        replace: If False (default), incoming slices are deep-merged onto the
          existing configuration. Slices are matched by (sst, sd); sessions
          within a slice are matched by name. Only explicitly provided fields
          are changed; all other existing fields are preserved. New slices or
          sessions not present in the existing config are appended.
          If True, the entire slice array is replaced with the provided value
          (original behaviour — all existing slices are discarded).

    Returns:
        {"ok": True, "subscriber": {...}} on success (secrets redacted).
        {"ok": False, "error": str} on failure.

    Examples:
        Rename a DNN while preserving QoS/AMBR (merge mode, default):
          subscriber_update_slices(
              imsi,
              slices=[{"sst": 1, "session": [{"name": "newname", "type": 3}]}]
          )
          — only changes the session name; all other fields are kept.

        Add a second DNN to an existing slice:
          subscriber_update_slices(
              imsi,
              slices=[{"sst": 1, "session": [{"name": "iotnet", "type": 3}]}]
          )
          — appends iotnet; existing internet session is untouched.

        Full replacement (replace=True):
          subscriber_update_slices(imsi, slices=[...], replace=True)
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

        merged = serialize(existing)
        merged.pop("_id", None)
        if replace:
            merged["slice"] = slices
        else:
            merged["slice"] = _merge_slices(merged.get("slice", []), slices)
        col.replace_one({"imsi": norm}, merged)

        mode = "replaced" if replace else "merged"
        return {"summary": f"Slice configuration {mode} for subscriber {norm}.",
                "detail": {"ok": True, "subscriber": redact(serialize(merged))}}

    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        _e = f"MongoDB connection failed: {exc}"
        return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}
    except Exception as exc:
        return {"summary": f"Error: {exc}", "detail": {"ok": False, "error": str(exc)}}
