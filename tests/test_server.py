"""Integration test: spin up the server and exercise it over stdio."""

import asyncio
import json
import sys
from pathlib import Path

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = [sys.executable, str(Path(__file__).parent.parent / "src" / "server.py")]

_EXPECTED_TOOLS = {
    "nf_lifecycle", "system_health_snapshot", "subscriber",
    "subscriber_update_profile", "subscriber_update_slices",
    "list_ue_sessions", "read_nf_config", "tail_nf_logs",
    "get_ue_trace", "amf_ran_query", "nf_resource_usage",
    "open5gs_version",
}


@pytest.mark.unit
def test_server_module_registers_all_tools():
    """Importing server.py must not raise.

    All @mcp.tool() decorators run at import time, which is where they build
    each tool's pydantic output schema from its TypedDict return annotation —
    a stdlib typing.TypedDict/NotRequired (valid on 3.12+ but not on the 3.10
    venv this deploys to) blows up here, not in any tool's own unit tests.
    """
    import server

    names = set(server.mcp._tool_manager._tools.keys())
    assert names == _EXPECTED_TOOLS


async def run():
    params = StdioServerParameters(command=SERVER[0], args=SERVER[1:])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ── 1. tools/list ────────────────────────────────────────────
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"[tools/list] {tool_names}")
            assert "nf_lifecycle" in tool_names, "nf_lifecycle not registered"

            schema = next(t for t in tools.tools if t.name == "nf_lifecycle")
            action_enum = schema.inputSchema["properties"]["action"]["enum"]
            print(f"[schema]     action enum = {action_enum}")
            assert set(action_enum) == {"start", "stop", "restart", "status"}

            # ── 2. status — all NFs ──────────────────────────────────────
            result = await session.call_tool("nf_lifecycle", {"action": "status"})
            payload = json.loads(result.content[0].text)
            print(f"[status all] ok={payload['ok']}  nfs={list(payload['nfs'].keys())}")
            assert payload["ok"] is True
            assert set(payload["nfs"].keys()) >= {"amf", "smf", "upf", "nrf"}
            for name, info in payload["nfs"].items():
                assert info["status"] in ("running", "stopped"), f"{name}: bad status"

            # ── 3. status — single NF ────────────────────────────────────
            result = await session.call_tool("nf_lifecycle", {"action": "status", "nf": ["amf"]})
            payload = json.loads(result.content[0].text)
            print(f"[status amf] {payload['nfs']}")
            assert "amf" in payload["nfs"]
            assert len(payload["nfs"]) == 1

            # ── 4. validation errors surface cleanly ─────────────────────
            # Invalid action: caught by FastMCP/Pydantic before our code runs.
            # FastMCP returns isError=True with a plain-text Pydantic message.
            result = await session.call_tool("nf_lifecycle", {"action": "nuke"})
            msg = result.content[0].text
            print(f"[bad action] isError={result.isError}  msg={msg[:80]}")
            assert result.isError is True
            assert "nuke" in msg

            # Invalid NF name: passes Pydantic (any list[str] is valid) so our
            # validator runs and returns a structured JSON error.
            result = await session.call_tool("nf_lifecycle", {"action": "status", "nf": ["bogus"]})
            payload = json.loads(result.content[0].text)
            print(f"[bad nf]     ok={payload['ok']}  error={payload['error']}")
            assert payload["ok"] is False
            assert "bogus" in payload["error"]

            print("\nAll assertions passed.")


if __name__ == "__main__":
    anyio.run(run)
