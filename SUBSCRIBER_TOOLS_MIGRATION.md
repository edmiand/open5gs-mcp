# Subscriber Tools Migration: subscriber_crud → subscriber + update tools

## Overview

The old `subscriber_crud` tool has been replaced with three focused tools:

| Old | New | Notes |
|-----|-----|-------|
| `subscriber_crud(operation="read", ...)` | `subscriber(action="read", ...)` | Same semantics |
| `subscriber_crud(operation="list", ...)` | `subscriber(action="list", ...)` | `count` now reflects total DB docs, not page size |
| `subscriber_crud(operation="create", ...)` | `subscriber(action="create", ...)` | Same semantics |
| `subscriber_crud(operation="delete", ...)` | `subscriber(action="delete", ...)` | Same semantics |
| `subscriber_crud(operation="update", data={"slice": ...})` | `subscriber_update_slices(imsi=..., slices=...)` | Slice array is replaced, not merged |
| `subscriber_crud(operation="update", data={non-slice fields})` | `subscriber_update_profile(imsi=..., **fields)` | Explicit params per field |
| `subscriber_repair` | *(removed)* | No longer needed |

---

## Migration Examples

### Read a subscriber

```python
# BEFORE
subscriber_crud(operation="read", imsi="999700000000001")

# AFTER
subscriber(action="read", imsi="999700000000001")
```

### List subscribers

```python
# BEFORE
subscriber_crud(operation="list", limit=50, filter={"subscriber_status": 0})

# AFTER
subscriber(action="list", limit=50, filter={"subscriber_status": 0})
```

**Note:** The `list` response now includes both `count` (total matching documents in DB) and `returned` (number of documents in this page).

### Create a subscriber

```python
# BEFORE
subscriber_crud(
    operation="create",
    imsi="999700000000002",
    data={"security": {"k": "<Ki>", "opc": "<OPc>"}}
)

# AFTER
subscriber(
    action="create",
    imsi="999700000000002",
    data={"security": {"k": "<Ki>", "opc": "<OPc>"}}
)
```

### Delete a subscriber

```python
# BEFORE
subscriber_crud(operation="delete", imsi="999700000000001")

# AFTER
subscriber(action="delete", imsi="999700000000001")
```

### Update profile parameters (security, AMBR, status, etc.)

```python
# BEFORE
subscriber_crud(
    operation="update",
    imsi="999700000000001",
    data={
        "security": {"sqn": 100},
        "subscriber_status": 1
    }
)

# AFTER
subscriber_update_profile(
    imsi="999700000000001",
    security={"sqn": 100},
    subscriber_status=1
)
```

### Update slice/session (DNN) configuration

```python
# BEFORE
subscriber_crud(
    operation="update",
    imsi="999700000000001",
    data={
        "slice": [
            {
                "sst": 1,
                "session": [
                    {"name": "internet", "type": 3},
                    {"name": "iotnet", "type": 3}
                ]
            }
        ]
    }
)

# AFTER
subscriber_update_slices(
    imsi="999700000000001",
    slices=[
        {
            "sst": 1,
            "session": [
                {"name": "internet", "type": 3},
                {"name": "iotnet", "type": 3}
            ]
        }
    ]
)
```

**⚠️ Important:** The entire slice array is **replaced**, not merged. Always pass the full desired slice configuration.

---

## Tool Reference

### `subscriber(action, imsi, data, limit, filter)`

Action-dispatched subscriber lifecycle tool.

- `action` (required): `"read"` | `"list"` | `"create"` | `"delete"`
- `imsi`: IMSI digits (10-15) or SUPI (`"imsi-<digits>"`). Required for read/create/delete.
- `data`: For create only. Deep-merged with schema defaults.
- `limit`: For list only. Max documents (1–1000, default 100).
- `filter`: For list only. Equality filter — allowed keys: `subscriber_status`, `network_access_mode`, `access_restriction_data`, `operator_determined_barring`

Returns: `{"ok": True, ...}` or `{"ok": False, "error": str}`

---

### `subscriber_update_profile(imsi, **fields)`

Update subscriber profile parameters (everything except slices/sessions). Only supplied fields are updated (deep merge for nested dicts).

**Updatable fields:**
- `security` (dict): `k`, `opc`, `amf`, `sqn`, `rand`
- `ambr` (dict): `downlink` / `uplink` with `value` and `unit` (0=bps, 1=Kbps, 2=Mbps, 3=Gbps)
- `msisdn` (list): Phone numbers
- `imeisv` (list): Device IDs
- `mme_host` (list): Legacy MME hostnames
- `mme_realm` (list): Legacy MME realms
- `purge_flag` (list)
- `access_restriction_data` (int)
- `subscriber_status` (int): 0=service_granted, 1=operator_barring
- `network_access_mode` (int): 0=packet+circuit, 1=packet-only, 2=circuit-only
- `operator_determined_barring` (int)
- `subscribed_rau_tau_timer` (int)

Returns: `{"ok": True, "subscriber": {...}}` (secrets redacted)

---

### `subscriber_update_slices(imsi, slices)`

Replace the subscriber's slice array. See [TOOLS.md](TOOLS.md) for the full slice schema.

Returns: `{"ok": True, "subscriber": {...}}` (secrets redacted)

---

## Shared Utilities (`_subscriber_util.py`)

- `normalize_imsi()` — validate and normalize IMSI format
- `get_subscribers_col()` — MongoDB connection
- `serialize()` — convert BSON to JSON-safe types
- `redact()` — hide `security.k` and `security.opc` as `"***"`
- `deep_merge()` — recursive dict merge for partial updates
- `DEFAULT_SUBSCRIBER` — schema defaults for new subscribers

---

## Future Additions

- `subscriber_auth_reset(imsi, k, opc, sqn)` — dedicated auth credential update with SQN sync
- `subscriber_slice_add(imsi, sst, session)` — add a slice without full replace
- Role-based access control (e.g., read-only agents get `subscriber(action="read/list")` only)
