"""MCP server — Open5GS 5G core management tools."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Literal

# Make `src/` importable regardless of invocation method
sys.path.insert(0, str(Path(__file__).parent))

import mcp.server.sse as _mcp_sse
from mcp.server.fastmcp import FastMCP
from sse_starlette.sse import EventSourceResponse as _ESR
from tools.nf_lifecycle import nf_lifecycle as _nf_lifecycle
from tools.system_health_snapshot import system_health_snapshot as _health
from tools.subscriber_crud import subscriber_crud as _subscriber_crud
from tools.list_ue_sessions import list_ue_sessions as _list_ue_sessions

# FastMCP's SSE transport doesn't set a ping interval, so idle connections are
# dropped by NATs/firewalls after ~60 s. Patch the EventSourceResponse reference
# in the mcp.server.sse module so every SSE connection gets a 15 s keepalive.
class _ESRWithPing(_ESR):
    def __init__(self, *args, ping: int = 10, **kwargs):
        super().__init__(*args, ping=ping, **kwargs)

_mcp_sse.EventSourceResponse = _ESRWithPing

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Open5GS MCP server")
    p.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport to use (default: stdio)",
    )
    p.add_argument("--host", default="0.0.0.0", help="Bind host for HTTP transports (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=8080, help="Bind port for HTTP transports (default: 8080)")
    return p.parse_args()


mcp = FastMCP(
    name="open5gs-mcp",
    instructions=(
        "Manage Open5GS 5G core network functions. "
        "Use nf_lifecycle to start, stop, restart, or check the status of NFs."
    ),
    host="0.0.0.0",
    port=8080,
)


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
async def subscriber_crud(
    operation: Literal["create", "read", "update", "delete", "list"],
    imsi: str | None = None,
    data: dict | None = None,
    limit: int = 100,
) -> dict:
    """Full CRUD against the Open5GS subscribers MongoDB collection.

    operation: create | read | update | delete | list
    imsi:  IMSI (10-15 digits) or SUPI ("imsi-<digits>").
           Required for create / read / update / delete.
    data:  Subscriber fields for create or update (deep-merged with defaults).
           Minimum for create: {"security": {"k": "<Ki>", "opc": "<OPc>"}}
           Any subset of: security, ambr, slice, msisdn, access_restriction_data.
    limit: Max results for list (default 100, max 1000).

    AMBR units: 0=bps 1=Kbps 2=Mbps 3=Gbps  |  Session type: 1=IPv4 2=IPv6 3=IPv4v6

    Returns subscriber document for create/read/update; deletion status for
    delete; subscriber list + count for list.
    """
    return await asyncio.to_thread(_subscriber_crud, operation, imsi, data, limit)


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


if __name__ == "__main__":
    args = _parse_args()
    mcp.host = args.host
    mcp.port = args.port
    mcp.run(transport=args.transport)
