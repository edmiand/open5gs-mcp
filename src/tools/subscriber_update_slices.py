"""Update subscriber slice and session configuration in Open5GS MongoDB."""

import copy

from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from ._subscriber_util import (
    normalize_imsi, get_subscribers_col, serialize, redact, deep_merge
)

_VALID_ACTIONS = ("replace", "rename_session", "upsert_session", "remove_session")


def _find_slice(slices: list, sst: int, sd: str | None):
    """Return the first slice matching sst (and sd if provided), or None."""
    for s in slices:
        if s.get("sst") != sst:
            continue
        if sd is not None and s.get("sd") != sd:
            continue
        return s
    return None


def subscriber_update_slices(
    imsi: str,
    action: str,
    # replace
    slices: list | None = None,
    force_replace: bool = False,
    # rename_session / upsert_session / remove_session
    sst: int | None = None,
    sd: str | None = None,
    # rename_session
    old_name: str | None = None,
    new_name: str | None = None,
    # upsert_session
    session: dict | None = None,
    # remove_session
    name: str | None = None,
) -> dict:
    """
    Update subscriber slice and session configuration.

    action: One of "replace", "rename_session", "upsert_session", "remove_session".

    ── replace ───────────────────────────────────────────────────────────────────
    Replace the entire slice array. All existing slices are discarded.

    Guard: if session names are both removed and added in the same slice, the call
    is rejected with a suggestion to use rename_session instead. Pass
    force_replace=True to override when you genuinely intend to swap DNNs.

    slices:        List of slice objects (required). Each slice:
      {
        "sst": <int>,              # Slice Service Type (required)
        "sd": "<string>",          # Slice Differentiator (optional)
        "default_indicator": <bool>,
        "session": [
          {
            "name": "<DNN or APN>",  # required
            "type": <int>,           # 1=IPv4, 2=IPv6, 3=IPv4v6
            "qos": {"index": <5QI>, "arp": {"priority_level", "pre_emption_capability",
                    "pre_emption_vulnerability"}},
            "ambr": {"downlink": {"value": <int>, "unit": <0-3>},
                     "uplink":   {"value": <int>, "unit": <0-3>}},
            "ue":  {"ipv4": "<IP>", "ipv6": "<IP>"},
            "smf": {"ipv4": "<IP>", "ipv6": "<IP>"},
            "pcc_rule": [...],
            "lbo_roaming_allowed": <bool>
          }
        ]
      }

    ── rename_session ────────────────────────────────────────────────────────────
    Rename a session (DNN) within a slice, preserving all other session fields.
    Use this to correct a wrong DNN name without losing QoS/AMBR/PCC config.

    sst:      Slice Service Type identifying the target slice (required).
    sd:       Slice Differentiator — required when multiple slices share the same sst.
    old_name: Current session name to rename (required).
    new_name: New session name (required).

    Example — fix "data" back to "internet":
      subscriber_update_slices(imsi, action="rename_session",
                               sst=1, old_name="data", new_name="internet")

    ── upsert_session ────────────────────────────────────────────────────────────
    Add a new session to a slice, or merge fields into an existing one.
    The session is identified by session["name"]. If a session with that name
    already exists, provided fields are merged in; omitted fields are preserved.
    If no session with that name exists, the session is appended.

    sst:     Slice Service Type identifying the target slice (required).
    sd:      Slice Differentiator (optional).
    session: Session dict with at least {"name": "<DNN>"} (required).

    ── remove_session ───────────────────────────────────────────────────────────
    Remove a session (DNN) from a slice by name. The slice must retain at least
    one session after removal.

    sst:  Slice Service Type identifying the target slice (required).
    sd:   Slice Differentiator (optional).
    name: Session name (DNN) to remove (required).

    ── Returns ──────────────────────────────────────────────────────────────────
    success: {"ok": True,  "subscriber": {...}}  (secrets redacted)
    error:   {"ok": False, "error": str}
    """
    norm = normalize_imsi(imsi)
    if norm is None:
        _e = f"Invalid IMSI '{imsi}'. Expected 10-15 digits or 'imsi-<digits>'."
        return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

    if action not in _VALID_ACTIONS:
        _e = f"Unknown action '{action}'. Valid: {', '.join(_VALID_ACTIONS)}"
        return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

    if action == "replace":
        return _replace(norm, slices, force_replace)
    if action == "rename_session":
        return _rename_session(norm, sst, sd, old_name, new_name)
    if action == "upsert_session":
        return _upsert_session(norm, sst, sd, session)
    # remove_session
    return _remove_session(norm, sst, sd, name)


# ── helpers ───────────────────────────────────────────────────────────────────

def _err(msg: str) -> dict:
    return {"summary": f"Error: {msg}", "detail": {"ok": False, "error": msg}}


def _ok(subscriber: dict, summary: str) -> dict:
    return {"summary": summary, "detail": {"ok": True, "subscriber": redact(serialize(subscriber))}}


def _load(norm: str):
    """Fetch the subscriber document. Returns (doc, None) or (None, err_dict)."""
    try:
        col = get_subscribers_col()
        doc = col.find_one({"imsi": norm})
        if doc is None:
            return None, None, _err(f"Subscriber {norm} not found")
        return col, doc, None
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        return None, None, _err(f"MongoDB connection failed: {exc}")
    except Exception as exc:
        return None, None, _err(str(exc))


def _save(col, norm: str, doc: dict) -> dict | None:
    """Write the document back. Returns an err dict on failure, None on success."""
    try:
        merged = serialize(doc)
        merged.pop("_id", None)
        col.replace_one({"imsi": norm}, merged)
        return None, merged
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        return _err(f"MongoDB connection failed: {exc}"), None
    except Exception as exc:
        return _err(str(exc)), None


# ── actions ───────────────────────────────────────────────────────────────────

def _replace(norm: str, slices, force_replace: bool) -> dict:
    if not isinstance(slices, list):
        return _err("slices must be a list")
    if not slices:
        return _err("slices list cannot be empty; at least one slice with one session is required")

    for i, slice_obj in enumerate(slices):
        if not isinstance(slice_obj, dict):
            return _err(f"slice[{i}] must be a dict")
        if "sst" not in slice_obj:
            return _err(f"slice[{i}] missing required field 'sst'")
        if "session" not in slice_obj or not isinstance(slice_obj["session"], list):
            return _err(f"slice[{i}] missing or invalid 'session' array")
        if not slice_obj["session"]:
            return _err(f"slice[{i}].session cannot be empty; at least one session is required")
        for j, sess in enumerate(slice_obj["session"]):
            if not isinstance(sess, dict):
                return _err(f"slice[{i}].session[{j}] must be a dict")
            if "name" not in sess:
                return _err(f"slice[{i}].session[{j}] missing required field 'name' (DNN)")

    col, doc, err = _load(norm)
    if err:
        return err

    doc = serialize(doc)

    if not force_replace:
        hints = _detect_session_additions(doc.get("slice", []), slices)
        if hints:
            lines = "; ".join(hints)
            return _err(
                f"replace blocked — new session names detected in existing slice(s): {lines}. "
                f"Use rename_session to rename a DNN, or upsert_session to add a new one. "
                f"To force a full replacement anyway, pass force_replace=True."
            )

    doc["slice"] = slices
    doc.pop("_id", None)
    try:
        col.replace_one({"imsi": norm}, doc)
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        return _err(f"MongoDB connection failed: {exc}")
    except Exception as exc:
        return _err(str(exc))

    return _ok(doc, f"Slice configuration updated for subscriber {norm}.")


def _detect_session_additions(existing_slices: list, new_slices: list) -> list[str]:
    """Return hint strings for each existing slice where new session names appear."""
    hints = []
    for new_slice in new_slices:
        sst = new_slice.get("sst")
        sd = new_slice.get("sd")
        existing = _find_slice(existing_slices, sst, sd)
        if existing is None:
            continue  # brand-new slice — no history to compare against
        old_names = {s["name"] for s in existing.get("session", [])}
        new_names = {s["name"] for s in new_slice.get("session", [])}
        added = new_names - old_names
        if not added:
            continue
        removed = old_names - new_names
        sd_hint = f" sd={sd!r}" if sd is not None else ""
        if removed:
            hints.append(
                f"slice sst={sst}{sd_hint}: '{', '.join(sorted(removed))}' removed "
                f"and '{', '.join(sorted(added))}' added — use rename_session"
            )
        else:
            hints.append(
                f"slice sst={sst}{sd_hint}: new session(s) '{', '.join(sorted(added))}' "
                f"added — use upsert_session to add a DNN"
            )
    return hints


def _rename_session(norm: str, sst, sd, old_name, new_name) -> dict:
    if sst is None:
        return _err("rename_session requires 'sst'")
    if not old_name:
        return _err("rename_session requires 'old_name'")
    if not new_name:
        return _err("rename_session requires 'new_name'")

    col, doc, err = _load(norm)
    if err:
        return err

    doc = serialize(doc)
    target_slice = _find_slice(doc.get("slice", []), sst, sd)
    if target_slice is None:
        sd_hint = f" sd={sd!r}" if sd is not None else ""
        return _err(f"No slice with sst={sst}{sd_hint} found for subscriber {norm}")

    sessions = target_slice.get("session", [])
    target_sess = next((s for s in sessions if s.get("name") == old_name), None)
    if target_sess is None:
        return _err(
            f"No session named '{old_name}' in slice sst={sst}"
            + (f" sd={sd!r}" if sd is not None else "")
        )

    # Check new_name doesn't already exist (would create a duplicate)
    if any(s.get("name") == new_name for s in sessions):
        return _err(
            f"A session named '{new_name}' already exists in slice sst={sst}; "
            f"remove it first or use upsert_session"
        )

    target_sess["name"] = new_name

    err, saved = _save(col, norm, doc)
    if err:
        return err
    return _ok(saved, f"Session renamed '{old_name}' → '{new_name}' in slice sst={sst} for subscriber {norm}.")


def _upsert_session(norm: str, sst, sd, session) -> dict:
    if sst is None:
        return _err("upsert_session requires 'sst'")
    if not isinstance(session, dict):
        return _err("upsert_session requires 'session' to be a dict")
    sess_name = session.get("name")
    if not sess_name:
        return _err("upsert_session: session dict must include 'name' (DNN)")

    col, doc, err = _load(norm)
    if err:
        return err

    doc = serialize(doc)
    target_slice = _find_slice(doc.get("slice", []), sst, sd)
    if target_slice is None:
        sd_hint = f" sd={sd!r}" if sd is not None else ""
        return _err(f"No slice with sst={sst}{sd_hint} found for subscriber {norm}")

    sessions = target_slice.setdefault("session", [])
    existing = next((s for s in sessions if s.get("name") == sess_name), None)
    if existing is not None:
        merged = deep_merge(existing, session)
        idx = sessions.index(existing)
        sessions[idx] = merged
        verb = f"updated session '{sess_name}'"
    else:
        sessions.append(copy.deepcopy(session))
        verb = f"added session '{sess_name}'"

    err, saved = _save(col, norm, doc)
    if err:
        return err
    return _ok(saved, f"Slice sst={sst}: {verb} for subscriber {norm}.")


def _remove_session(norm: str, sst, sd, name) -> dict:
    if sst is None:
        return _err("remove_session requires 'sst'")
    if not name:
        return _err("remove_session requires 'name'")

    col, doc, err = _load(norm)
    if err:
        return err

    doc = serialize(doc)
    target_slice = _find_slice(doc.get("slice", []), sst, sd)
    if target_slice is None:
        sd_hint = f" sd={sd!r}" if sd is not None else ""
        return _err(f"No slice with sst={sst}{sd_hint} found for subscriber {norm}")

    sessions = target_slice.get("session", [])
    remaining = [s for s in sessions if s.get("name") != name]
    if len(remaining) == len(sessions):
        return _err(f"No session named '{name}' in slice sst={sst}")
    if not remaining:
        return _err(
            f"Cannot remove the last session '{name}' from slice sst={sst}; "
            f"a slice must have at least one session"
        )

    target_slice["session"] = remaining

    err, saved = _save(col, norm, doc)
    if err:
        return err
    return _ok(saved, f"Removed session '{name}' from slice sst={sst} for subscriber {norm}.")
