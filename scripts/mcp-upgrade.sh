#!/usr/bin/env bash
# mcp-upgrade.sh — safe, reversible upgrade of the open5gs-mcp server on this VM.
#
# This script lives inside the very repo it upgrades and calls `git pull` on
# it while running. To survive that safely, the whole script body is defined
# as a function (main, below) and only invoked at the very last line — bash
# parses a function body into memory in full before executing any of it, so
# rewriting the file mid-run (via git) cannot corrupt an already-started
# function the way it could a top-level script being read line-by-line.
# Never move logic to the top level of this file for that reason.
#
# Flow: fetch origin -> show what's new -> (confirm) -> snapshot rollback
# point + current tool schemas -> pull -> sync deps -> run tests (against
# new code, old process still serving) -> (confirm) -> restart via systemd
# -> wait for the port -> diff new tool schemas against the snapshot ->
# roll back automatically only if the service fails to come back up.
#
# Usage:
#   mcp-upgrade.sh --dry-run     Show what's new, stop there
#   mcp-upgrade.sh                Full upgrade, asks to confirm
#   mcp-upgrade.sh --yes          Full upgrade, no prompts
#   mcp-upgrade.sh --skip-tests   Skip the pytest gate (not recommended)
#   mcp-upgrade.sh --rollback     Revert code to the last recorded good commit
#
set -uo pipefail

main() {
    local MCP_DIR="${MCP_DIR:-/home/dmandrey/open5gs-mcp}"
    local SCRIPT_DIR="$MCP_DIR/scripts"
    local STATE_DIR="$SCRIPT_DIR/.state"
    local LOG_DIR="$SCRIPT_DIR/logs"
    local SHA_FILE="$STATE_DIR/mcp-last-good.sha"
    local RUN_LOG="$LOG_DIR/mcp-upgrade-$(date -u +%Y%m%dT%H%M%SZ).log"
    local MCP_URL="${MCP_URL:-http://localhost:8080/mcp}"
    mkdir -p "$STATE_DIR" "$LOG_DIR"

    local DRY_RUN=0 ASSUME_YES=0 SKIP_TESTS=0 DO_ROLLBACK=0
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dry-run)    DRY_RUN=1; shift ;;
            --yes|-y)     ASSUME_YES=1; shift ;;
            --skip-tests) SKIP_TESTS=1; shift ;;
            --rollback)   DO_ROLLBACK=1; shift ;;
            -h|--help)
                sed -n '2,20p' "$MCP_DIR/scripts/mcp-upgrade.sh" | sed 's/^# \{0,1\}//'
                exit 0 ;;
            *) echo "Unknown option: $1" >&2; exit 2 ;;
        esac
    done

    exec > >(tee -a "$RUN_LOG") 2>&1

    echo "== mcp-upgrade $(date -u +%FT%TZ) =="
    echo "Target: $MCP_DIR"
    echo

    [[ -d "$MCP_DIR/.git" ]] || { echo "ERROR: not a git checkout: $MCP_DIR" >&2; exit 1; }
    local CTL="$MCP_DIR/open5gs-mcp"
    [[ -x $CTL ]] || { echo "ERROR: control script not found: $CTL" >&2; exit 1; }

    if (( DO_ROLLBACK )); then
        do_manual_rollback "$MCP_DIR" "$SHA_FILE" "$CTL"
        exit $?
    fi

    # ── 1. refuse on dirty tree (tracked files only — scratch/state files
    #      like .mcp-server.state are expected to be untracked and harmless) ─
    local dirty
    dirty=$(git -C "$MCP_DIR" status --porcelain --untracked-files=no)
    if [[ -n $dirty ]]; then
        echo "ERROR: $MCP_DIR has uncommitted changes to tracked files:" >&2
        echo "$dirty" >&2
        echo "Commit, stash, or discard them before upgrading." >&2
        exit 1
    fi

    # ── 2. fetch + show what's new ──────────────────────────────────────────
    echo "Fetching origin..."
    git -C "$MCP_DIR" fetch origin || { echo "ERROR: git fetch origin failed" >&2; exit 1; }

    local old_sha new_sha
    old_sha=$(git -C "$MCP_DIR" rev-parse HEAD)
    new_sha=$(git -C "$MCP_DIR" rev-parse origin/main)

    if [[ $old_sha == "$new_sha" ]]; then
        echo "Already up to date with origin/main ($old_sha)."
        exit 0
    fi

    if ! git -C "$MCP_DIR" merge-base --is-ancestor HEAD origin/main; then
        echo "ERROR: HEAD is not an ancestor of origin/main — local history has diverged." >&2
        echo "This script only fast-forwards; resolve the divergence manually first." >&2
        exit 1
    fi

    echo
    echo "New commits (HEAD..origin/main):"
    git -C "$MCP_DIR" log --oneline "HEAD..origin/main" | sed 's/^/  /'
    echo
    echo "Files changed:"
    git -C "$MCP_DIR" diff --stat "HEAD..origin/main" | sed 's/^/  /'
    echo

    local deps_touched
    deps_touched=$(git -C "$MCP_DIR" diff --name-only "HEAD..origin/main" -- requirements.txt)

    if (( DRY_RUN )); then
        echo "Dry run — stopping here. No changes made."
        exit 0
    fi

    # ── 3. confirm ───────────────────────────────────────────────────────────
    if (( ! ASSUME_YES )); then
        confirm "Proceed with pull, dependency sync, tests, and a restart?" || { echo "Aborted."; exit 1; }
    fi

    # ── 4. snapshot rollback point + current tool schemas ───────────────────
    echo "$old_sha" > "$SHA_FILE"
    echo "Recorded rollback point: $old_sha -> $SHA_FILE"

    local old_schema="$LOG_DIR/schema-before-$new_sha.json"
    if ! fetch_tool_schemas "$MCP_URL" "$old_schema"; then
        echo "WARNING: could not snapshot current tool schemas (is the server up at $MCP_URL?)."
        echo "Continuing without a pre-upgrade schema baseline — post-upgrade diff will be skipped."
        old_schema=""
    fi

    # ── 5. pull ──────────────────────────────────────────────────────────────
    echo "Pulling origin/main (fast-forward only)..."
    git -C "$MCP_DIR" merge --ff-only origin/main || { echo "ERROR: fast-forward merge failed unexpectedly." >&2; exit 1; }

    # ── 6. sync deps ─────────────────────────────────────────────────────────
    if [[ -n $deps_touched ]]; then
        echo "requirements.txt changed — syncing venv..."
        "$MCP_DIR/.venv/bin/pip" install -q -r "$MCP_DIR/requirements.txt" \
            || { echo "ERROR: pip install failed. Rolling back code..." >&2; rollback_code "$MCP_DIR" "$old_sha"; exit 1; }
    fi

    # ── 7. test gate — runs against the new code while the OLD process is
    #      still serving traffic, so a failure here costs nothing live ──────
    if (( ! SKIP_TESTS )); then
        echo "Running test suite (pytest -m 'not live')..."
        if ! "$MCP_DIR/.venv/bin/pytest" -c "$MCP_DIR/pytest.ini" --rootdir "$MCP_DIR" "$MCP_DIR/tests"; then
            echo "ERROR: tests failed against the new code. NOT restarting — the old version is still running." >&2
            echo "Code on disk is now at $new_sha but the live process is untouched. Fix the failure, or run:" >&2
            echo "  $0 --rollback" >&2
            exit 1
        fi
    else
        echo "--skip-tests given: skipping the pytest gate (not recommended for production)."
    fi

    # ── 8. confirm restart specifically (tests passing doesn't mean "go") ──
    if (( ! ASSUME_YES )); then
        confirm "Tests passed. Restart the live MCP server now?" || {
            echo "Not restarting. Code on disk is at $new_sha; old process ($old_sha) still serving."
            echo "Restart later with: $CTL restart"
            exit 1
        }
    fi

    # ── 9. restart (open5gs-mcp already knows systemd vs. manual) ──────────
    echo "Restarting..."
    "$CTL" restart || { echo "ERROR: restart command failed. Attempting rollback..." >&2; rollback_and_restart "$MCP_DIR" "$old_sha" "$CTL"; exit 1; }

    # ── 10. wait for the port, verify liveness ──────────────────────────────
    echo "Waiting for server to come up..."
    if ! wait_for_port 8080 15; then
        echo "== FAILED == server did not come up on port 8080 within 15s. Rolling back to $old_sha..." >&2
        rollback_and_restart "$MCP_DIR" "$old_sha" "$CTL"
        if wait_for_port 8080 15; then
            echo "== ROLLED BACK OK == server is back on $old_sha and listening. Upgrade to $new_sha did NOT stick." >&2
            exit 1
        fi
        echo "== CRITICAL == rollback did not come up either. Manual intervention required:" >&2
        echo "  journalctl -u open5gs-mcp.service -n100 --no-pager" >&2
        echo "  Old (known-good) commit: $old_sha   Attempted: $new_sha" >&2
        exit 2
    fi

    echo "Server is up. Verifying it actually answers tools/list..."
    local new_schema="$LOG_DIR/schema-after-$new_sha.json"
    if ! fetch_tool_schemas "$MCP_URL" "$new_schema"; then
        echo "== FAILED == server is listening but did not answer an MCP tools/list call. Rolling back..." >&2
        rollback_and_restart "$MCP_DIR" "$old_sha" "$CTL"
        exit 1
    fi

    echo
    echo "== SUCCESS == open5gs-mcp upgraded $old_sha -> $new_sha, server is up and answering MCP calls."

    # ── 11. schema compatibility check (informational, not a rollback trigger) ─
    if [[ -n $old_schema ]]; then
        echo
        echo "Tool schema compatibility report ($old_sha -> $new_sha):"
        if python3 "$SCRIPT_DIR/lib/mcp_schema_diff.py" "$old_schema" "$new_schema"; then
            echo "No breaking changes to tool schemas."
        else
            echo
            echo "BREAKING tool schema changes detected above. The server itself is healthy — this is not"
            echo "a service failure — but the AI agent app on the other VM may call these tools with the"
            echo "old shapes. Verify it's compatible before relying on the affected tools. This upgrade was"
            echo "NOT rolled back automatically for this reason alone; run '$0 --rollback' if you want to revert."
        fi
    fi

    exit 0
}

confirm() {
    local prompt=$1 ans
    read -r -p "$prompt [y/N] " ans
    [[ $ans == y || $ans == Y || $ans == yes ]]
}

wait_for_port() {
    local port=$1 timeout=$2 i=0
    while (( i < timeout * 2 )); do
        ss -tln 2>/dev/null | grep -q ":${port} " && return 0
        sleep 0.5; (( i++ ))
    done
    return 1
}

# fetch_tool_schemas <mcp_url> <out_file> — MCP initialize handshake +
# tools/list, writes the raw `result.tools` JSON array to out_file.
fetch_tool_schemas() {
    local mcp_url=$1 out_file=$2
    local init session body
    init=$(curl -si --max-time 5 -X POST "$mcp_url" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"mcp-upgrade","version":"1"}}}') || return 1

    session=$(echo "$init" | grep -i "^mcp-session-id:" | awk '{print $2}' | tr -d '\r')
    [[ -n $session ]] || return 1

    curl -s --max-time 5 -X POST "$mcp_url" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -H "Mcp-Session-Id: $session" \
        -d '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' > /dev/null

    body=$(mktemp)
    curl -s --max-time 5 -X POST "$mcp_url" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -H "Mcp-Session-Id: $session" \
        --data-binary @<(printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}') \
        -o "$body" || { rm -f "$body"; return 1; }

    python3 - "$body" "$out_file" <<'PYEOF'
import json, sys
raw = open(sys.argv[1]).read()
line = next((l for l in raw.splitlines() if l.startswith("data: ")), None)
if not line:
    sys.exit(1)
tools = json.loads(line[6:])["result"]["tools"]
json.dump(tools, open(sys.argv[2], "w"))
PYEOF
    local rc=$?
    rm -f "$body"
    return $rc
}

rollback_code() {
    local mcp_dir=$1 target_sha=$2
    git -C "$mcp_dir" reset --hard "$target_sha"
}

rollback_and_restart() {
    local mcp_dir=$1 target_sha=$2 ctl=$3
    rollback_code "$mcp_dir" "$target_sha"
    "$ctl" restart
    sleep 1
}

do_manual_rollback() {
    local mcp_dir=$1 sha_file=$2 ctl=$3
    [[ -f $sha_file ]] || { echo "ERROR: no recorded rollback point at $sha_file." >&2; return 1; }
    local target_sha; target_sha=$(cat "$sha_file")
    echo "Rolling back $mcp_dir to $target_sha..."
    rollback_and_restart "$mcp_dir" "$target_sha" "$ctl"
    if wait_for_port 8080 15; then
        echo "Rollback OK — server is on $target_sha and listening."
        return 0
    fi
    echo "Rollback completed but server did not come back up — investigate manually." >&2
    return 1
}

main "$@"
