"""nf_lifecycle — start/stop/restart/status any Open5GS network function.

Depends on open5gs-ctl.sh being present at ../open5gs/open5gs-ctl.sh.
This script is not part of vanilla Open5GS; it ships with the
edmiand/open5gs fork (https://github.com/edmiand/open5gs).
Vanilla Open5GS manages NFs via systemd (systemctl start open5gs-amfd, etc.).
"""

import re
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent.parent.parent / "open5gs" / "open5gs-ctl.sh"

VALID_NFS = frozenset(
    {"amf", "smf", "upf", "ausf", "udm", "udr", "pcf", "nssf", "bsf", "nrf", "scp", "webui"}
)
VALID_ACTIONS = frozenset({"start", "stop", "restart", "status"})

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_STATUS_ROW_RE = re.compile(r"^(\S+)\s+(running|stopped)\s*(\d+)?\s*(\S+)?")
_LIFECYCLE_LINE_RE = re.compile(r"^(\w+):\s+(.+)$")
_PID_RE = re.compile(r"\(pid (\d+)\)")


def _wrap(summary: str, detail: dict) -> dict:
    return {"summary": summary, "detail": detail}


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _validate(action: str, nfs: list[str]) -> str | None:
    if action not in VALID_ACTIONS:
        return f"Invalid action '{action}'. Must be one of: {sorted(VALID_ACTIONS)}"
    for nf in nfs:
        if nf not in VALID_NFS:
            return f"Invalid NF '{nf}'. Must be one of: {sorted(VALID_NFS)}"
    return None


def _parse_status(stdout: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for line in _strip_ansi(stdout).splitlines():
        m = _STATUS_ROW_RE.match(line.strip())
        if not m:
            continue
        name = m.group(1)
        if name not in VALID_NFS:
            continue
        pid_str = m.group(3)
        result[name] = {
            "status": m.group(2),
            "pid": int(pid_str) if pid_str else None,
            "uptime": m.group(4) or None,
        }
    return result


def _parse_lifecycle(stdout: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for line in _strip_ansi(stdout).splitlines():
        m = _LIFECYCLE_LINE_RE.match(line.strip())
        if not m:
            continue
        name, msg = m.group(1), m.group(2)
        if name not in VALID_NFS:
            continue
        pid_m = _PID_RE.search(msg)
        pid = int(pid_m.group(1)) if pid_m else None
        is_error = "ERROR" in msg
        entry: dict = {
            "result": "error" if is_error else msg.split(" (")[0].strip(),
            "pid": pid,
        }
        if is_error:
            entry["message"] = msg
        result[name] = entry
    return result


def nf_lifecycle(action: str, nf: str | list[str] | None = None) -> dict:
    """
    Manage Open5GS network function lifecycle.

    Args:
        action: "start" | "stop" | "restart" | "status"
        nf:     NF name or list of names. None targets all NFs.

    Returns:
        {
            "ok": bool,
            "action": str,
            "nfs": {
                "<name>": {
                    # status action:
                    "status": "running" | "stopped",
                    "pid": int | None,
                    "uptime": str | None,

                    # start/stop/restart action:
                    "result": str,   # e.g. "started", "stopped", "already running", "error"
                    "pid": int | None,
                    "message": str,  # only present on error
                }
            },
            "stderr": str,  # only present if non-empty
        }
    """
    if isinstance(nf, str):
        nf_list = [nf]
    elif nf is None:
        nf_list = []
    else:
        nf_list = list(nf)

    err = _validate(action, nf_list)
    if err:
        return _wrap(f"Error: {err}", {"ok": False, "error": err})

    if not _SCRIPT.exists():
        _e = f"Control script not found: {_SCRIPT}"
        return _wrap(f"Error: {_e}", {"ok": False, "error": _e})

    try:
        proc = subprocess.run(
            ["bash", str(_SCRIPT), action] + nf_list,
            capture_output=True,
            text=True,
            timeout=60,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        return _wrap("Error: open5gs-ctl.sh timed out after 60 s",
                     {"ok": False, "error": "open5gs-ctl.sh timed out after 60 s"})
    except Exception as exc:
        return _wrap(f"Error: {exc}", {"ok": False, "error": str(exc)})

    stdout = proc.stdout
    stderr = proc.stderr.strip()

    if action == "status":
        nfs = _parse_status(stdout)
    else:
        nfs = _parse_lifecycle(stdout)

    has_errors = any(v.get("result") == "error" for v in nfs.values())

    if action == "status":
        running = sum(1 for v in nfs.values() if v.get("status") == "running")
        _summary = f"{len(nfs)} NF(s) queried: {running} running, {len(nfs) - running} stopped."
    else:
        err_nfs = [n for n, v in nfs.items() if v.get("result") == "error"]
        _summary = (
            f"{action.capitalize()} completed with errors on: {', '.join(err_nfs)}."
            if err_nfs else f"{action.capitalize()} succeeded for {len(nfs)} NF(s)."
        )

    detail: dict = {
        "ok": proc.returncode == 0 and not has_errors,
        "action": action,
        "nfs": nfs,
    }
    if stderr:
        detail["stderr"] = stderr
    if not nfs and proc.returncode != 0:
        detail["error"] = f"script exited {proc.returncode} with no parseable output"
        detail["raw_output"] = stdout.strip()
        _summary = f"Script exited {proc.returncode} with no parseable output."
    return _wrap(_summary, detail)
