"""MCP server — Open5GS 5G core management tools."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Literal

# Make `src/` importable regardless of invocation method
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP
from tools.nf_lifecycle import nf_lifecycle as _nf_lifecycle

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


if __name__ == "__main__":
    args = _parse_args()
    mcp.host = args.host
    mcp.port = args.port
    mcp.run(transport=args.transport)
