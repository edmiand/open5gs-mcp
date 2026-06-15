"""Bearer token authentication — used by server.py when security.auth_enabled=true."""

import os
import secrets

from mcp.server.auth.provider import AccessToken, TokenVerifier

SCOPE_READ  = "mcp:read"
SCOPE_WRITE = "mcp:write"


class StaticTokenVerifier:
    """Single static bearer token; grants mcp:read + mcp:write on every valid request."""

    def __init__(self, token: str):
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if secrets.compare_digest(token, self._token):
            return AccessToken(
                token=token,
                client_id="open5gs-mcp-client",
                scopes=[SCOPE_READ, SCOPE_WRITE],
            )
        return None


def resolve_token(config_token: str) -> str:
    """Return a token from: MCP_AUTH_TOKEN env var → config file → auto-generated."""
    token = os.environ.get("MCP_AUTH_TOKEN") or config_token.strip()
    if not token:
        token = secrets.token_urlsafe(32)
        print(
            f"[open5gs-mcp] No token configured — generated for this session: {token}\n"
            f"              Set MCP_AUTH_TOKEN env var or security.token in server.yaml to persist it.",
            flush=True,
        )
    return token
