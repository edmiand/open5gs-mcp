# open5gs-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes [Open5GS](https://open5gs.org) 5G core operations as tools for AI agents (Claude, Ollama, etc.).

Instead of SSH-ing into a server and grepping logs, an agent can call a single tool to triage the entire system, correlate logs across NFs, provision subscribers, or check live UE sessions.

---

## Tools

| # | Tool | What it does |
|---|------|-------------|
| 1 | `nf_lifecycle` | Start / stop / restart / status any NF |
| 2 | `system_health_snapshot` | One-shot triage: all NFs + MongoDB + TUN + recent errors |
| 3 | `subscriber_crud` | Full CRUD against the subscribers MongoDB collection |
| 4 | `list_ue_sessions` | Live UE registrations and PDU sessions from AMF + SMF |
| 5 | `tail_nf_logs` | Filtered log reads across one or more NFs, interleaved by timestamp |

> 7 more tools planned: `read_nf_config`, `patch_nf_config`, `subscriber_auth_reset`, `query_nf_metrics`, `network_infra_check`, `generate_credentials`, `nrf_registry_query`

---

## Requirements

- Open5GS installed at `../open5gs` (sibling directory)
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

### Start automatically on boot

```bash
./open5gs-mcp install
```

This installs a systemd service that starts after `network.target` and `mongod.service`.

### Start manually

```bash
./open5gs-mcp start    # runs on 0.0.0.0:8080
./open5gs-mcp status
./open5gs-mcp stop
./open5gs-mcp logs
```

---

## Connecting a client

The server exposes **both transports on port 8080** simultaneously — no configuration needed per client:

| Transport | Endpoint | Client |
|-----------|----------|--------|
| SSE | `http://<host>:8080/sse` | ollmcp, Claude Desktop |
| Streamable HTTP | `http://<host>:8080/mcp` | mcp-curl, any HTTP client |

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

### mcp-curl (quick tool listing)

```bash
./mcp-curl                              # list tools on localhost:8080
./mcp-curl http://192.168.1.10:8080/mcp # remote server
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
subscriber_crud(operation="create", imsi="999700000000002",
    data={"security": {"k": "<Ki>", "opc": "<OPc>"}})
→ {"ok": true, "subscriber": {...}}
```

**Check why a device can't get data**
```
list_ue_sessions(imsi_filter="999700000000001")
→ PDU session exists but ipv4: null → IP allocation failed at SMF/UPF
```

---

## Repo structure

```
open5gs-mcp/
├── src/
│   ├── server.py          # FastMCP server, tool registration
│   └── tools/
│       ├── nf_lifecycle.py
│       ├── system_health_snapshot.py
│       ├── subscriber_crud.py
│       ├── list_ue_sessions.py
│       └── tail_nf_logs.py
├── tests/
│   └── test_server.py     # integration tests over stdio
├── open5gs-mcp            # CLI: start / stop / restart / status / logs / install
├── open5gs-mcp.service    # systemd unit file
├── mcp-curl               # quick tool listing via HTTP
└── ollmcp-servers.json    # ready-made ollmcp server config
```
