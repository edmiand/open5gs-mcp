"""system_health_snapshot — one-shot health check of the Open5GS 5G core."""

import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from tools._nf_util import LOG_DIR as _LOG_DIR, RUN_DIR as _RUN_DIR, metrics_url as _metrics_url

# NFs that expose subscriber-relevant HTTP info endpoints
_NF_INFO_ENDPOINTS: dict[str, str] = {
    "amf": "/ue-info",
    "smf": "/pdu-info",
}

# Startup dependency order (matches open5gs-ctl.sh)
_NFS = ["nrf", "scp", "amf", "smf", "upf", "ausf", "udm", "udr", "pcf", "nssf", "bsf", "webui"]

# Log line: optional ANSI prefix + MM/DD HH:MM:SS
_TS_RE = re.compile(r"^(?:\x1b\[[0-9;]*m)?(\d{2}/\d{2} \d{2}:\d{2}:\d{2})")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Open5GS error levels
_ERR_RE = re.compile(r"\b(?:ERROR|CRIT|FATAL|WARNING)\b")

# ── NF process detection ───────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").is_dir()


def _get_nf_pid(nf: str) -> int | None:
    # 1. pidfile
    pidfile = _RUN_DIR / f"{nf}.pid"
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            if _pid_alive(pid):
                return pid
        except (ValueError, OSError):
            pass

    # 2. pgrep by binary name
    if nf != "webui":
        try:
            r = subprocess.run(
                ["pgrep", f"open5gs-{nf}d"],
                capture_output=True, text=True, timeout=3,
            )
            for tok in r.stdout.split():
                pid = int(tok)
                if _pid_alive(pid):
                    return pid
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        return None

    # 3. webui: node process listening on port 9999
    try:
        r = subprocess.run(
            ["ss", "-tlnp", "sport", "=", ":9999"],
            capture_output=True, text=True, timeout=3,
        )
        m = re.search(r"pid=(\d+)", r.stdout)
        if m:
            pid = int(m.group(1))
            if _pid_alive(pid):
                return pid
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# ── log scanning ───────────────────────────────────────────────────────────────

def _parse_log_ts(token: str, year: int, now_md: tuple[int, int]) -> datetime | None:
    """Parse MM/DD HH:MM:SS into a datetime, handling year rollover."""
    try:
        dt = datetime.strptime(f"{year}/{token}", "%Y/%m/%d %H:%M:%S")
        # If the log date is in the future (e.g. 12/31 read on 01/02), use prior year
        now_m, now_d = now_md
        log_m, log_d = dt.month, dt.day
        if (log_m, log_d) > (now_m, now_d):
            dt = dt.replace(year=year - 1)
        return dt
    except ValueError:
        return None


def _scan_log(nf: str, minutes: int) -> list[str]:
    """Return up to 3 recent error/warning lines from the last `minutes` minutes."""
    logfile = _LOG_DIR / f"{nf}.log"
    if not logfile.exists():
        return []

    now = datetime.now()
    cutoff = now - timedelta(minutes=minutes)
    now_md = (now.month, now.day)
    errors: list[str] = []

    try:
        with open(logfile, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 65536))
            text = fh.read().decode("utf-8", errors="replace")

        for line in text.splitlines():
            m = _TS_RE.match(line)
            if not m:
                continue
            ts = _parse_log_ts(m.group(1), now.year, now_md)
            if ts is None or ts < cutoff:
                continue
            if _ERR_RE.search(line):
                errors.append(_ANSI_RE.sub("", line).rstrip())

        return errors[-3:]
    except OSError:
        return []


# ── infrastructure checks ──────────────────────────────────────────────────────

def _check_mongodb(uri: str = "mongodb://localhost:27017") -> dict:
    try:
        from pymongo import MongoClient
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        n = client["open5gs"]["subscribers"].count_documents({})
        return {"status": "ok", "subscribers": n}
    except Exception as exc:
        return {"status": "down", "error": str(exc)[:120]}


def _check_tun(device: str = "ogstun") -> dict:
    try:
        r = subprocess.run(
            ["ip", "link", "show", device],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            return {"status": "missing", "device": device}
        line = r.stdout.split("\n")[0]
        if "LOWER_UP" in line:
            return {"status": "ok", "device": device, "detail": line.strip()}
        if "UP" in line:
            return {"status": "down", "device": device, "detail": line.strip()}
        return {"status": "missing", "device": device}
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"status": "unknown", "device": device, "error": str(exc)}


# ── RAN connectivity check ────────────────────────────────────────────────────

def _check_ran() -> dict:
    """Return gNB count from AMF /gnb-info. Lightweight: fetches page 0 only."""
    url = _metrics_url("amf") + "/gnb-info?page=0&page_size=1"
    try:
        r = httpx.get(url, timeout=2.0)
        data = r.json()
        count = data.get("pager", {}).get("count", len(data.get("items", [])))
        status = "ok" if count > 0 else "no_gnbs"
        return {"status": status, "gnbs_connected": count}
    except httpx.ConnectError:
        return {"status": "unreachable", "gnbs_connected": 0}
    except httpx.TimeoutException:
        return {"status": "timeout", "gnbs_connected": 0}
    except Exception as exc:
        return {"status": "error", "gnbs_connected": 0, "error": str(exc)[:80]}


# ── NF info endpoint probes ────────────────────────────────────────────────────

def _probe_nf_endpoint(nf: str) -> str:
    """Return 'ok' if the NF info endpoint responds, 'unreachable' otherwise."""
    path = _NF_INFO_ENDPOINTS.get(nf)
    if not path:
        return "n/a"
    url = _metrics_url(nf) + path
    try:
        r = httpx.get(url, timeout=2.0)
        return "ok" if r.status_code < 500 else "error"
    except Exception:
        return "unreachable"


# ── main ───────────────────────────────────────────────────────────────────────

def system_health_snapshot(log_minutes: int = 15) -> dict:
    """
    One-shot health check of the Open5GS 5G core.

    Polls all NF processes, scans recent logs for errors, checks MongoDB
    reachability, and verifies the TUN device. Designed to be called first
    in any diagnostic session so an agent can decide which targeted tool to
    invoke next without making 6+ separate calls.

    Args:
        log_minutes: How many minutes back to scan logs for errors (1–1440).

    Returns:
        {
          "ok": bool,
          "timestamp": ISO-8601 UTC string,
          "nfs": {
            "<name>": {
              "status": "green" | "yellow" | "red",
              "pid": int | None,
              "recent_errors": [str],         # up to 3 stripped log lines
              "endpoint": "ok"|"unreachable"|"error"  # amf/smf only; absent for other NFs
            }
          },
          "mongodb": {"status": "ok"|"down", "subscribers": int, ...},
          "tun":     {"status": "ok"|"down"|"missing"|"unknown", ...},
          "ran":     {"status": "ok"|"no_gnbs"|"unreachable"|"timeout"|"error", "gnbs_connected": int},
          "summary": {
            "overall":    "healthy" | "degraded" | "critical",
            "nfs_green":  int,
            "nfs_yellow": int,
            "nfs_red":    int,
            "nfs_total":  int,
            "mongodb":    str,
            "tun":        str,
            "ran":        str,
          }
        }
    """
    if not (1 <= log_minutes <= 1440):
        return {"ok": False, "error": "log_minutes must be between 1 and 1440"}

    nfs_result: dict[str, dict] = {}
    green = yellow = red = 0

    for nf in _NFS:
        pid = _get_nf_pid(nf)
        recent_errors = _scan_log(nf, log_minutes) if pid else []
        endpoint = _probe_nf_endpoint(nf) if pid else None

        if pid is None:
            status, red = "red", red + 1
        elif recent_errors or endpoint in ("unreachable", "error"):
            status, yellow = "yellow", yellow + 1
        else:
            status, green = "green", green + 1

        nf_entry: dict = {"status": status, "pid": pid, "recent_errors": recent_errors}
        if endpoint is not None and endpoint != "n/a":
            nf_entry["endpoint"] = endpoint
        nfs_result[nf] = nf_entry

    mongodb = _check_mongodb()
    tun = _check_tun()
    amf_up = nfs_result.get("amf", {}).get("pid") is not None
    ran = _check_ran() if amf_up else {"status": "unreachable", "gnbs_connected": 0}

    infra_ok = mongodb["status"] == "ok" and tun["status"] in ("ok", "down")
    ran_ok = ran["status"] in ("ok", "unreachable")
    overall = (
        "healthy"  if red == 0 and yellow == 0 and infra_ok and ran_ok else
        "degraded" if red == 0 else
        "critical"
    )

    return {
        "ok": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nfs": nfs_result,
        "mongodb": mongodb,
        "tun": tun,
        "ran": ran,
        "summary": {
            "overall":    overall,
            "nfs_green":  green,
            "nfs_yellow": yellow,
            "nfs_red":    red,
            "nfs_total":  len(_NFS),
            "mongodb":    mongodb["status"],
            "tun":        tun["status"],
            "ran":        ran["status"],
        },
    }
