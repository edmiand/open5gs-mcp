191 passed, zero failures. Everything is done. Here's a summary of what was built:

---

### What was implemented

**`server.yaml`** (project root) — the single place to configure all three layers. All values default to disabled, so your existing agents on the remote VM continue to work with no changes.

**`src/auth.py`** — `StaticTokenVerifier` and `resolve_token()`. Kept separate from `server.py` so it's testable and doesn't pollute the tool registration code.

**`src/server.py`** — three targeted changes:
- `_load_config()` + `_early_config()` run at module load, auto-detecting `server.yaml` at the project root (or via `--config /path/to/file`)
- `FastMCP(token_verifier=..., auth=...)` only wired when `auth_enabled: true` — when disabled, the constructor args are `None` and FastMCP behaves exactly as before
- Scope guard added to 4 destructive tools (`nf_lifecycle`, `subscriber`, `subscriber_update_profile`, `subscriber_update_slices`) — only active when both `auth_enabled` and `scope_enforcement` are `true`

**`mcp-curl`** — reads `MCP_AUTH_TOKEN` env var and injects `Authorization: Bearer <token>` into all three curl calls. Gives a clear 401 error message instead of a confusing failure.

---

### Turning on security (when you're ready)

Edit `server.yaml`:
```yaml
security:
  auth_enabled: true
  token: ""        # leave blank to use MCP_AUTH_TOKEN env var
  scope_enforcement: true
```

Then set the token on the server: `export MCP_AUTH_TOKEN=<your-token>` and on each client add `Authorization: Bearer <your-token>` to the MCP client's headers config.
