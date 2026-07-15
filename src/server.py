"""MCP server — Open5GS 5G core management tools."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
import yaml
from pydantic import Field
from starlette.applications import Starlette

# Make `src/` importable regardless of invocation method
sys.path.insert(0, str(Path(__file__).parent))

import mcp.server.sse as _mcp_sse
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
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
        "Manage and observe an Open5GS 5G core: NF lifecycle and health "
        "(nf_lifecycle, system_health_snapshot, nf_resource_usage), subscriber "
        "provisioning in MongoDB (subscriber, subscriber_update_profile, "
        "subscriber_update_slices), live UE/RAN state (list_ue_sessions, "
        "amf_ran_query), and diagnostics (tail_nf_logs, read_nf_config, "
        "get_ue_trace).\n\n"
        "Every tool returns the same envelope: "
        '{"summary": <one-sentence string>, "detail": {"ok": <bool>, ...}}. '
        'On failure, summary starts with "Error: " and detail is '
        '{"ok": false, "error": <str>}. Each tool\'s return documentation '
        "describes the contents of detail."
    ),
    token_verifier=_token_verifier,
    auth=_auth_settings,
    host=_effective_host,
    port=_srv["port"],
)


# ── Tool registrations ─────────────────────────────────────────────────────────

# NF name enums, shared across tool signatures so schemas carry real enums.
_NF = Literal["amf", "smf", "upf", "ausf", "udm", "udr", "pcf", "nssf", "bsf",
              "nrf", "scp", "webui"]
_NF_OR_ALL = Literal["all", "amf", "smf", "upf", "ausf", "udm", "udr", "pcf",
                     "nssf", "bsf", "nrf", "scp", "webui"]
_YAML_NF = Literal["amf", "smf", "upf", "ausf", "udm", "udr", "pcf", "nssf",
                   "bsf", "nrf", "scp"]  # webui has no YAML config
_TRACE_NF = Literal["amf", "ausf", "udm", "udr", "smf", "pcf", "upf", "nrf"]

_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


@mcp.tool(annotations=_MUTATING)
async def nf_lifecycle(
    action: Annotated[
        Literal["start", "stop", "restart", "status"],
        Field(description="Operation to perform."),
    ],
    nf: Annotated[
        list[_NF] | None,
        Field(description='NF names to target, e.g. ["amf", "smf"]. '
                          "Omit to target all NFs."),
    ] = None,
) -> dict:
    """Start, stop, restart, or query the status of Open5GS network functions.

    Use this to bring NFs up or down, restart a crashed NF, or check which NFs are
    currently running — the first step before diagnosing a stopped service.
    UPF operations require sudo — the underlying script handles privilege escalation.

    detail contains: ok, action, and nfs mapping each NF name to its result —
    for status: {status: "running"|"stopped", pid, uptime}; for lifecycle ops:
    {result, pid} plus message on a per-NF error. stderr, error, and raw_output
    appear when the control script fails or emits unparseable output.
    """
    if _scope_enforce and action in ("start", "stop", "restart"):
        if err := _require_write_scope("lifecycle mutations"):
            return err
    return await asyncio.to_thread(_nf_lifecycle, action, nf)


@mcp.tool(annotations=_READ_ONLY)
async def system_health_snapshot(
    log_minutes: Annotated[
        int,
        Field(ge=1, le=1440,
              description="How many minutes back to scan logs for errors."),
    ] = 15,
) -> dict:
    """One-shot health check of the Open5GS 5G core.

    Polls all NF processes, scans recent logs for errors, checks MongoDB
    reachability, verifies the ogstun TUN device, and counts connected gNBs.
    Call this first in any diagnostic session — it lets an agent triage the
    entire system in one call and decide which targeted tool to invoke next.

    detail contains: ok, timestamp, plus
      nfs      — per-NF status (green/yellow/red), pid, up to 3 recent error
                 lines; endpoint reachability for amf/smf
      mongodb  — status + subscriber count
      tun      — ogstun device status
      ran      — gNB connectivity via the AMF (status, gnbs_connected)
      summary  — overall health (healthy/degraded/critical) and counts
    """
    return await asyncio.to_thread(_health, log_minutes)


@mcp.tool(annotations=_MUTATING)
async def subscriber(
    action: Annotated[
        Literal["read", "list", "create", "delete"],
        Field(description="Operation to perform."),
    ],
    imsi: Annotated[
        str | None,
        Field(description='IMSI digits (10-15) or SUPI ("imsi-<digits>"). '
                          "Required for read/create/delete."),
    ] = None,
    data: Annotated[
        dict | None,
        Field(description="For create only. Subscriber fields deep-merged with "
                          'defaults, e.g. {"security": {"k": "<Ki>", "opc": "<OPc>"}, '
                          '"msisdn": ["+1234567890"], "slice": [...]}.'),
    ] = None,
    limit: Annotated[
        int,
        Field(ge=1, le=1000,
              description="For list only. Max documents to return per page."),
    ] = 100,
    filter: Annotated[
        dict | None,
        Field(description="For list only. Equality filter; allowed keys: "
                          "subscriber_status (0=SERVICE_GRANTED, 1=OPERATOR_"
                          "DETERMINED_BARRING), network_access_mode, "
                          "access_restriction_data, operator_determined_barring. "
                          'E.g. {"subscriber_status": 1} lists barred subscribers.'),
    ] = None,
) -> dict:
    """Manage subscriber lifecycle — read, list, create, or delete.

    Use this to provision a new subscriber (create), look up their stored profile
    (read), enumerate all subscribers (list), or remove one (delete). To modify an
    existing subscriber's parameters, use subscriber_update_profile or
    subscriber_update_slices instead.

    detail contains:
      read/create: ok, subscriber (secrets redacted)
      list:        ok, subscribers, count (total matching documents in the DB),
                   returned (documents in this page, ≤ limit)
      delete:      ok, deleted (false when the IMSI did not exist), imsi
    """
    if _scope_enforce and action in ("create", "delete"):
        if err := _require_write_scope("subscriber mutations"):
            return err
    return await asyncio.to_thread(_subscriber, action, imsi, data, limit, filter)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                      idempotentHint=True, openWorldHint=False))
async def subscriber_update_profile(
    imsi: Annotated[
        str,
        Field(description='IMSI digits (10-15) or SUPI ("imsi-<digits>").'),
    ],
    security: Annotated[
        dict | None,
        Field(description="Authentication credentials, deep-merged: "
                          '{"k": "<Ki hex>", "opc": "<OPc hex>", "op": "<OP hex>", '
                          '"amf": "<AMF hex, e.g. 8000>", "sqn": <int>}.'),
    ] = None,
    ambr: Annotated[
        dict | None,
        Field(description="UE-AMBR: "
                          '{"downlink": {"value": <int>, "unit": <int>}, '
                          '"uplink": {...}} where unit is 0=bps, 1=Kbps, '
                          "2=Mbps, 3=Gbps, 4=Tbps."),
    ] = None,
    msisdn: Annotated[
        list | None,
        Field(description='Phone numbers, e.g. ["+1234567890"].'),
    ] = None,
    imeisv: Annotated[
        list | None,
        Field(description="IMEISV strings (equipment identity)."),
    ] = None,
    mme_host: Annotated[
        list | None,
        Field(description="EPC interworking: serving MME Diameter host(s)."),
    ] = None,
    mme_realm: Annotated[
        list | None,
        Field(description="EPC interworking: serving MME Diameter realm(s)."),
    ] = None,
    purge_flag: Annotated[
        list | None,
        Field(description="EPC interworking: UE-purged-in-MME flag(s)."),
    ] = None,
    access_restriction_data: Annotated[
        int | None,
        Field(description="Bitmask of restricted access types "
                          "(3GPP TS 29.272 §7.3.31), e.g. 32 = "
                          "HO-to-non-3GPP-access not allowed."),
    ] = None,
    subscriber_status: Annotated[
        int | None,
        Field(description="0=SERVICE_GRANTED, 1=OPERATOR_DETERMINED_BARRING "
                          "(TS 29.272 §7.3.29)."),
    ] = None,
    network_access_mode: Annotated[
        int | None,
        Field(description="0=PACKET_AND_CIRCUIT, 1=RESERVED, 2=ONLY_PACKET "
                          "(TS 29.272 §7.3.21)."),
    ] = None,
    operator_determined_barring: Annotated[
        int | None,
        Field(description="Barring category 0-8 (TS 29.272 §7.3.30); "
                          "0 = all packet-oriented services barred. Takes "
                          "effect when subscriber_status=1."),
    ] = None,
    subscribed_rau_tau_timer: Annotated[
        int | None,
        Field(description="Periodic RAU/TAU timer in minutes (default 12)."),
    ] = None,
) -> dict:
    """Update subscriber profile parameters (excludes slice/session configuration).

    Use this to change an existing subscriber's operational status (barring, network
    access mode), AMBR limits, MSISDN, or security credentials — without touching
    their slice/DNN configuration. The subscriber must already exist; use
    subscriber action="create" first if needed.

    Only supplied parameters are updated (deep merge for nested dicts).
    See subscriber_update_slices to change DNN/slice configuration.

    detail contains: ok, subscriber (the full updated document — imsi,
    subscriber_status, network_access_mode, access_restriction_data,
    operator_determined_barring, ambr, security (redacted), msisdn, slice, ...).
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                      idempotentHint=True, openWorldHint=False))
async def subscriber_update_slices(
    imsi: Annotated[
        str,
        Field(description='IMSI digits (10-15) or SUPI ("imsi-<digits>").'),
    ],
    action: Annotated[
        Literal["replace", "rename_session", "upsert_session", "remove_session"],
        Field(description="Operation to perform — see the tool description for "
                          "when to use each."),
    ],
    slices: Annotated[
        list | None,
        Field(description="replace only (required). Array of slice objects, each "
                          "with sst (int, required), session (array, required — "
                          "at least one, each with name (DNN, required), "
                          "type (1=IPv4, 2=IPv6, 3=IPv4v6), and optional "
                          "qos/arp/ambr/ue/smf/pcc_rule), plus optional sd, "
                          "default_indicator, lbo_roaming_allowed."),
    ] = None,
    sst: Annotated[
        int | None,
        Field(description="Slice Service Type identifying the target slice. "
                          "Required for rename/upsert/remove_session."),
    ] = None,
    sd: Annotated[
        str | None,
        Field(description="Slice Differentiator (6 hex digits). For rename/"
                          "upsert/remove_session: REQUIRED when multiple slices "
                          "share the same sst — otherwise the call is rejected "
                          "as ambiguous."),
    ] = None,
    old_name: Annotated[
        str | None,
        Field(description="rename_session only (required): current session name."),
    ] = None,
    new_name: Annotated[
        str | None,
        Field(description="rename_session only (required): new session name."),
    ] = None,
    session: Annotated[
        dict | None,
        Field(description="upsert_session only (required): session dict with at "
                          'least {"name": "<DNN>"}.'),
    ] = None,
    name: Annotated[
        str | None,
        Field(description="remove_session only (required): session name (DNN) "
                          "to remove."),
    ] = None,
) -> dict:
    """Update subscriber slice and session (DNN) configuration.

    ── replace ───────────────────────────────────────────────────────────────────
    Replace the entire slice array verbatim. Trigger: bulk reconfiguration or initial
    setup. All existing slices are discarded — to keep one, include it in `slices`
    (read the current config first via `subscriber action="read"`). Do NOT use this
    to rename or add/remove a single DNN — use rename_session/upsert_session/
    remove_session instead, which preserve everything else untouched.

    ── rename_session ────────────────────────────────────────────────────────────
    Rename a session (DNN) within a slice, preserving all QoS/AMBR/PCC fields.
    Trigger: DNN name is wrong and must be corrected — do NOT use replace or
    upsert_session to add the correct name; that creates a duplicate DNN.
    Requires sst, old_name, new_name (and sd when ambiguous).

    ── upsert_session ────────────────────────────────────────────────────────────
    Add a new session to a slice, or merge fields into an existing one (identified
    by session["name"]). Trigger: adding a second DNN, or patching QoS on one DNN
    without touching the others. Requires sst, session (and sd when ambiguous).

    ── remove_session ───────────────────────────────────────────────────────────
    Remove a session (DNN) from a slice by name. The slice must retain at least
    one session after removal. Trigger: decommissioning a DNN from a subscriber.
    Requires sst, name (and sd when ambiguous).

    detail contains: ok, subscriber (updated document, secrets redacted) with
    slice: [{sst, sd, session: [...]}].
    """
    if _scope_enforce:
        if err := _require_write_scope("subscriber_update_slices"):
            return err
    return await asyncio.to_thread(
        _subscriber_update_slices,
        imsi, action, slices, sst, sd, old_name, new_name, session, name,
    )


@mcp.tool(annotations=_READ_ONLY)
async def list_ue_sessions(
    imsi_filter: Annotated[
        str | None,
        Field(description='IMSI prefix (digits or "imsi-<digits>") to narrow '
                          "results."),
    ] = None,
    include_idle: Annotated[
        bool,
        Field(description="Set false to return only UEs with at least one "
                          "active PDU session."),
    ] = True,
) -> dict:
    """List all live UE registrations and their PDU sessions.

    Use this to check how many UEs are currently registered and what data sessions
    they have active — the right first step when verifying a successful attach,
    debugging connectivity, or auditing capacity.

    Queries the AMF (/ue-info) for registration context and the SMF (/pdu-info)
    for PDU session detail (including assigned IPs), then joins by SUPI.

    detail contains: ok, timestamp, ue_count, ues (per-UE cm_state, ue_activity,
    slices, location, and pdu_sessions: psi, dnn, snssai, ipv4/ipv6, state,
    qos_flows, N3 GTP-U endpoints), and sources — per-source reachability
    (sources.amf / sources.smf).
    """
    return await asyncio.to_thread(_list_ue_sessions, imsi_filter, include_idle)


@mcp.tool(annotations=_READ_ONLY)
async def tail_nf_logs(
    nf: Annotated[
        _NF_OR_ALL | list[_NF],
        Field(description='NF name, list of names, or "all".'),
    ] = "all",
    level: Annotated[
        Literal["debug", "info", "warn", "warning", "error"],
        Field(description="Minimum severity to include (error → only "
                          "ERROR/CRIT/FATAL)."),
    ] = "info",
    grep: Annotated[
        str | None,
        Field(description="Keyword or Python regex (case-insensitive) matched "
                          'against the raw log line, e.g. "imsi-999700", '
                          '"Registration", "5QI|NSSAI".'),
    ] = None,
    lines: Annotated[
        int,
        Field(ge=1, le=500,
              description="Max total lines to return across all NFs."),
    ] = 100,
    since: Annotated[
        str | None,
        Field(description='Time window start — relative ("15m", "2h") or ISO '
                          'datetime ("2026-06-03T20:00:00"). Omit to read from '
                          "the current tail without a time constraint."),
    ] = None,
) -> dict:
    """Filtered log reads across one or more Open5GS NF log files.

    Reads the last ~2 MB of each requested log file, filters by level/keyword/
    time window, then interleaves results from all NFs in chronological order —
    ideal for correlating events across AMF + AUSF + UDM during a single
    registration or session failure.

    detail contains: ok, query (arguments echoed), total_matched, lines — each
    {nf, timestamp, component, level, message, source} — nf_counts per NF, and
    errors per NF that could not be read (e.g. UPF log permission denied).
    When a since= window predates the 2 MB tail, detail also carries
    truncated: true and earliest_available (the oldest timestamp actually
    readable) — results before that point are missing, not absent.
    """
    return await asyncio.to_thread(_tail_nf_logs, nf, level, grep, lines, since)


@mcp.tool(annotations=_READ_ONLY)
async def read_nf_config(
    nf: Annotated[
        _YAML_NF,
        Field(description="NF name (webui has no YAML config)."),
    ],
    path: Annotated[
        str | None,
        Field(description="Dot-separated path into the config tree, e.g. "
                          '"amf.sbi.client.scp". List items can be indexed '
                          'numerically: "amf.sbi.server.0". Omit for the '
                          "full tree."),
    ] = None,
) -> dict:
    """Read the YAML configuration for any Open5GS network function.

    Parses install/etc/open5gs/<nf>.yaml and returns the full config tree,
    or a specific subtree when path is supplied. Use this to inspect why two
    NFs can't communicate (mismatched SBI addresses, wrong NRF/SCP URI),
    verify slice or subnet configuration, or check interface bindings — all
    without opening files manually.

    Useful paths:
      "amf.sbi"                → SBI server/client addresses
      "amf.sbi.client.scp"     → SCP URI the AMF is pointing at
      "smf.pfcp.client.upf"    → UPF address SMF sends PFCP to
      "smf.session"            → UE IP subnet pool
      "amf.guami"              → PLMN + AMF ID
      "amf.plmn_support"       → supported PLMNs and slices
      "logger"                 → log file path and level

    detail contains: ok, nf, config_file (absolute path), path (echoed), and
    config (the parsed subtree — full tree when path omitted).
    """
    return await asyncio.to_thread(_read_nf_config, nf, path)


@mcp.tool(annotations=_READ_ONLY)
async def get_ue_trace(
    supi: Annotated[
        str,
        Field(description='IMSI/SUPI string: "imsi-999700000000001" (canonical), '
                          '"999700000000001" (bare digits), or '
                          '"IMSI:999700000000001" (colon-separated).'),
    ],
    time_window_minutes: Annotated[
        int,
        Field(ge=1, le=1440,
              description="How far back to search the AMF log."),
    ] = 60,
    include_nfs: Annotated[
        list[_TRACE_NF] | None,
        Field(description="Subset of NFs to search. Defaults to "
                          '["amf","ausf","udm","udr","smf","pcf","upf"] — '
                          "NRF is excluded unless listed here or "
                          "include_nrf=true."),
    ] = None,
    window_padding_seconds: Annotated[
        int,
        Field(ge=0, le=60,
              description="Seconds added to both ends of the derived search "
                          "window; increase on loaded systems where NF clocks "
                          "lag."),
    ] = 5,
    include_nrf: Annotated[
        bool,
        Field(description="Also search the NRF log and include NF-lifecycle "
                          'events (direction="internal").'),
    ] = False,
) -> dict:
    """Collect full e2e trace for a UE identified by IMSI/SUPI across all Open5GS NFs.

    Use this when a UE fails to register, authenticate, or establish a PDU session
    and you need to reconstruct the full signalling call flow across NFs — the
    output is structured for generating a Mermaid sequence diagram.

    Searches AMF first to anchor the time window, then correlates logs from AUSF,
    UDM, UDR, SMF (PFCP SEIDs + UE IP), UPF, and PCF (plus NRF when requested).

    detail contains: ok, supi, time_range, summary (registration_success,
    pdu_session_success, ue_ip_assigned, errors, total_events), events (sorted
    list of structured log events with timestamp/nf/level/direction/
    message_type/from/to/message), mermaid_hint (sequenceDiagram participant
    block), and nf_errors if any NF log was unreadable.
    """
    return await asyncio.to_thread(
        _get_ue_trace, supi, time_window_minutes, include_nfs,
        window_padding_seconds, include_nrf,
    )


@mcp.tool(annotations=_READ_ONLY)
async def nf_resource_usage(
    nfs: Annotated[
        list[_NF] | None,
        Field(description='NF names to sample, e.g. ["amf", "smf"]. '
                          "Omit for all NFs."),
    ] = None,
    sample_interval: Annotated[
        float,
        Field(ge=0.1, le=10.0,
              description="Sampling window in seconds. Larger values give "
                          "more accurate CPU averages."),
    ] = 1.0,
) -> dict:
    """CPU, memory, and I/O utilisation for each running Open5GS NF vs system totals.

    Takes two snapshots separated by sample_interval to compute per-process CPU %
    and I/O rates — the call blocks for at least that duration. Use this to
    identify which NF is consuming resources, spot memory leaks, or compare
    Open5GS load against overall system capacity.

    detail contains: ok, timestamp, sample_interval_s, and:
      nfs        — per-NF {status, pid, cpu_percent, memory{rss_mb,vms_mb,percent},
                   io{read/write_bytes_per_s, read/write_total_mb}, threads}
      aggregates — nfs_running, total_cpu_percent, total_rss_mb,
                   total_io_read/write_bytes_per_s across all sampled NFs
      system     — cpu_count_logical/physical, cpu_percent_used,
                   memory_total/available/used_mb, memory_percent_used, disk_io
      open5gs_share — cpu_pct_of_system_usage, memory_pct_of_total
    """
    return await asyncio.to_thread(_nf_resource_usage, nfs, sample_interval)


@mcp.tool(annotations=_READ_ONLY)
async def amf_ran_query() -> dict:
    """Query live RAN state from the AMF OAM API and metrics endpoint.

    Calls /namf-oam/v1/plmns for aggregate counts and PLMN/slice config, then
    /gnb-info for per-gNB detail. Use this to check whether gNBs are attached,
    inspect their TA/slice config, and count UEs per gNB before troubleshooting
    registration failures.

    detail contains: ok, connected_gnbs, registered_ues, total_plmns, plmns list
    (each entry: plmn_id, mcc, mnc, s_nssai[]), gnbs list (each entry: gnb_id,
    plmn, sctp_peer, supported_ta_list, num_connected_ues), and gnbs_status
    ("ok"|"unreachable"|"timeout"|"error").
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
