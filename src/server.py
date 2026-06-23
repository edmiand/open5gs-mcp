"""MCP server — Open5GS 5G core management tools."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Literal

import uvicorn
import yaml
from starlette.applications import Starlette

# Make `src/` importable regardless of invocation method
sys.path.insert(0, str(Path(__file__).parent))

import mcp.server.sse as _mcp_sse
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.middleware.auth_context import get_access_token as _get_access_token
from sse_starlette.sse import EventSourceResponse as _ESR
from tools.nf_lifecycle import nf_lifecycle as _nf_lifecycle
from tools.system_health_snapshot import system_health_snapshot as _health
from tools.subscriber import subscriber as _subscriber
from tools.subscriber_update_profile import subscriber_update_profile as _subscriber_update_profile
from tools.subscriber_update_slices import subscriber_update_slices as _subscriber_update_slices
from tools.list_ue_sessions import list_ue_sessions as _list_ue_sessions
from tools.tail_nf_logs import tail_nf_logs as _tail_nf_logs
from tools.read_nf_config import read_nf_config as _read_nf_config
from tools.ue_trace import get_ue_trace as _get_ue_trace
from tools.amf_ran_query import amf_ran_query as _amf_ran_query
from tools.nf_resource_usage import nf_resource_usage as _nf_resource_usage

# FastMCP's SSE transport doesn't set a ping interval, so idle connections are
# dropped by NATs/firewalls after ~60 s. Patch the EventSourceResponse reference
# in the mcp.server.sse module so every SSE connection gets a 15 s keepalive.
class _ESRWithPing(_ESR):
    def __init__(self, *args, ping: int = 10, **kwargs):
        super().__init__(*args, ping=ping, **kwargs)

_mcp_sse.EventSourceResponse = _ESRWithPing


# ── Config loading ─────────────────────────────────────────────────────────────

_CONFIG_DEFAULTS: dict = {
    "server": {"host": "0.0.0.0", "port": 8080, "transport": "all"},
    "security": {
        "localhost_only": False,
        "auth_enabled": False,
        "token": "",
        "scope_enforcement": False,
    },
}


def _load_config(path: Path | None) -> dict:
    """Load server.yaml. Missing file or missing keys fall back to defaults."""
    if path is None:
        candidate = Path(__file__).parent.parent / "server.yaml"
        path = candidate if candidate.exists() else None
    if path is None or not path.exists():
        return {k: dict(v) for k, v in _CONFIG_DEFAULTS.items()}
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    result = {k: dict(v) for k, v in _CONFIG_DEFAULTS.items()}
    for section in ("server", "security"):
        if section in data and isinstance(data[section], dict):
            result[section].update(data[section])
    return result


def _early_config() -> dict:
    """Extract --config path from argv without consuming other flags, then load."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--config", default=None)
    ns, _ = p.parse_known_args()
    return _load_config(Path(ns.config) if ns.config else None)


_cfg = _early_config()
_srv = _cfg["server"]
_sec = _cfg["security"]

# Layer 3 is only active when Layer 2 is also on
_scope_enforce: bool = bool(_sec["scope_enforcement"] and _sec["auth_enabled"])


def _require_write_scope(operation: str) -> dict | None:
    """Return an envelope error dict if mcp:write scope is missing, else None."""
    tok = _get_access_token()
    if tok is None or "mcp:write" not in tok.scopes:
        _e = f"mcp:write scope required for {operation}"
        return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}
    return None

# ── Auth setup (Layer 2) ───────────────────────────────────────────────────────

_token_verifier = None
_auth_settings = None

if _sec["auth_enabled"]:
    from auth import StaticTokenVerifier, resolve_token
    from mcp.server.auth.settings import AuthSettings

    _resolved_token = resolve_token(_sec["token"])
    _token_verifier = StaticTokenVerifier(_resolved_token)
    _auth_settings = AuthSettings(
        issuer_url=f"http://localhost:{_srv['port']}",
        resource_server_url=None,
        required_scopes=["mcp:read"],
    )


# ── FastMCP instance ───────────────────────────────────────────────────────────

# Apply localhost_only here so the setting takes effect regardless of whether
# the server is run directly or imported as a module.
_effective_host = "127.0.0.1" if _sec["localhost_only"] else _srv["host"]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Open5GS MCP server")
    p.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse", "all"],
        default=_srv["transport"],
        help=f"Transport to use (default: {_srv['transport']})",
    )
    p.add_argument(
        "--host",
        default=_srv["host"],
        help=f"Bind host (default: {_srv['host']}). Ignored when security.localhost_only=true.",
    )
    p.add_argument(
        "--port",
        type=int,
        default=_srv["port"],
        help=f"Bind port (default: {_srv['port']})",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Path to server.yaml config file (default: auto-detect at project root)",
    )
    return p.parse_args()


mcp = FastMCP(
    name="open5gs-mcp",
    instructions=(
        "Manage Open5GS 5G core network functions. "
        "Use nf_lifecycle to start, stop, restart, or check the status of NFs."
    ),
    token_verifier=_token_verifier,
    auth=_auth_settings,
    host=_effective_host,
    port=_srv["port"],
)


# ── Tool registrations ─────────────────────────────────────────────────────────

@mcp.tool()
async def nf_lifecycle(
    action: Literal["start", "stop", "restart", "status"],
    nf: list[str] | None = None,
) -> dict:
    """Start, stop, restart, or query the status of Open5GS network functions.

    action: Operation to perform — one of start, stop, restart, status.
    nf:     NF names to target, e.g. ["amf", "smf"]. Omit to target all NFs.
            Valid values: amf, smf, upf, ausf, udm, udr, pcf, nssf, bsf, nrf, scp, webui.

    Returns a dict with "ok" (bool), "action", and "nfs" mapping each NF name to its
    result. For status: {status, pid, uptime}. For lifecycle ops: {result, pid}.
    UPF operations require sudo — the underlying script handles privilege escalation.
    """
    if _scope_enforce and action in ("start", "stop", "restart"):
        if err := _require_write_scope("lifecycle mutations"):
            return err
    return await asyncio.to_thread(_nf_lifecycle, action, nf)


@mcp.tool()
async def system_health_snapshot(log_minutes: int = 15) -> dict:
    """One-shot health check of the Open5GS 5G core.

    Polls all NF processes, scans recent logs for errors, checks MongoDB
    reachability, and verifies the ogstun TUN device. Call this first in any
    diagnostic session — it lets an agent triage the entire system in one call
    and decide which targeted tool to invoke next.

    log_minutes: How many minutes back to scan logs for errors (default 15, max 1440).

    Returns ok/timestamp plus:
      nfs      — per-NF status (green/yellow/red), pid, up to 3 recent error lines
      mongodb  — status + subscriber count
      tun      — ogstun device status
      summary  — overall health (healthy/degraded/critical) and counts
    """
    return await asyncio.to_thread(_health, log_minutes)


@mcp.tool()
async def subscriber(
    action: str,
    imsi: str | None = None,
    data: dict | None = None,
    limit: int = 100,
    filter: dict | None = None,
) -> dict:
    """Manage subscriber lifecycle — read, list, create, or delete.

    action: One of "read", "list", "create", "delete".
    imsi:   IMSI digits (10-15) or SUPI ("imsi-<digits>"). Required for read/create/delete.
    data:   For create only. Subscriber fields deep-merged with defaults.
              {"security": {"k": "<Ki>", "opc": "<OPc>"}, "msisdn": [...], ...}
    limit:  For list only. Max documents to return (1–1000, default 100).
    filter: For list only. Equality filter — allowed keys:
              subscriber_status, network_access_mode,
              access_restriction_data, operator_determined_barring

    Returns:
      read:   {"ok": True, "subscriber": {...}} (secrets redacted)
      list:   {"ok": True, "subscribers": [...], "count": int}
      create: {"ok": True, "subscriber": {...}} (secrets redacted)
      delete: {"ok": True, "deleted": bool, "imsi": str}
      error:  {"ok": False, "error": str}
    """
    if _scope_enforce and action in ("create", "delete"):
        if err := _require_write_scope("subscriber mutations"):
            return err
    return await asyncio.to_thread(_subscriber, action, imsi, data, limit, filter)


@mcp.tool()
async def subscriber_update_profile(
    imsi: str,
    security: dict | None = None,
    ambr: dict | None = None,
    msisdn: list | None = None,
    imeisv: list | None = None,
    mme_host: list | None = None,
    mme_realm: list | None = None,
    purge_flag: list | None = None,
    access_restriction_data: int | None = None,
    subscriber_status: int | None = None,
    network_access_mode: int | None = None,
    operator_determined_barring: int | None = None,
    subscribed_rau_tau_timer: int | None = None,
) -> dict:
    """Update subscriber profile parameters (excludes slice/session configuration).

    imsi: IMSI digits (10-15) or SUPI ("imsi-<digits>").

    Only supplied parameters are updated (deep merge for nested dicts).
    See subscriber_update_slices to change DNN/slice configuration.

    Returns updated subscriber document (secrets redacted).
    """
    if _scope_enforce:
        if err := _require_write_scope("subscriber_update_profile"):
            return err
    return await asyncio.to_thread(
        _subscriber_update_profile,
        imsi, security, ambr, msisdn, imeisv, mme_host, mme_realm, purge_flag,
        access_restriction_data, subscriber_status, network_access_mode,
        operator_determined_barring, subscribed_rau_tau_timer,
    )


@mcp.tool()
async def subscriber_update_slices(imsi: str, slices: list) -> dict:
    """Update subscriber slice and session (DNN) configuration.

    imsi:   IMSI digits (10-15) or SUPI ("imsi-<digits>").
    slices: Array of slice objects. Each slice must have:
              sst (int, required): Slice Service Type
              session (array, required): At least one session with:
                name (string, required): DNN (Data Network Name) or APN
                type (int): 1=IPv4, 2=IPv6, 3=IPv4v6
                qos, arp, ambr, ue, smf, pcc_rule (optional, complex objects)
            Optional: sd (Slice Differentiator), default_indicator, lbo_roaming_allowed

    The entire slice array is replaced (not merged). To add a second DNN,
    pass your full desired slice config including existing DNNs.

    Returns updated subscriber document (secrets redacted).
    """
    if _scope_enforce:
        if err := _require_write_scope("subscriber_update_slices"):
            return err
    return await asyncio.to_thread(_subscriber_update_slices, imsi, slices)


@mcp.tool()
async def list_ue_sessions(
    imsi_filter: str | None = None,
    include_idle: bool = True,
) -> dict:
    """List all live UE registrations and their PDU sessions.

    Queries the AMF (/ue-info) for registration context and the SMF (/pdu-info)
    for PDU session detail (including assigned IPs), then joins by SUPI.

    imsi_filter:  Optional IMSI prefix (digits or "imsi-<digits>") to narrow results.
    include_idle: Set False to return only UEs with at least one active PDU session.

    Returns ue_count, per-UE cm_state/ue_activity, and per-session detail:
      psi, dnn, S-NSSAI, ipv4/ipv6, state, QoS flows, N3 GTP-U endpoints.
    Also reports source reachability (sources.amf / sources.smf).
    """
    return await asyncio.to_thread(_list_ue_sessions, imsi_filter, include_idle)


@mcp.tool()
async def tail_nf_logs(
    nf: str | list[str] = "all",
    level: str = "info",
    grep: str | None = None,
    lines: int = 100,
    since: str | None = None,
) -> dict:
    """Filtered log reads across one or more Open5GS NF log files.

    Reads from each log file tail, filters, then interleaves results from all
    requested NFs in chronological order — ideal for correlating events across
    AMF + AUSF + UDM during a single registration or session failure.

    nf:     NF name, list of names, or "all". Valid: amf smf upf ausf udm udr
            pcf nssf bsf nrf scp webui
    level:  Minimum severity: debug | info | warn | error
    grep:   Optional keyword or Python regex (case-insensitive) applied to the
            raw log line. E.g. "imsi-999700", "Registration", "5QI|NSSAI"
    lines:  Max total lines to return across all NFs (default 100, max 500).
    since:  Time window start. Relative ("15m", "2h") or ISO datetime.
            Omit to read from the current tail without a time constraint.

    Returns total_matched, per-line {nf, timestamp, component, level, message,
    source}, per-NF line counts, and per-NF errors (e.g. UPF permission denied).
    """
    return await asyncio.to_thread(_tail_nf_logs, nf, level, grep, lines, since)


@mcp.tool()
async def read_nf_config(nf: str, path: str | None = None) -> dict:
    """Read the YAML configuration for any Open5GS network function.

    Parses install/etc/open5gs/<nf>.yaml and returns the full config tree,
    or a specific subtree when path is supplied. Use this to inspect why two
    NFs can't communicate (mismatched SBI addresses, wrong NRF/SCP URI),
    verify slice or subnet configuration, or check interface bindings — all
    without opening files manually. Also a prerequisite before patch_nf_config.

    nf:   NF name. Valid: amf smf upf ausf udm udr pcf nssf bsf nrf scp
    path: Optional dot-separated path into the config tree.
          Examples:
            "amf.sbi"                → SBI server/client addresses
            "amf.sbi.client.scp"     → SCP URI the AMF is pointing at
            "smf.pfcp.client.upf"    → UPF address SMF sends PFCP to
            "smf.session"            → UE IP subnet pool
            "amf.guami"              → PLMN + AMF ID
            "amf.plmn_support"       → supported PLMNs and slices
            "logger"                 → log file path and level
          List items can be indexed numerically: "amf.sbi.server.0"

    Returns ok, nf, config_file path, path echoed, and config subtree.
    """
    return await asyncio.to_thread(_read_nf_config, nf, path)


@mcp.tool()
async def get_ue_trace(
    supi: str,
    time_window_minutes: int = 60,
    include_nfs: list[str] | None = None,
) -> dict:
    """Collect full e2e trace for a UE identified by IMSI/SUPI across all Open5GS NFs.

    Searches AMF first to anchor the time window, then correlates logs from AUSF,
    UDM, UDR, SMF (PFCP SEIDs + UE IP), UPF, PCF, and NRF. Returns structured
    events suitable for reconstructing a Mermaid sequence diagram of the call flow.

    supi:                IMSI/SUPI string. Accepted formats:
                           "imsi-999700000000001"  (SUPI canonical form)
                           "999700000000001"       (bare digits)
                           "IMSI:999700000000001"  (colon-separated)
    time_window_minutes: How far back to search the AMF log (default 60, max 1440).
    include_nfs:         Subset of NFs to search. Defaults to all:
                           ["amf","ausf","udm","udr","smf","pcf","nrf","upf"]

    Returns:
      ok, supi, time_range, summary (registration_success, pdu_session_success,
      ue_ip_assigned, errors), events (sorted list of structured log events with
      timestamp/nf/level/direction/message_type/from/to/message),
      mermaid_hint (sequenceDiagram participant block), and nf_errors if any NF
      log was unreadable.
    """
    return await asyncio.to_thread(_get_ue_trace, supi, time_window_minutes, include_nfs)


@mcp.tool()
async def nf_resource_usage(
    nfs: list[str] | None = None,
    sample_interval: float = 1.0,
) -> dict:
    """CPU, memory, and I/O utilisation for each running Open5GS NF vs system totals.

    Takes two snapshots separated by sample_interval to compute per-process CPU %
    and I/O rates — the call blocks for at least that duration. Use this to
    identify which NF is consuming resources, spot memory leaks, or compare
    Open5GS load against overall system capacity.

    nfs:             NF names to sample (e.g. ["amf","smf"]). Omit for all NFs.
                     Valid: amf smf upf ausf udm udr pcf nssf bsf nrf scp webui
    sample_interval: Sampling window in seconds (0.1 – 10.0, default 1.0).
                     Larger values give more accurate CPU averages.

    Returns ok, timestamp, sample_interval_s, and:
      nfs        — per-NF {status, pid, cpu_percent, memory{rss_mb,vms_mb,percent},
                   io{read/write_bytes_per_s, read/write_total_mb}, threads}
      aggregates — nfs_running, total_cpu_percent, total_rss_mb,
                   total_io_read/write_bytes_per_s across all sampled NFs
      system     — cpu_count_logical/physical, cpu_percent_used,
                   memory_total/available/used_mb, memory_percent_used, disk_io
      open5gs_share — cpu_pct_of_system_usage, memory_pct_of_total
    """
    return await asyncio.to_thread(_nf_resource_usage, nfs, sample_interval)


@mcp.tool()
async def amf_ran_query() -> dict:
    """Query live RAN state from the AMF OAM API and metrics endpoint.

    Calls /namf-oam/v1/plmns for aggregate counts and PLMN/slice config, then
    /gnb-info for per-gNB detail. Use this to check whether gNBs are attached,
    inspect their TA/slice config, and count UEs per gNB before troubleshooting
    registration failures.

    Returns ok, connected_gnbs, registered_ues, total_plmns, plmns list
    (each entry: plmn_id, mcc, mnc, s_nssai[]), gnbs list (each entry:
    gnb_id, plmn, sctp_peer, supported_ta_list, num_connected_ues), and
    gnbs_status ("ok"|"unreachable"|"timeout"|"error").
    """
    return await asyncio.to_thread(_amf_ran_query)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = _parse_args()

    # Layer 1: localhost_only in config overrides --host
    if _sec["localhost_only"]:
        mcp.host = "127.0.0.1"
    else:
        mcp.host = args.host
    mcp.port = args.port

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "all":
        # Serve SSE (/sse, /messages) and streamable-http (/mcp) on one port
        # so ollmcp and mcp-tools both work without switching transports.
        # Each sub-app has its own lifespan; preserve both via a dispatcher.
        _sse_app  = mcp.sse_app()
        _http_app = mcp.streamable_http_app()

        from contextlib import asynccontextmanager
        from starlette.routing import Route, Router

        @asynccontextmanager
        async def _combined_lifespan(app):
            async with _sse_app.router.lifespan_context(_sse_app):
                async with _http_app.router.lifespan_context(_http_app):
                    yield

        # Route by path prefix: /mcp → streamable-http, everything else → SSE
        async def _dispatch(scope, receive, send):
            if scope["type"] == "lifespan":
                await _combined_lifespan_app(scope, receive, send)
                return
            path = scope.get("path", "")
            target = _http_app if path.startswith("/mcp") else _sse_app
            try:
                await target(scope, receive, send)
            except Exception:
                # A broken SSE connection must not crash the whole server.
                # Uvicorn treats an unhandled exception from run_asgi() as fatal
                # (sets should_exit=True), so we catch here and let it log the
                # traceback without propagating.
                logging.exception("Unhandled ASGI error on %s (connection dropped)", path)

        _combined_lifespan_app = Starlette(lifespan=_combined_lifespan)

        uvicorn.run(_dispatch, host=mcp.host, port=mcp.port,
                    log_level=mcp.settings.log_level.lower())
    else:
        mcp.run(transport=args.transport)
