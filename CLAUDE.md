# open5gs-mcp — Claude Code Context

## What this repo is
An MCP server that exposes Open5GS 5G core operations as tools for AI agents.
This repo is maintained separately from Open5GS.

## Open5GS location
Open5GS lives at ../open5gs (sibling directory).
Never modify files in ../open5gs directly.

## Key paths in Open5GS (read-only reference)
- Configs:  ../open5gs/install/etc/open5gs/<nf>.yaml
- Logs:     ../open5gs/install/var/log/open5gs/<nf>.log
- Scripts:  ../open5gs/misc/open5gs-ctl.sh
- PID files:../open5gs/install/var/run/
- Certs:    ../open5gs/misc/make-certs.sh, gen-hnkey.sh
- WebUI:    http://localhost:9999 (REST API for subscriber CRUD)
- MongoDB:  mongodb://localhost:27017, db: open5gs

## NF names
amf, smf, upf, ausf, udm, udr, pcf, nssf, bsf, nrf, scp, webui

## NF ports (Prometheus metrics)
All NFs expose metrics at http://localhost:9090/metrics
NRF SBI: http://localhost:7777

## AMF SBI / OAM API
- AMF SBI address: 127.0.0.5:7777 (reads from amf.yaml at runtime)
- AMF OAM endpoint: http://127.0.0.5:7777/namf-oam/v1/
- AMF speaks HTTP/2 prior knowledge (h2c) — no upgrade negotiation
- Always use `curl --http2-prior-knowledge`; httpx/httpcore will fail with HTTP/1.1

## MCP tools — built
- nf_lifecycle          — start/stop/restart/status any NF
- system_health_snapshot — full health check in one call
- subscriber_crud        — CRUD against subscribers collection
- list_ue_sessions       — active UE contexts and PDU sessions (AMF+SMF join)
- read_nf_config         — read any NF YAML config
- tail_nf_logs           — filtered log reads across NFs
- get_ue_trace           — e2e UE call flow reconstruction across all NFs
- amf_ran_query          — connected gNBs, registered UEs, PLMN/slice config (OAM API)

## MCP tools — not yet built
- subscriber_auth_reset  — update K/OPc/SQN for a SUPI
- patch_nf_config        — patch key paths in any NF YAML config
- query_nf_metrics       — scrape Prometheus metrics from any NF
- network_infra_check    — check/setup TUN devices
- generate_credentials   — run cert/key generation scripts
- nrf_registry_query     — query NRF for registered NF instances

## Developer utilities
- `mcp-curl` — shell script to inspect the running MCP server without a client
  - default: table view of all tools (name + first sentence of description)
  - `--tool <name>` — full description and parameter table for one tool
  - `--schema <name>` — raw JSON inputSchema for one tool
  - `--help` / `-h` — usage help
  - Uses Streamable HTTP transport (POST /mcp); handles session handshake automatically

## Constraints
- Never modify ../open5gs files
- UPF operations require sudo — handle gracefully
- Tools should return structured data (dicts/JSON), not raw strings
- Each tool must have input validation and a clear error message on failure
