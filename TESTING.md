# Testing Guide

## Overview

The test suite covers all 10 MCP tools with no Open5GS infrastructure required.
All tests mock I/O boundaries (files, subprocesses, HTTP, MongoDB) so they run
in under 1 second on any machine.

## Quick start

```bash
# Run everything (default — excludes live tests)
pytest tests/ -q

# Run only pure unit tests
pytest tests/ -m unit -q

# Run unit + integration (same as default)
pytest tests/ -m "not live" -q

# Run against a live Open5GS stack
pytest tests/ -m live -q
```

## Test tiers

| Marker | Requires | Speed | Purpose |
|---|---|---|---|
| `unit` | nothing | ms | parsing, validation, business logic |
| `integration` | nothing (mocked) | ms | full tool call with faked I/O |
| `live` | running Open5GS | s–min | smoke test against a real stack |

Markers are declared in `pytest.ini`. The default `addopts` excludes `live` so
`pytest tests/` is always safe to run without a running core.

## File layout

```
tests/
  conftest.py                        # shared fixtures (see below)
  test_tail_nf_logs.py               # tail_nf_logs tool
  test_system_health_snapshot.py     # system_health_snapshot tool
  test_subscriber.py                 # subscriber CRUD tool
  test_subscriber_update_profile.py  # subscriber_update_profile tool
  test_subscriber_update_slices.py   # subscriber_update_slices tool
  test_list_ue_sessions.py           # list_ue_sessions tool
  test_amf_ran_query.py              # amf_ran_query tool
  test_nf_lifecycle.py               # nf_lifecycle tool
  test_read_nf_config.py             # read_nf_config tool
  test_nf_resource_usage.py          # nf_resource_usage tool
  test_ue_trace.py                   # get_ue_trace tool
  test_server.py                     # e2e stdio smoke test (requires server)
```

## Shared fixtures (`conftest.py`)

| Fixture / helper | What it provides |
|---|---|
| `make_log_line(nf, level, msg, ts)` | Valid Open5GS log line string |
| `write_log(log_dir, nf, lines)` | Write lines to `<log_dir>/<nf>.log` |
| `fake_subscriber()` / `make_subscriber(imsi)` | Minimal valid subscriber document |
| `make_mock_col(docs)` | Mock pymongo Collection with find/insert/delete/replace wired up |
| `http_response(data, status_code)` | Mock `httpx` response |
| `oam_page(items)` | AMF OAM paginated JSON `{items, pager}` |
| `completed(stdout, returncode, stderr)` | Mock `subprocess.CompletedProcess` |

## Mock boundaries per tool

Each tool test patches only at the I/O boundary, not inside business logic:

| Tool | What is mocked |
|---|---|
| `tail_nf_logs` | `_LOG_DIR` redirected to `tmp_path` (real file I/O on temp files) |
| `system_health_snapshot` | `_get_nf_pid`, `_read_nf_log`, `_check_mongodb`, `_check_tun`, `_check_ran`, `_probe_nf_endpoint` |
| `subscriber` / `subscriber_update_*` | `get_subscribers_col` from `_subscriber_util` |
| `list_ue_sessions` | `httpx.get` (AMF + SMF OAM responses) |
| `amf_ran_query` | `subprocess.run` (curl for SBI), `httpx.get` (for `/gnb-info`) |
| `nf_lifecycle` | `subprocess.run`, `_SCRIPT` patched to a real temp file |
| `read_nf_config` | `_CONFIG_DIR` redirected to `tmp_path` (real YAML files) |
| `nf_resource_usage` | `_get_nf_pid`, `psutil.Process`, `psutil.cpu_percent`, `psutil.virtual_memory`, `psutil.disk_io_counters`, `time.sleep` |
| `get_ue_trace` | `_read_log_tail` |

## What each test file covers

### `test_tail_nf_logs.py`
- Input validation: unknown NF, bad level, `lines` out of range, invalid regex, bad `since` format
- Happy path: single NF, list of NFs, `"all"`, field schema, chronological ordering, `lines` cap
- Filtering: level exclusion, error-only level, grep keyword, grep regex, `since` relative window
- Errors: missing log file reported without blocking other NFs

### `test_system_health_snapshot.py`
- Input validation: `log_minutes` boundaries
- NF classification: all down → red, all clean → green, recent errors → yellow, endpoint unreachable → yellow
- Infrastructure: TUN missing, MongoDB down, RAN skipped when AMF is down, no gNBs
- Overall health: `healthy` / `degraded` / `critical` transitions
- Output schema: all top-level keys, per-NF keys, summary keys, `recent_errors` as `list[str]`

### `test_subscriber.py`
- `read`: missing IMSI, invalid IMSI, not found, happy path (digits + SUPI format), secrets redacted, MongoDB error
- `list`: limit validation, unsupported filter key, non-scalar filter value, returns all docs, filter by status, secrets redacted
- `create`: missing IMSI, invalid IMSI, duplicate, happy path, defaults merged
- `delete`: missing IMSI, invalid IMSI, happy path, not-found returns `deleted: false`
- Unknown action

### `test_subscriber_update_profile.py`
- Invalid IMSI, no fields supplied, subscriber not found, MongoDB error
- Update `subscriber_status`, update `ambr`, update `msisdn`
- Secrets redacted, SUPI format accepted

### `test_subscriber_update_slices.py`
- 9 validation cases: not a list, empty list, slice not a dict, missing `sst`, missing `session`, empty session, session not a dict, session missing `name`, subscriber not found
- Happy path: valid update, two DNNs, secrets redacted, SUPI format

### `test_list_ue_sessions.py`
- Validation: non-digit filter, too-short filter
- Happy path: single UE, AMF+SMF merge (IP from SMF), IMSI filter match/no-match/SUPI format, empty core, include_idle=False
- Output schema: all required UE and session fields
- Error handling: AMF unreachable, SMF unreachable (AMF data preserved), timeout

### `test_amf_ran_query.py`
- Happy path: PLMNs + gNBs, gNB field schema, no gNBs
- Output schema: all top-level keys
- Errors: curl failure, invalid JSON, gNB endpoint unreachable/timeout, subprocess timeout

### `test_nf_lifecycle.py`
- Parser unit tests: `_parse_status` (running/stopped/multi-NF/ANSI strip), `_parse_lifecycle` (started/error/stopped)
- Validation: invalid action, invalid NF, mixed list, script not found
- Status: all NFs, single NF, list of NFs, stderr included/absent
- Lifecycle: start, stop, restart, error → `ok: False`, subprocess timeout

### `test_read_nf_config.py`
- `_resolve_path` unit tests: single key, nested keys, list index, missing key, out-of-range index, non-integer index, traverse into scalar
- Validation: unknown NF, webui (no YAML), config file not found, YAML parse error
- Happy path: full config, dot-path subtree, scalar leaf, list index path, case-insensitive NF, bad path key → error, config_file path in response

### `test_nf_resource_usage.py`
- Validation: `sample_interval` bounds, invalid NF
- Happy path: running NF has all metric fields, not-running NF has `not_running` status
- Output schema: top-level, system, aggregates
- `time.sleep` called with correct interval
- Defaults to all NFs when `nfs=None`
- IO access denied → `io_note` field (graceful degradation)

## Adding tests for a new tool

1. Create `tests/test_<tool_name>.py`
2. Add `sys.path` is handled automatically by `conftest.py` — no setup needed
3. Import your tool function and any shared fixtures from `conftest`
4. Identify the I/O boundary (see table above for the pattern)
5. Write four test classes: `TestValidation`, `TestHappyPath`, `TestErrorHandling`, `TestOutputSchema`
6. Mark classes with `@pytest.mark.unit` (pure logic) or `@pytest.mark.integration` (mocked I/O)

Minimal template:

```python
from unittest.mock import patch
import pytest
from tools.my_tool import my_tool

@pytest.mark.unit
class TestValidation:
    def test_bad_input(self):
        r = my_tool(bad_param="x")
        assert r["ok"] is False
        assert "error" in r

@pytest.mark.integration
class TestHappyPath:
    @patch("tools.my_tool.<boundary>")
    def test_nominal(self, mock_boundary):
        mock_boundary.return_value = ...
        r = my_tool(good_param="y")
        assert r["ok"] is True
        assert "expected_key" in r
```

## Regression workflow

After any code change, run:

```bash
pytest tests/ -q
```

Expected output: all tests pass in under 2 seconds. If a test fails, the short
traceback (`--tb=short` is set in `pytest.ini`) shows the exact assertion and
line. Fix the tool or the test depending on whether the behaviour change was
intentional.
