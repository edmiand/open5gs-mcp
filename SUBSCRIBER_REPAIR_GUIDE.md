# Subscriber Repair Guide

## Problem: Corrupted Subscriber After Update

If you see an error in the Open5GS WebUI after using `subscriber_update_slices`, the subscriber document likely has missing or malformed fields.

---

## Root Causes

### 1. **Incomplete Slice Definition**
When you call `subscriber_update_slices`, you **replace the entire slice array**. If you pass minimal data, you lose optional but expected fields:

```python
# ❌ WRONG — loses default_indicator, type, qos, etc.
subscriber_update_slices(
    imsi="999700000000001",
    slices=[{"sst": 1, "session": [{"name": "iotnet"}]}]
)

# ✅ CORRECT — includes all expected fields
subscriber_update_slices(
    imsi="999700000000001",
    slices=[{
        "sst": 1,
        "default_indicator": True,
        "session": [{
            "name": "iotnet",
            "type": 3,  # IPv4v6
            "qos": {"index": 9, "arp": {...}},
            "ambr": {"downlink": {...}, "uplink": {...}},
            "pcc_rule": []
        }]
    }]
)
```

### 2. **WebUI Schema Expectations**
The Open5GS WebUI may enforce stricter validation than the database schema, expecting fields like:
- `default_indicator` — at least one slice must be marked as default
- `type` — session must specify IPv4/IPv6/both
- `qos` — session must have QoS config
- `ambr` — session should have bitrate limits
- `pcc_rule` — even if empty array

---

## Diagnosis: What Went Wrong?

Use the new `subscriber_repair` tool to diagnose:

```python
result = subscriber_repair(imsi="999700000000001", action="diagnose")
```

Returns:
```python
{
    "ok": True,
    "action": "diagnose",
    "diagnosis": {
        "current_fields": ["imsi", "security", "slice", ...],
        "missing_top_level_fields": ["ambr", "schema_version"],
        "problematic_slices": [
            "slice[0].session[0] missing 'type' (IPv4/v6 indicator)",
            "slice[0].session[0] missing 'qos' config"
        ]
    },
    "subscriber": {...}  # Full doc so you can inspect it
}
```

**Check:**
- Are slice fields present and correct?
- Does each session have `name`, `type`, `qos`?
- Is there at least one slice with `default_indicator: true`?

---

## Recovery Options

### Option 1: Restore to Safe Defaults (Recommended)
Resets to defaults but **preserves security credentials**:

```python
result = subscriber_repair(imsi="999700000000001", action="restore_defaults")
```

This:
- ✅ Keeps IMSI and security (K, OPc, SQN)
- ✅ Restores sane slice/session defaults
- ❌ Loses any custom profile settings (AMBR, slices, DNNs, status flags)

After recovery, you can:
1. Verify it opens in WebUI
2. Re-apply any custom settings with `subscriber_update_profile` and `subscriber_update_slices`

### Option 2: Full Reset (Nuclear)
Complete wipe to defaults:

```python
result = subscriber_repair(imsi="999700000000001", action="restore_full")
```

This:
- ✅ Guarantees a clean, valid document
- ❌ **Loses ALL data including security credentials**
- ⚠️ You must re-provision K/OPc/SQN with `subscriber_update_profile`

### Option 3: Manual Fix (Advanced)
If you know exactly what went wrong, you can manually fix it:

1. **Get the diagnosis:**
   ```python
   diag = subscriber_repair(imsi="999700000000001", action="diagnose")
   slices = diag["subscriber"]["slice"]
   ```

2. **Fix the slices locally** (add missing `type`, `qos`, `default_indicator`, etc.)

3. **Re-apply:**
   ```python
   subscriber_update_slices(imsi="999700000000001", slices=fixed_slices)
   ```

---

## Prevention: Update Slices Safely

Always include the full slice structure when updating. Use `subscriber_read` first to see the current structure:

```python
# 1. Read current subscriber
current = subscriber_read(imsi="999700000000001")
slices = current["subscriber"]["slice"]

# 2. Modify slices locally (add a DNN, change AMBR, etc.)
slices[0]["session"].append({
    "name": "newdnn",
    "type": 3,
    "qos": {"index": 9, ...},
    "ambr": {...},
    "pcc_rule": []
})

# 3. Apply the full updated structure
subscriber_update_slices(imsi="999700000000001", slices=slices)
```

---

## Example Workflow: Add a DNN Safely

```python
# 1. Read current state
subscriber = subscriber_read(imsi="999700000000001")
slices = subscriber["subscriber"]["slice"]

# 2. Modify in place (add a new DNN to the default slice)
slices[0]["session"].append({
    "name": "iotnet",
    "type": 1,  # IPv4 only
    "qos": {
        "index": 80,  # 5QI for IoT
        "arp": {
            "priority_level": 15,
            "pre_emption_capability": 0,
            "pre_emption_vulnerability": 0
        }
    },
    "ambr": {
        "downlink": {"value": 100, "unit": 2},  # 100 Mbps down
        "uplink": {"value": 50, "unit": 2}      # 50 Mbps up
    },
    "pcc_rule": []
})

# 3. Apply the full structure
result = subscriber_update_slices(imsi="999700000000001", slices=slices)

# 4. Verify in WebUI
# Open http://localhost:9999 and check the subscriber
```

---

## Why This Happened: Design Tradeoff

The split into `subscriber_update_profile` (scalar fields) and `subscriber_update_slices` (array fields) creates a **replace, not merge** semantic for slices:

- **Pro**: Clear, predictable — you always see exactly what you're writing
- **Con**: Requires knowing the full slice structure; easy to accidentally drop fields

The old `subscriber_crud` with generic `data` merge had the same issue but hid it with the `operation` enum.

**Future improvement**: Add `subscriber_add_session` and `subscriber_modify_session` for fine-grained DNN edits without full-slice replacement.

---

## Troubleshooting

### WebUI still broken after `restore_defaults`?
- Try refreshing the browser (Ctrl+F5)
- Check browser console for JavaScript errors
- Verify MongoDB is running: `mongosh open5gs --eval "db.subscribers.countDocuments()"`
- Check WebUI logs: `docker logs open5gs-webui` (if containerized) or check syslog

### "Subscriber not found" after repair?
- The IMSI format might be wrong. Use: `subscriber_list()` to see valid IMSIs
- Verify the subscriber still exists: `mongosh open5gs --eval "db.subscribers.findOne({imsi: '999700000000001'})"`

### Need to restore from backup?
If you have a MongoDB backup:
```bash
# Restore the entire subscribers collection
mongorestore --archive=backup.archive --nsInclude="open5gs.subscribers"

# Or restore a single IMSI from a JSON export
mongosh open5gs --eval "db.subscribers.insertOne($(cat subscriber_backup.json))"
```

---

## Default Slice Template (reference)

When `restore_defaults` is used, the slice structure is:

```json
{
  "sst": 1,
  "default_indicator": true,
  "session": [
    {
      "name": "internet",
      "type": 3,
      "qos": {
        "index": 9,
        "arp": {
          "priority_level": 8,
          "pre_emption_capability": 1,
          "pre_emption_vulnerability": 1
        }
      },
      "ambr": {
        "downlink": {"value": 1, "unit": 3},
        "uplink": {"value": 1, "unit": 3}
      },
      "pcc_rule": []
    }
  ]
}
```

Use this as a template when manually building slices.
