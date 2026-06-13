# MCP Tools Reference

Complete documentation of all Open5GS MCP server tools.

---

## Table of Contents

1. [Network Function Lifecycle](#network-function-lifecycle)
2. [System Health & Diagnostics](#system-health--diagnostics)
3. [Subscriber Management](#subscriber-management)
   - [Read Operations](#read-operations)
   - [Create Operations](#create-operations)
   - [Delete Operations](#delete-operations)
   - [Update Operations](#update-operations)
   - [Repair Operations](#repair-operations)
4. [UE Session Management](#ue-session-management)
5. [Configuration & Logs](#configuration--logs)
6. [RAN & Network State](#ran--network-state)
7. [Resource Monitoring](#resource-monitoring)

---

## Network Function Lifecycle

### `nf_lifecycle`

Start, stop, restart, or query the status of Open5GS network functions.

**Parameters:**
- `action` (string, required): One of `start`, `stop`, `restart`, `status`
- `nf` (list of strings, optional): NF names to target. Omit to target all NFs.
  - Valid NF names: `amf`, `smf`, `upf`, `ausf`, `udm`, `udr`, `pcf`, `nssf`, `bsf`, `nrf`, `scp`, `webui`

**Returns:**
```python
{
    "ok": True,
    "action": "start",  # or stop/restart/status
    "nfs": {
        "amf": {"status": "running", "pid": 12345, "uptime": "2h 15m"},
        "smf": {"status": "running", "pid": 12346, "uptime": "2h 15m"},
        ...
    }
}
```

**Examples:**

```python
# Check status of all NFs
nf_lifecycle(action="status")

# Start specific NFs
nf_lifecycle(action="start", nf=["amf", "smf", "upf"])

# Restart a single NF
nf_lifecycle(action="restart", nf=["nrf"])

# Stop all NFs
nf_lifecycle(action="stop")
```

**Use cases:**
- Troubleshoot crashed NFs
- Perform maintenance restarts
- Reset network function state
- Verify all NFs are running before tests

---

## System Health & Diagnostics

### `system_health_snapshot`

One-shot health check of the entire Open5GS 5G core.

**Parameters:**
- `log_minutes` (int, optional): How far back to scan logs for errors (default 15, max 1440)

**Returns:**
```python
{
    "ok": True,
    "timestamp": "2026-06-13T10:30:45Z",
    "summary": {
        "overall_health": "healthy",  # or degraded/critical
        "nf_count": {"green": 8, "yellow": 1, "red": 0},
        "mongodb_status": "ok",
        "tun_status": "ok"
    },
    "nfs": {
        "amf": {
            "status": "green",
            "pid": 12345,
            "uptime": "2h 15m",
            "recent_errors": [
                "[2026-06-13 10:25:00.123] ERR|... some error",
                ...
            ]
        },
        ...
    },
    "mongodb": {
        "status": "ok",
        "subscriber_count": 42
    },
    "tun": {
        "status": "ok",
        "device": "ogstun",
        "ip": "10.45.0.1/16"
    }
}
```

**Examples:**

```python
# Quick health check with last 15 minutes of logs
system_health_snapshot()

# Extended check with 1 hour of logs
system_health_snapshot(log_minutes=60)

# Detailed diagnostics with 4 hours of logs
system_health_snapshot(log_minutes=240)
```

**Use cases:**
- First call in any diagnostic session
- Triage system status before deeper investigation
- Monitor for degradation trends
- Verify NF recovery after restart

---

## Subscriber Management

Subscribers are UE profiles stored in MongoDB. The old monolithic `subscriber_crud` tool has been split into focused tools for clarity and safety.

### Read Operations

#### `subscriber_read`

Read a single subscriber record by IMSI.

**Parameters:**
- `imsi` (string, required): IMSI digits (10-15) or SUPI format ("imsi-<digits>")

**Returns:**
```python
{
    "ok": True,
    "subscriber": {
        "imsi": "999700000000001",
        "schema_version": 1,
        "security": {
            "k": "***",  # redacted
            "opc": "***",  # redacted
            "amf": "8000",
            "sqn": 0
        },
        "ambr": {
            "downlink": {"value": 1, "unit": 3},  # 1 Gbps
            "uplink": {"value": 1, "unit": 3}
        },
        "msisdn": ["+1234567890"],
        "imeisv": ["12345678901234567"],
        "slice": [...],
        "access_restriction_data": 32,
        "subscriber_status": 0,
        "network_access_mode": 0,
        "operator_determined_barring": 0,
        "subscribed_rau_tau_timer": 12
    }
}
```

**Examples:**

```python
# Read by raw IMSI digits
subscriber_read(imsi="999700000000001")

# Read by SUPI format
subscriber_read(imsi="imsi-999700000000001")

# Use in a chain
sub = subscriber_read(imsi="999700000000001")
if sub["ok"]:
    print(f"Subscriber status: {sub['subscriber']['subscriber_status']}")
```

**Use cases:**
- Inspect subscriber profile before/after changes
- Verify security credentials are set
- Check subscriber status flags (barred, network mode, etc.)
- Debug registration failures

---

#### `subscriber_list`

List subscribers with optional filtering.

**Parameters:**
- `limit` (int, optional): Max documents to return (1–1000, default 100)
- `filter` (dict, optional): Equality filter on allowed keys only:
  - `subscriber_status` (0=service_granted, 1=operator_barring)
  - `network_access_mode` (0=packet_and_circuit, 1=only_packet, 2=only_circuit)
  - `access_restriction_data` (int, e.g., 32)
  - `operator_determined_barring` (int)

**Returns:**
```python
{
    "ok": True,
    "subscribers": [
        {
            "imsi": "999700000000001",
            "security": {...},
            "subscriber_status": 0,
            ...
        },
        ...
    ],
    "count": 5
}
```

**Examples:**

```python
# List all subscribers (up to 100)
subscriber_list()

# List with limit
subscriber_list(limit=50)

# Find barred subscribers
subscriber_list(filter={"subscriber_status": 1})

# Find packet-only subscribers
subscriber_list(filter={"network_access_mode": 1})

# Find subscribers with specific access restrictions
subscriber_list(filter={"access_restriction_data": 32}, limit=100)
```

**Use cases:**
- Audit subscriber inventory
- Find barred subscribers
- Locate subscribers with specific access modes (4G-only, packet-only)
- Batch operations (read all, then update)

---

### Create Operations

#### `subscriber_create`

Create a new subscriber record.

**Parameters:**
- `imsi` (string, required): IMSI digits (10-15) or SUPI format. Must not already exist.
- `data` (dict, optional): Override defaults. Merged with schema defaults.

**Defaults:**
- AMBR: 1 Gbps downlink/uplink
- Slice: SST=1, default session "internet" (IPv4v6, 5QI=9)
- subscriber_status: 0 (service_granted)
- network_access_mode: 0 (packet_and_circuit)
- access_restriction_data: 32

**Returns:**
```python
{
    "ok": True,
    "subscriber": {
        "imsi": "999700000000002",
        "security": {...},
        "slice": [...],
        ...
    }
}
```

**Examples:**

```python
# Minimal: just auth credentials (uses all defaults)
subscriber_create(
    imsi="999700000000002",
    data={"security": {"k": "<Ki>", "opc": "<OPc>"}}
)

# With MSISDN
subscriber_create(
    imsi="999700000000003",
    data={
        "security": {"k": "<Ki>", "opc": "<OPc>"},
        "msisdn": ["+1234567890"]
    }
)

# Full customization
subscriber_create(
    imsi="999700000000004",
    data={
        "security": {
            "k": "<Ki>",
            "opc": "<OPc>",
            "amf": "8000",
            "sqn": 0
        },
        "msisdn": ["+1234567890"],
        "subscriber_status": 1,  # Barred
        "network_access_mode": 1,  # Packet only
        "ambr": {
            "downlink": {"value": 10, "unit": 2},  # 10 Mbps
            "uplink": {"value": 5, "unit": 2}  # 5 Mbps
        },
        "slice": [
            {
                "sst": 1,
                "default_indicator": True,
                "session": [
                    {
                        "name": "internet",
                        "type": 3,
                        "qos": {"index": 9, "arp": {...}},
                        "ambr": {...},
                        "pcc_rule": []
                    }
                ]
            }
        ]
    }
)
```

**Use cases:**
- Provision test SIMs
- Bulk import subscriber data
- Set up subscribers with specific AMBR limits
- Create multi-slice subscribers

---

### Delete Operations

#### `subscriber_delete`

Delete a subscriber record by IMSI.

**Parameters:**
- `imsi` (string, required): IMSI digits (10-15) or SUPI format

**Returns:**
```python
{
    "ok": True,
    "deleted": True,  # False if subscriber didn't exist
    "imsi": "999700000000001"
}
```

**Examples:**

```python
# Delete a subscriber
result = subscriber_delete(imsi="999700000000001")
if result["deleted"]:
    print("Subscriber deleted")
else:
    print("Subscriber did not exist")

# Bulk delete (with error handling)
imsis_to_delete = ["999700000000001", "999700000000002"]
for imsi in imsis_to_delete:
    result = subscriber_delete(imsi=imsi)
    print(f"{imsi}: {'deleted' if result['deleted'] else 'not found'}")
```

**Use cases:**
- Clean up test subscribers
- Remove duplicate entries
- Decommission subscriber accounts
- Bulk cleanup of expired test data

---

### Update Operations

#### `subscriber_update_profile`

Update subscriber profile parameters (non-slice fields).

**Parameters:**
- `imsi` (string, required): IMSI digits (10-15) or SUPI format
- All other parameters are optional; only supplied ones are updated (deep merge)

**Profile Fields:**

| Field | Type | Values | Example |
|-------|------|--------|---------|
| `security` | dict | `k`, `opc`, `amf`, `sqn`, `rand` | `{"k": "<Ki>", "opc": "<OPc>", "sqn": 100}` |
| `ambr` | dict | nested `downlink`/`uplink` with `value`+`unit` | `{"downlink": {"value": 1, "unit": 3}}` |
| `msisdn` | list | phone numbers | `["+1234567890"]` |
| `imeisv` | list | device IDs | `["12345678901234567"]` |
| `mme_host` | list | MME hostnames (legacy) | `["mme1.example.com"]` |
| `mme_realm` | list | MME realms (legacy) | `["mme.example.com"]` |
| `purge_flag` | list | boolean indicators | `[True]` |
| `access_restriction_data` | int | bitmask (default 32) | `32` |
| `subscriber_status` | int | 0=service_granted, 1=operator_barring | `0` |
| `network_access_mode` | int | 0=packet+circuit, 1=packet-only, 2=circuit-only | `1` |
| `operator_determined_barring` | int | barring state | `0` |
| `subscribed_rau_tau_timer` | int | minutes (legacy 4G) | `12` |

**AMBR Unit Codes:**
- 0 = bps
- 1 = Kbps
- 2 = Mbps
- 3 = Gbps

**Returns:**
```python
{
    "ok": True,
    "subscriber": {
        "imsi": "999700000000001",
        "security": {...},
        "subscriber_status": 1,
        ...
    }
}
```

**Examples:**

```python
# Update authentication credentials
subscriber_update_profile(
    imsi="999700000000001",
    security={
        "k": "<new Ki>",
        "opc": "<new OPc>",
        "sqn": 150
    }
)

# Update AMBR (bitrate limits)
subscriber_update_profile(
    imsi="999700000000001",
    ambr={
        "downlink": {"value": 10, "unit": 2},  # 10 Mbps down
        "uplink": {"value": 5, "unit": 2}      # 5 Mbps up
    }
)

# Bar a subscriber (prevent registration)
subscriber_update_profile(
    imsi="999700000000001",
    subscriber_status=1
)

# Change to packet-only mode (no circuit calls)
subscriber_update_profile(
    imsi="999700000000001",
    network_access_mode=1
)

# Update phone number
subscriber_update_profile(
    imsi="999700000000001",
    msisdn=["+9876543210"]
)

# Multiple fields at once
subscriber_update_profile(
    imsi="999700000000001",
    security={"sqn": 200},
    subscriber_status=0,
    network_access_mode=1
)
```

**Use cases:**
- Update authentication after SIM refresh
- Implement AMBR enforcement
- Bar/unbar subscribers
- Update subscriber contact info
- Manage access restrictions

---

#### `subscriber_update_slices`

Update slice and session (DNN) configuration.

**⚠️ Important:** The entire slice array is **replaced**, not merged. Pass your full desired configuration.

**Parameters:**
- `imsi` (string, required): IMSI digits (10-15) or SUPI format
- `slices` (list, required): Array of slice objects

**Slice Schema:**
```python
{
    "sst": <int>,                           # Slice Service Type (required)
    "sd": "<string>",                       # Slice Differentiator (optional)
    "default_indicator": <bool>,            # Is this the default slice?
    "session": [                            # At least one required
        {
            "name": "<DNN>",                # Data Network Name (required)
            "type": <int>,                  # 1=IPv4, 2=IPv6, 3=IPv4v6
            "qos": {
                "index": <5QI>,             # 5G QoS Indicator
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
                "ipv4": "<IP>",             # UE assigned IPv4
                "ipv6": "<IP>"              # UE assigned IPv6
            },
            "smf": {
                "ipv4": "<IP>",             # SMF assigned IPv4
                "ipv6": "<IP>"              # SMF assigned IPv6
            },
            "pcc_rule": [...],
            "lbo_roaming_allowed": <bool>   # Local breakout roaming
        }
    ]
}
```

**Returns:**
```python
{
    "ok": True,
    "subscriber": {
        "imsi": "999700000000001",
        "slice": [...],  # Updated slices
        ...
    }
}
```

**Examples:**

```python
# Single slice, single DNN (minimal valid config)
subscriber_update_slices(
    imsi="999700000000001",
    slices=[{
        "sst": 1,
        "default_indicator": True,
        "session": [{
            "name": "internet",
            "type": 3,  # IPv4v6
            "qos": {"index": 9},
            "ambr": {
                "downlink": {"value": 1, "unit": 3},
                "uplink": {"value": 1, "unit": 3}
            },
            "pcc_rule": []
        }]
    }]
)

# Single slice, multiple DNNs
subscriber_update_slices(
    imsi="999700000000001",
    slices=[{
        "sst": 1,
        "default_indicator": True,
        "session": [
            {
                "name": "internet",
                "type": 3,
                "qos": {"index": 9, "arp": {
                    "priority_level": 8,
                    "pre_emption_capability": 1,
                    "pre_emption_vulnerability": 1
                }},
                "ambr": {
                    "downlink": {"value": 1, "unit": 3},
                    "uplink": {"value": 1, "unit": 3}
                },
                "pcc_rule": []
            },
            {
                "name": "iotnet",
                "type": 1,  # IPv4 only
                "qos": {"index": 80},  # IoT 5QI
                "ambr": {
                    "downlink": {"value": 100, "unit": 2},  # 100 Mbps
                    "uplink": {"value": 50, "unit": 2}      # 50 Mbps
                },
                "pcc_rule": []
            }
        ]
    }]
)

# Multiple slices with different SSTAIs
subscriber_update_slices(
    imsi="999700000000001",
    slices=[
        {
            "sst": 1,
            "sd": "000001",
            "default_indicator": True,
            "session": [{
                "name": "internet",
                "type": 3,
                "qos": {"index": 9},
                "ambr": {"downlink": {"value": 1, "unit": 3}, "uplink": {"value": 1, "unit": 3}},
                "pcc_rule": []
            }]
        },
        {
            "sst": 128,
            "sd": "000002",
            "default_indicator": False,
            "session": [{
                "name": "urllc",
                "type": 3,
                "qos": {"index": 1},  # URLLC 5QI
                "ambr": {"downlink": {"value": 1, "unit": 2}, "uplink": {"value": 1, "unit": 2}},
                "pcc_rule": []
            }]
        }
    ]
)

# Safe workflow: read first, modify, apply
current = subscriber_read(imsi="999700000000001")
slices = current["subscriber"]["slice"]
# Modify slices locally
slices[0]["session"].append({
    "name": "enterprise",
    "type": 3,
    "qos": {"index": 5},
    "ambr": {"downlink": {"value": 100, "unit": 2}, "uplink": {"value": 50, "unit": 2}},
    "pcc_rule": []
})
# Apply full structure
subscriber_update_slices(imsi="999700000000001", slices=slices)
```

**Use cases:**
- Add/remove DNNs from subscriber
- Update QoS per-DNN
- Implement AMBR per-session
- Configure URLLC or other specialized slices
- Enable local breakout roaming

---

## UE Session Management

### `list_ue_sessions`

List all live UE registrations and their PDU sessions.

**Parameters:**
- `imsi_filter` (string, optional): IMSI prefix to narrow results (digits or "imsi-<digits>")
- `include_idle` (bool, optional): Include idle UEs with no active PDU sessions (default True)

**Returns:**
```python
{
    "ok": True,
    "ue_count": 5,
    "ues": [
        {
            "supi": "imsi-999700000000001",
            "cm_state": "connected",     # or idle/deregistered
            "ue_activity": "reachable",  # or unreachable/idle
            "location": {
                "tai": {"plmn": "99970", "tac": 1},
                "cgi": {"plmn": "99970", "cell_id": 256}
            },
            "gnb_id": "0x000000ff",
            "sessions": [
                {
                    "psi": 1,
                    "dnn": "internet",
                    "s_nssai": {"sst": 1},
                    "ipv4": "10.45.0.100",
                    "ipv6": "fe80::1:100",
                    "state": "active",
                    "qos_flows": [
                        {"qfi": 1, "5qi": 9},
                        {"qfi": 2, "5qi": 8}
                    ],
                    "n3_endpoints": {
                        "gnb": {"teid": "0x00000100", "endpoint": "192.168.1.100:2152"},
                        "upf": {"teid": "0x00000200", "endpoint": "192.168.1.50:2152"}
                    }
                }
            ]
        },
        ...
    ],
    "sources": {
        "amf": "127.0.0.5:7777",
        "smf": "127.0.0.6:7777"
    }
}
```

**Examples:**

```python
# List all UEs with active sessions
list_ue_sessions(include_idle=False)

# Filter by IMSI prefix
list_ue_sessions(imsi_filter="999700")

# Get full registration state
all_ues = list_ue_sessions()
for ue in all_ues["ues"]:
    print(f"{ue['supi']}: {len(ue['sessions'])} sessions, {ue['cm_state']}")
```

**Use cases:**
- Find which UEs are registered
- Check PDU session IP allocations
- Verify QoS flow setup
- Troubleshoot connectivity issues
- Monitor network load

---

## Configuration & Logs

### `read_nf_config`

Read the YAML configuration for any Open5GS network function.

**Parameters:**
- `nf` (string, required): NF name (amf, smf, upf, ausf, udm, udr, pcf, nssf, bsf, nrf, scp)
- `path` (string, optional): Dot-separated path into the config tree

**Returns:**
```python
{
    "ok": True,
    "nf": "amf",
    "config_file": "/path/to/install/etc/open5gs/amf.yaml",
    "path": "amf.sbi",
    "config": {
        "server": [{"address": "127.0.0.5", "port": 7777}],
        "client": {
            "scp": [{"address": "127.0.0.10", "port": 7777}],
            ...
        }
    }
}
```

**Common Paths:**
- `amf.sbi` — SBI server/client addresses
- `amf.sbi.client.scp` — SCP URI
- `smf.pfcp.client.upf` — UPF address
- `smf.session` — UE IP subnet pool
- `amf.guami` — PLMN + AMF ID
- `amf.plmn_support` — supported PLMNs and slices
- `logger` — log file path and level

**Examples:**

```python
# Read full AMF config
read_nf_config(nf="amf")

# Read specific SBI path
read_nf_config(nf="amf", path="amf.sbi")

# Read UE IP pool configuration
read_nf_config(nf="smf", path="smf.session")

# Verify SCP connectivity setup
read_nf_config(nf="amf", path="amf.sbi.client.scp")
```

**Use cases:**
- Verify NF connectivity setup (SBI addresses)
- Inspect subscriber IP pools
- Check logger configuration
- Debug mismatched NF references
- Audit PLMN and slice configuration

---

### `tail_nf_logs`

Filtered log reads across one or more Open5GS NF log files.

**Parameters:**
- `nf` (string or list, optional): NF name(s) or "all" (default "all")
  - Valid: amf, smf, upf, ausf, udm, udr, pcf, nssf, bsf, nrf, scp, webui
- `level` (string, optional): Minimum severity (debug, info, warn, error; default "info")
- `grep` (string, optional): Keyword or Python regex (case-insensitive)
- `lines` (int, optional): Max total lines across all NFs (default 100, max 500)
- `since` (string, optional): Time window start — relative ("15m", "2h") or ISO datetime

**Returns:**
```python
{
    "ok": True,
    "total_matched": 42,
    "lines": [
        {
            "nf": "amf",
            "timestamp": "2026-06-13T10:30:45.123Z",
            "component": "NGAP",
            "level": "info",
            "message": "UE registered successfully"
        },
        ...
    ],
    "per_nf_count": {"amf": 20, "smf": 15, "ausf": 7},
    "per_nf_errors": {}  # e.g., {"upf": "Permission denied"}
}
```

**Examples:**

```python
# Last 100 lines of all NFs
tail_nf_logs()

# Find a specific UE
tail_nf_logs(grep="imsi-999700000000001", lines=200)

# Errors in the last hour
tail_nf_logs(level="error", since="1h")

# Multi-NF correlation during registration
tail_nf_logs(nf=["amf", "ausf", "udm"], grep="Registration", since="15m")

# NSSAI or slice-related events
tail_nf_logs(grep="5QI|NSSAI|slice", lines=300)
```

**Use cases:**
- Correlate errors across NFs
- Troubleshoot registration failures
- Find specific UE events
- Monitor error trends
- Debug configuration issues

---

### `get_ue_trace`

Collect full e2e trace for a UE across all Open5GS NFs.

**Parameters:**
- `supi` (string, required): IMSI/SUPI (formats: "imsi-999700000000001", "999700000000001", "IMSI:999700000000001")
- `time_window_minutes` (int, optional): How far back to search (default 60, max 1440)
- `include_nfs` (list, optional): NFs to include (default all: amf, ausf, udm, udr, smf, pcf, nrf, upf)

**Returns:**
```python
{
    "ok": True,
    "supi": "imsi-999700000000001",
    "time_range": "2026-06-13T09:30:45Z - 2026-06-13T10:30:45Z",
    "summary": {
        "registration_success": True,
        "pdu_session_success": True,
        "ue_ip_assigned": True,
        "errors": []
    },
    "events": [
        {
            "timestamp": "2026-06-13T10:00:10.123Z",
            "nf": "amf",
            "level": "info",
            "direction": "up",
            "message_type": "Registration",
            "from": "gNB",
            "to": "AMF",
            "message": "Initial registration request"
        },
        ...
    ],
    "mermaid_hint": "sequenceDiagram\n  participant gNB\n  participant AMF\n  ...",
    "nf_errors": {}
}
```

**Examples:**

```python
# Full trace for last hour
get_ue_trace(supi="999700000000001")

# Extended trace (4 hours)
get_ue_trace(supi="imsi-999700000000001", time_window_minutes=240)

# Subset of NFs
get_ue_trace(
    supi="999700000000001",
    include_nfs=["amf", "smf", "upf"]
)

# Use the mermaid diagram for visualization
trace = get_ue_trace(supi="999700000000001")
print(trace["mermaid_hint"])  # Copy to Mermaid Live Editor
```

**Use cases:**
- Reconstruct full UE registration flow
- Debug PDU session setup failures
- Verify IP allocation
- Understand e2e message timing
- Generate sequence diagrams for documentation

---

## RAN & Network State

### `amf_ran_query`

Query live RAN state from the AMF OAM API and metrics endpoint.

**Parameters:** None

**Returns:**
```python
{
    "ok": True,
    "timestamp": "2026-06-13T10:30:45Z",
    "connected_gnbs": 3,
    "registered_ues": 42,
    "total_plmns": 1,
    "plmns": [
        {
            "plmn_id": "99970",
            "mcc": "999",
            "mnc": "70",
            "s_nssai": [
                {"sst": 1},
                {"sst": 128, "sd": "000001"}
            ]
        }
    ],
    "gnbs": [
        {
            "gnb_id": "0x000000ff",
            "plmn": "99970",
            "sctp_peer": "192.168.1.100:38412",
            "supported_ta_list": [1, 2, 3],
            "num_connected_ues": 15
        },
        ...
    ],
    "gnbs_status": "ok"  # or unreachable/timeout/error
}
```

**Examples:**

```python
# Query RAN state
ran = amf_ran_query()
print(f"Connected gNBs: {ran['connected_gnbs']}")
print(f"Registered UEs: {ran['registered_ues']}")
for gnb in ran["gnbs"]:
    print(f"  gNB {gnb['gnb_id']}: {gnb['num_connected_ues']} UEs")
```

**Use cases:**
- Check gNB connectivity
- Count active UEs per gNB
- Verify PLMN and slice configuration
- Monitor RAN health
- Diagnose gNB attachment failures

---

## Resource Monitoring

### `nf_resource_usage`

CPU, memory, and I/O utilisation for each running Open5GS NF vs system totals.

**Parameters:**
- `nfs` (list, optional): NF names to sample (default all)
  - Valid: amf, smf, upf, ausf, udm, udr, pcf, nssf, bsf, nrf, scp, webui
- `sample_interval` (float, optional): Sampling window in seconds (0.1–10.0, default 1.0)
  - Larger values give more accurate CPU averages

**Returns:**
```python
{
    "ok": True,
    "timestamp": "2026-06-13T10:30:45Z",
    "sample_interval_s": 1.0,
    "nfs": {
        "amf": {
            "status": "running",
            "pid": 12345,
            "cpu_percent": 5.2,
            "memory": {
                "rss_mb": 128,  # resident set size
                "vms_mb": 256,  # virtual memory size
                "percent": 2.1
            },
            "io": {
                "read_bytes_per_s": 1024000,
                "write_bytes_per_s": 512000,
                "read_total_mb": 150,
                "write_total_mb": 75
            },
            "threads": 12
        },
        ...
    },
    "aggregates": {
        "nfs_running": 8,
        "total_cpu_percent": 18.5,
        "total_rss_mb": 512,
        "total_io_read_bytes_per_s": 4096000,
        "total_io_write_bytes_per_s": 2048000
    },
    "system": {
        "cpu_count_logical": 16,
        "cpu_count_physical": 8,
        "cpu_percent_used": 25.0,
        "memory_total_mb": 32768,
        "memory_available_mb": 16384,
        "memory_used_mb": 16384,
        "memory_percent_used": 50.0,
        "disk_io": {
            "read_bytes_per_s": 16777216,
            "write_bytes_per_s": 8388608
        }
    },
    "open5gs_share": {
        "cpu_pct_of_system_usage": 74.0,
        "memory_pct_of_total": 1.56
    }
}
```

**Examples:**

```python
# Quick resource check with 1s sampling
nf_resource_usage()

# Longer sampling for more accurate averages
nf_resource_usage(sample_interval=5.0)

# Check specific NFs
nf_resource_usage(nfs=["upf", "smf"])

# Find which NF is consuming most resources
usage = nf_resource_usage()
by_cpu = sorted(
    usage["nfs"].items(),
    key=lambda x: x[1]["cpu_percent"],
    reverse=True
)
print("Top CPU consumers:")
for nf, stats in by_cpu[:3]:
    print(f"  {nf}: {stats['cpu_percent']:.1f}%")
```

**Use cases:**
- Identify resource bottlenecks
- Monitor for memory leaks
- Spot I/O performance issues
- Compare NF resource efficiency
- Capacity planning

---

## Tool Selection Guide

### Troubleshooting Registration Failures

1. **System health check** — `system_health_snapshot()`
2. **Find the UE** — `list_ue_sessions(imsi_filter="...")`
3. **Correlate logs** — `tail_nf_logs(nf=["amf","ausf","udm"], grep="<IMSI>")`
4. **Full trace** — `get_ue_trace(supi="<IMSI>")`

### Debugging Data Plane Issues

1. **Check UE session** — `list_ue_sessions(imsi_filter="...")`
2. **Verify IP allocation** — check `ipv4`/`ipv6` fields
3. **Check SMF config** — `read_nf_config(nf="smf", path="smf.session")`
4. **Monitor UPF resources** — `nf_resource_usage(nfs=["upf"])`

### Subscriber Provisioning Workflow

1. **Create subscriber** — `subscriber_create(imsi="...", data={...})`
2. **Verify creation** — `subscriber_read(imsi="...")`
3. **Update profile if needed** — `subscriber_update_profile(imsi="...", ...)`
4. **Add/modify DNNs** — `subscriber_update_slices(imsi="...", slices=[...])`
5. **Verify in WebUI** — http://localhost:9999

---

## Error Handling

All tools return structured responses:

**Success:**
```python
{"ok": True, ...}
```

**Failure:**
```python
{"ok": False, "error": "<human-readable error message>"}
```

Always check `ok` before using tool results:
```python
result = subscriber_read(imsi="999700000000001")
if result["ok"]:
    print(result["subscriber"]["subscriber_status"])
else:
    print(f"Error: {result['error']}")
```

---

## Security Notes

- **Credentials redacted**: All responses redact `security.k` and `security.opc` as `"***"`
- **MongoDB authentication**: Configure per your MongoDB setup (default: localhost:27017)
- **NF accessibility**: UPF operations require sudo for `io_counters` syscall; handled gracefully with fallback
- **Log correlation**: All timestamps are UTC; ensure NF clocks are synchronized

---

## See Also

- [CLAUDE.md](CLAUDE.md) — Project context and architecture
- [README.md](README.md) — Quick start and overview
- [SUBSCRIBER_TOOLS_MIGRATION.md](SUBSCRIBER_TOOLS_MIGRATION.md) — Migration from old `subscriber_crud` tool
