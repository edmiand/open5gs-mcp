# Subscriber Tools Migration: subscriber_crud → 5 Focused Tools

## Overview

The `subscriber_crud` tool has been split into 5 focused tools with explicit semantics, better documentation, and clearer parameter intent.

**Why split?**
- **Clarity**: Each tool has one job with dedicated parameters (no `operation` enum)
- **Documentation**: Profile-updatable fields are explicitly listed, not hidden in schema docs
- **Discoverability**: Agents see focused tools instead of one monolithic CRUD
- **Maintainability**: Future additions (e.g., `subscriber_auth_reset`) slot in naturally

---

## Old → New Mapping

### Old Tool: `subscriber_crud(operation="read", imsi="...")`
```python
# BEFORE
subscriber_crud(operation="read", imsi="999700000000001")

# AFTER
subscriber_read(imsi="999700000000001")
```

### Old Tool: `subscriber_crud(operation="list", limit=100, filter={...})`
```python
# BEFORE
subscriber_crud(operation="list", limit=50, filter={"subscriber_status": 0})

# AFTER
subscriber_list(limit=50, filter={"subscriber_status": 0})
```

### Old Tool: `subscriber_crud(operation="create", imsi="...", data={...})`
```python
# BEFORE
subscriber_crud(
    operation="create",
    imsi="999700000000002",
    data={"security": {"k": "<Ki>", "opc": "<OPc>"}}
)

# AFTER
subscriber_create(
    imsi="999700000000002",
    data={"security": {"k": "<Ki>", "opc": "<OPc>"}}
)
```

### Old Tool: `subscriber_crud(operation="delete", imsi="...")`
```python
# BEFORE
subscriber_crud(operation="delete", imsi="999700000000001")

# AFTER
subscriber_delete(imsi="999700000000001")
```

### Old Tool: `subscriber_crud(operation="update", imsi="...", data={...})`

This is now split into **two** tools depending on what you're updating:

#### Update Profile Parameters (security, AMBR, status, restrictions, etc.)
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

#### Update Slice/Session Configuration (DNN names, etc.)
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

---

## New Tools Reference

### 1. `subscriber_read(imsi: str)`
Read a single subscriber record.

**Returns:** `{"ok": True, "subscriber": {...}}` or `{"ok": False, "error": "..."}`

**Example:**
```python
subscriber_read(imsi="999700000000001")
```

---

### 2. `subscriber_list(limit: int = 100, filter: dict | None = None)`
List subscribers with optional filtering.

**Filter keys:** `subscriber_status`, `network_access_mode`, `access_restriction_data`, `operator_determined_barring`

**Returns:** `{"ok": True, "subscribers": [...], "count": int}` or `{"ok": False, "error": "..."}`

**Example:**
```python
# List all subscribers
subscriber_list(limit=100)

# List barred subscribers
subscriber_list(filter={"subscriber_status": 1})

# List packet-only subscribers
subscriber_list(filter={"network_access_mode": 1})
```

---

### 3. `subscriber_create(imsi: str, data: dict | None = None)`
Create a new subscriber record.

**Defaults:**
- AMBR: 1 Gbps down/up
- Slice: SST=1, default session "internet" (IPv4v6, 5QI=9)
- subscriber_status: 0 (service_granted)
- network_access_mode: 0 (packet_and_circuit)

**Returns:** `{"ok": True, "subscriber": {...}}` or `{"ok": False, "error": "..."}`

**Example:**
```python
# Minimal: just auth credentials (uses all defaults)
subscriber_create(
    imsi="999700000000002",
    data={"security": {"k": "<Ki>", "opc": "<OPc>"}}
)

# Full: override defaults
subscriber_create(
    imsi="999700000000003",
    data={
        "security": {"k": "<Ki>", "opc": "<OPc>", "sqn": 0},
        "msisdn": ["+1234567890"],
        "subscriber_status": 1,  # Barred
        "network_access_mode": 1,  # Packet only
        "slice": [
            {
                "sst": 1,
                "session": [
                    {"name": "internet", "type": 3},
                    {"name": "iotnet", "type": 1}
                ]
            }
        ]
    }
)
```

---

### 4. `subscriber_delete(imsi: str)`
Delete a subscriber record.

**Returns:** `{"ok": True, "deleted": bool, "imsi": str}` or `{"ok": False, "error": "..."}`

**Example:**
```python
subscriber_delete(imsi="999700000000001")
```

---

### 5. `subscriber_update_profile(...)`
Update subscriber profile parameters (everything except slices/sessions).

**Updatable fields:**
- `security` (dict): `k`, `opc`, `amf`, `sqn`, `rand`
- `ambr` (dict): `downlink` / `uplink` with `value` and `unit` (0=bps, 1=Kbps, 2=Mbps, 3=Gbps)
- `msisdn` (list): Phone numbers, e.g., `["+1234567890"]`
- `imeisv` (list): Device IDs, e.g., `["12345678901234567"]`
- `mme_host` (list): Legacy MME hostnames
- `mme_realm` (list): Legacy MME realms
- `purge_flag` (list): Subscription purge indicators
- `access_restriction_data` (int): Bitmask (default 32)
- `subscriber_status` (int): 0=service_granted, 1=operator_barring
- `network_access_mode` (int): 0=packet_and_circuit, 1=only_packet, 2=only_circuit
- `operator_determined_barring` (int): Barring state
- `subscribed_rau_tau_timer` (int): RAU/TAU timer in minutes

**Returns:** `{"ok": True, "subscriber": {...}}` or `{"ok": False, "error": "..."}`

**Examples:**
```python
# Update authentication credentials
subscriber_update_profile(
    imsi="999700000000001",
    security={"k": "<new Ki>", "opc": "<new OPc>", "sqn": 150}
)

# Update AMBR
subscriber_update_profile(
    imsi="999700000000001",
    ambr={"downlink": {"value": 10, "unit": 2}, "uplink": {"value": 5, "unit": 2}}  # 10 Mbps down, 5 Mbps up
)

# Bar a subscriber
subscriber_update_profile(
    imsi="999700000000001",
    subscriber_status=1
)

# Update multiple fields
subscriber_update_profile(
    imsi="999700000000001",
    security={"sqn": 200},
    msisdn=["+9876543210"],
    network_access_mode=1  # Packet-only
)
```

---

### 6. `subscriber_update_slices(imsi: str, slices: list)`
Update slice and session (DNN) configuration.

**Note:** The entire slice array is **replaced**, not merged. Pass your full desired slice config.

**Slice object schema:**
```python
{
    "sst": <int>,              # Slice Service Type (required)
    "sd": "<string>",          # Slice Differentiator (optional)
    "default_indicator": <bool>,
    "session": [               # At least one required
        {
            "name": "<DNN>",   # Data Network Name (required)
            "type": <int>,     # 1=IPv4, 2=IPv6, 3=IPv4v6
            "qos": {
                "index": <5QI>,
                "arp": {
                    "priority_level": <int>,
                    "pre_emption_capability": <0-1>,
                    "pre_emption_vulnerability": <0-1>
                }
            },
            "ambr": {...},
            "ue": {"ipv4": "<IP>", "ipv6": "<IP>"},
            "smf": {"ipv4": "<IP>", "ipv6": "<IP>"},
            "pcc_rule": [...],
            "lbo_roaming_allowed": <bool>
        }
    ]
}
```

**Returns:** `{"ok": True, "subscriber": {...}}` or `{"ok": False, "error": "..."}`

**Examples:**
```python
# Single slice, single DNN
subscriber_update_slices(
    imsi="999700000000001",
    slices=[
        {
            "sst": 1,
            "default_indicator": True,
            "session": [
                {"name": "internet", "type": 3}
            ]
        }
    ]
)

# Single slice, multiple DNNs
subscriber_update_slices(
    imsi="999700000000001",
    slices=[
        {
            "sst": 1,
            "default_indicator": True,
            "session": [
                {"name": "internet", "type": 3},
                {"name": "iotnet", "type": 1},
                {"name": "enterprise", "type": 2}
            ]
        }
    ]
)

# Multiple slices (with different SSTAIs)
subscriber_update_slices(
    imsi="999700000000001",
    slices=[
        {
            "sst": 1,
            "sd": "000001",
            "default_indicator": True,
            "session": [{"name": "internet", "type": 3}]
        },
        {
            "sst": 128,
            "sd": "000002",
            "session": [{"name": "urllc", "type": 3}]
        }
    ]
)
```

---

## Implementation Details

### Shared Utilities (`_subscriber_util.py`)
- `normalize_imsi()` — validate and normalize IMSI format
- `get_subscribers_col()` — MongoDB connection pooling
- `serialize()` — convert BSON to JSON-safe types
- `redact()` — hide `security.k` and `security.opc`
- `deep_merge()` — recursive dict merge for partial updates
- `DEFAULT_SUBSCRIBER` — sensible schema defaults

### Error Handling
All tools return structured errors:
```python
{"ok": False, "error": "<human-readable error message>"}
```

### Secrets Redaction
All returned subscriber documents have `security.k` and `security.opc` replaced with `"***"` for safety in logs/debug output.

---

## Backward Compatibility

**The old `subscriber_crud` tool is deprecated** but a backup exists at `subscriber_crud.py.bak` if needed for reference or rollback.

Agents should migrate to the new tools immediately, as they provide:
- Clearer intent (no guessing about operation)
- Better documentation (field names in function signature)
- Easier error diagnosis (operation-specific error messages)

---

## Future Additions

The split architecture makes room for:
- `subscriber_auth_reset(imsi, k, opc, sqn)` — dedicated auth credential update
- `subscriber_slice_add(imsi, sst, session)` — add a slice without full replace
- `subscriber_session_add(imsi, sst, session)` — add a DNN to existing slice
- Role-based access control (e.g., read-only agents get only `subscriber_read` + `subscriber_list`)
