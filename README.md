# open5gs-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes [Open5GS](https://open5gs.org) 5G core operations as tools for AI agents (Claude, Ollama, etc.).

Instead of SSH-ing into a server and grepping logs, an agent can call a single tool to triage the entire system, correlate logs across NFs, provision subscribers, or check live UE sessions.

---

## Tools

| # | Tool | What it does |
|---|------|-------------|
| 1 | `nf_lifecycle` | Start / stop / restart / status any NF |
| 2 | `system_health_snapshot` | One-shot triage: all NFs + MongoDB + TUN + recent errors |
| 3 | `subscriber` | Read / list / create / delete subscribers (action-dispatched) |
| 4 | `subscriber_update_profile` | Update profile params (security, AMBR, status, restrictions, etc.) |
| 5 | `subscriber_update_slices` | Update slice/session (DNN) configuration |
| 6 | `list_ue_sessions` | Live UE registrations and PDU sessions from AMF + SMF |
| 7 | `tail_nf_logs` | Filtered log reads across one or more NFs, interleaved by timestamp |
| 8 | `read_nf_config` | Read YAML config from any NF |
| 9 | `get_ue_trace` | E2E UE call flow reconstruction across all NFs |
| 10 | `amf_ran_query` | RAN state: connected gNBs, registered UEs, slices |
| 11 | `nf_resource_usage` | CPU/RAM/I/O utilisation per NF |

> More tools planned: `patch_nf_config`, `subscriber_auth_reset`, `query_nf_metrics`, `network_infra_check`, `generate_credentials`, `nrf_registry_query`

---

## Requirements

- Open5GS installed at `../open5gs` (sibling directory)
  - The `nf_lifecycle` tool requires `open5gs-ctl.sh` to be present in that directory.
    This script is **not part of vanilla Open5GS** — it ships with the
    [edmiand/open5gs](https://github.com/edmiand/open5gs) fork.
    Clone that fork (or copy the script) alongside this repo before using lifecycle operations.
  - Vanilla Open5GS manages NFs via `systemctl` (one service per NF).
    All other tools in this repo work with a vanilla install.
- Python 3.12+
- MongoDB running (`mongod.service`)

---

## Installation

```bash
git clone https://github.com/edmiand/open5gs-mcp
cd open5gs-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Start automatically on boot (recommended)

```bash
./open5gs-mcp install
```

Copies `open5gs-mcp.service` to `/etc/systemd/system/`, enables it, and starts it. The service restarts automatically on failure and after boot, and ensures the old process group is fully stopped before rebinding the port.

### Start manually

```bash
./open5gs-mcp start    # runs on 0.0.0.0:8080
./open5gs-mcp status
./open5gs-mcp stop
./open5gs-mcp restart
./open5gs-mcp logs
./open5gs-mcp uninstall   # remove systemd service
```

> **Note:** Always use `.venv/bin/python` (or the `open5gs-mcp` wrapper) when running the server manually. Running with the system `python3` will fail — dependencies including `psutil` are installed only inside the venv.

### Start options

```bash
./open5gs-mcp start --transport all      # default: SSE + Streamable HTTP on one port
./open5gs-mcp start --port 9090          # bind to a different port
./open5gs-mcp start --host 127.0.0.1    # localhost only
```

---

## Configuration

Edit `server.yaml` in the project root to configure the server. All security features are disabled by default.

```yaml
server:
  host: "0.0.0.0"
  port: 8080
  transport: "all"   # all | sse | streamable-http | stdio

security:
  # Layer 1: bind to 127.0.0.1 only (blocks remote connections at OS level)
  localhost_only: false

  # Layer 2: require Authorization: Bearer <token> on every request
  auth_enabled: false
  token: ""           # or set MCP_AUTH_TOKEN env var; leave blank to auto-generate

  # Layer 3: require mcp:write scope for destructive tools (needs auth_enabled: true)
  # Destructive tools: nf_lifecycle (start/stop/restart), subscriber (create/delete),
  #                    subscriber_update_profile, subscriber_update_slices
  scope_enforcement: false
```

Pass a custom config file path with `--config /path/to/server.yaml`.

---

## Connecting a client

The server exposes **both transports on port 8080** simultaneously — no configuration needed per client:

| Transport | Endpoint | Client |
|-----------|----------|--------|
| SSE | `http://<host>:8080/sse` | ollmcp, Claude Desktop |
| Streamable HTTP | `http://<host>:8080/mcp` | mcp-tools, any HTTP client |

### ollmcp (Ollama)

```bash
pip install mcp-client-for-ollama
```

Create `~/.config/ollmcp/config.json` (or use the included `ollmcp-servers.json`):

```json
{
  "mcpServers": {
    "open5gs": {
      "type": "sse",
      "url": "http://<server-ip>:8080/sse"
    }
  }
}
```

```bash
ollmcp -m qwen3:4b -j ollmcp-servers.json
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "open5gs": {
      "command": "/path/to/open5gs-mcp/.venv/bin/python",
      "args": ["/path/to/open5gs-mcp/src/server.py"]
    }
  }
}
```

### mcp-tools (quick tool inspection)

```bash
./mcp-tools                               # table of all tools (name + description)
./mcp-tools --tool get_ue_trace           # full description + parameter table for one tool
./mcp-tools --schema nf_lifecycle         # raw JSON inputSchema for one tool
./mcp-tools http://192.168.1.10:8080/mcp  # remote server
```

Set `MCP_AUTH_TOKEN` in the environment to authenticate against a token-protected server.

### ue-trace (call get_ue_trace from the shell)

```bash
./ue-trace imsi-999700000000001          # trace last 60 minutes
./ue-trace 999700000000001 30            # trace last 30 minutes
MCP_URL=http://192.168.1.10:8080/mcp ./ue-trace imsi-999700000000001
```

---

## Example agent workflows

**Triage a system in one call**
```
system_health_snapshot()
→ overall: critical, nrf: red, amf: green, mongodb: ok
→ agent decides to call nf_lifecycle(action="start", nf=["nrf"])
```

**Diagnose a registration failure**
```
tail_nf_logs(nf=["amf", "ausf", "udm"], grep="imsi-999700000000001", since="15m")
→ correlated timeline across 3 NFs in one result
```

**Provision a test SIM**
```
subscriber(action="create", imsi="999700000000002",
    data={"security": {"k": "<Ki>", "opc": "<OPc>"}})
→ {"ok": true, "subscriber": {...}}
```

**Check why a device can't get data**
```
list_ue_sessions(imsi_filter="999700000000001")
→ PDU session exists but ipv4: null → IP allocation failed at SMF/UPF
```

**Find all barred subscribers**
```
subscriber(action="list", filter={"subscriber_status": 1})
→ {"ok": true, "subscribers": [...], "count": 3, "returned": 3}
```

---

## Repo structure

```
open5gs-mcp/
├── src/
│   ├── server.py          # FastMCP server, tool registration, transport dispatch
│   ├── auth.py            # StaticTokenVerifier + token resolution (Layer 2)
│   └── tools/
│       ├── nf_lifecycle.py
│       ├── system_health_snapshot.py
│       ├── subscriber.py              # Action-dispatched: read/list/create/delete
│       ├── subscriber_update_profile.py
│       ├── subscriber_update_slices.py
│       ├── _subscriber_util.py        # Shared: IMSI validation, MongoDB, serialization
│       ├── _nf_util.py                # Shared: NF process helpers
│       ├── _log_util.py               # Shared: log parsing helpers
│       ├── list_ue_sessions.py
│       ├── tail_nf_logs.py
│       ├── read_nf_config.py
│       ├── ue_trace.py
│       ├── amf_ran_query.py
│       └── nf_resource_usage.py
├── tests/
│   ├── conftest.py
│   ├── test_server.py
│   ├── test_nf_lifecycle.py
│   ├── test_subscriber.py
│   ├── test_subscriber_update_profile.py
│   ├── test_subscriber_update_slices.py
│   ├── test_list_ue_sessions.py
│   ├── test_tail_nf_logs.py
│   ├── test_read_nf_config.py
│   ├── test_ue_trace.py
│   ├── test_amf_ran_query.py
│   ├── test_nf_resource_usage.py
│   └── test_system_health_snapshot.py
├── server.yaml            # server + security configuration (all defaults off)
├── open5gs-mcp            # CLI: start/stop/restart/status/logs/install/uninstall
├── open5gs-mcp.service    # systemd unit (Restart=always, clean port release on stop)
├── mcp-tools               # HTTP tool inspector (list / --tool / --schema)
├── ue-trace               # Shell wrapper for get_ue_trace
└── ollmcp-servers.json    # ready-made ollmcp server config
```
