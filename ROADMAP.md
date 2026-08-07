# open5gs-mcp — Roadmap

Build status for the MCP tools. See `CLAUDE.md` for repo context and constraints;
see `FEATURES.md` for the fuller backlog of candidate tools discovered by NF
API/source analysis (that list predates several of the "built" tools below and
hasn't been reconciled against them yet).

## Built (12)
- `nf_lifecycle`              — start/stop/restart/status any NF
- `system_health_snapshot`    — full health check in one call
- `subscriber`                — read/list/create/delete subscribers (action-dispatched)
- `subscriber_update_profile` — update profile params (security, AMBR, status, restrictions)
- `subscriber_update_slices`  — update slice/session (DNN) configuration
- `list_ue_sessions`          — active UE contexts and PDU sessions (AMF+SMF join)
- `read_nf_config`            — read any NF YAML config
- `tail_nf_logs`              — filtered log reads across NFs
- `get_ue_trace`              — e2e UE call flow reconstruction across all NFs
- `amf_ran_query`             — connected gNBs, registered UEs, PLMN/slice config (OAM API)
- `nf_resource_usage`         — CPU/RAM/I/O per-NF utilisation vs system totals
- `open5gs_version`           — installed Open5GS version (from a compiled binary's -v)

## Not yet built (6)
- `subscriber_auth_reset` — update K/OPc/SQN for a SUPI
- `patch_nf_config`       — patch key paths in any NF YAML config
- `query_nf_metrics`      — scrape Prometheus metrics from any NF
- `network_infra_check`   — check/setup TUN devices
- `generate_credentials`  — run cert/key generation scripts
- `nrf_registry_query`    — query NRF for registered NF instances

Next up: `subscriber_auth_reset`.
