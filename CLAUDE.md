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
- Scripts:  ../open5gs/open5gs-ctl.sh
- Upgrade:  ../open5gs/open5gs-upgrade.sh — safe, reversible upgrade of the Open5GS checkout
- PID files:../open5gs/install/var/run/
- Certs:    ../open5gs/misc/make-certs.sh, gen-hnkey.sh
- WebUI:    http://localhost:9999 (REST API for subscriber CRUD)
- MongoDB:  mongodb://localhost:27017, db: open5gs

## NF names
amf, smf, upf, ausf, udm, udr, pcf, nssf, bsf, nrf, scp, webui

## NF ports (Prometheus metrics)
Every NF exposes metrics at `<nf-loopback-addr>:9090/metrics` — the address is
per-NF (e.g. AMF `127.0.0.5`, SMF `127.0.0.4`), read from each NF's own YAML
config (`metrics.server`), not a single shared `localhost` port. See
`_METRICS_DEFAULTS` in `src/tools/_nf_util.py` for the fallback map.
NRF SBI: http://localhost:7777

## AMF SBI / OAM API
- AMF SBI address: 127.0.0.5:7777 (reads from amf.yaml at runtime)
- AMF OAM endpoint: http://127.0.0.5:7777/namf-oam/v1/
- AMF speaks HTTP/2 prior knowledge (h2c) — no upgrade negotiation
- Always use `curl --http2-prior-knowledge`; httpx/httpcore will fail with HTTP/1.1

## MCP tools
Build status (done vs. planned) lives in ROADMAP.md — check there before assuming
a tool exists or picking the next one to build. FEATURES.md has the fuller backlog
of candidate tools discovered by NF API/source analysis.

## Developer utilities
- `mcp-tools` — shell script to inspect the running MCP server without a client
  - default: table view of all tools (name + first sentence of description)
  - `--tool <name>` — full description and parameter table for one tool
  - `--schema <name>` — raw JSON inputSchema for one tool
  - `--help` / `-h` — usage help
  - Uses Streamable HTTP transport (POST /mcp); handles session handshake automatically
- `mcp-upgrade.sh` — safe, reversible upgrade of this MCP server: fetch, show diff,
  snapshot rollback point + tool schemas, pull, sync deps, run tests, restart, verify,
  auto-rollback on failed restart. `--dry-run` / `--yes` / `--skip-tests` / `--rollback`

## Constraints
- Never modify ../open5gs files
- UPF operations require sudo — handle gracefully
- All tools must return the standard envelope: `{"summary": "<one sentence>", "detail": {...}}`
  - `summary` is always a plain string; starts with `"Error: "` on failure
  - `detail` contains the full structured payload (`{"ok": True/False, ...}`)
- Each tool must have input validation and a clear error message on failure

## subscriber_update_slices — action semantics
Action-dispatched tool, four actions:
- `replace` — the `slices` array is written verbatim to MongoDB (full replace, no merge,
  no guessing at intent). To keep an existing slice/session, the caller must read the
  current config first (`subscriber action="read"`) and include it alongside any changes.
- `rename_session` — rename one session (DNN) within a slice, preserving its other fields.
- `upsert_session` — add a new session to a slice, or merge fields into an existing one.
- `remove_session` — remove one session from a slice (a slice must keep at least one).
For `rename_session`/`upsert_session`/`remove_session`, `sd` must be supplied when more
than one slice shares the same `sst` — otherwise the call is rejected as ambiguous.
