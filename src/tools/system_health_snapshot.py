"""system_health_snapshot — one-shot health check of the Open5GS 5G core."""

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from tools._nf_util import get_nf_pid as _get_nf_pid, metrics_url as _metrics_url
from tools.tail_nf_logs import _read_nf_log, _LEVELS as _LOG_LEVELS

# NFs that expose subscriber-relevant HTTP info endpoints
_NF_INFO_ENDPOINTS: dict[str, str] = {
    "amf": "/ue-info",
    "smf": "/pdu-info",
}

# Startup dependency order (matches open5gs-ctl.sh)
_NFS = ["nrf", "scp", "amf", "smf", "upf", "ausf", "udm", "udr", "pcf", "nssf", "bsf", "webui"]

_WARNING_LEVEL = _LOG_LEVELS["WARNING"]


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
              "recent_errors": [str],         # up to 3 warning/error message strings
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
        return {"summary": "Error: log_minutes must be between 1 and 1440.",
                "detail": {"ok": False, "error": "log_minutes must be between 1 and 1440"}}

    nfs_result: dict[str, dict] = {}
    green = yellow = red = 0
    since_dt = datetime.now(timezone.utc) - timedelta(minutes=log_minutes)

    for nf in _NFS:
        pid = _get_nf_pid(nf)
        recent_errors: list[str] = []
        if pid:
            recs, _, _ = _read_nf_log(nf, _WARNING_LEVEL, None, since_dt, 20)
            # Sort by severity desc so FATAL/CRIT/ERROR lines surface before WARNINGs
            recs.sort(key=lambda r: _LOG_LEVELS.get(r.get("level", ""), 0), reverse=True)
            recent_errors = [r["message"] for r in recs[:3]]
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

    _summary_str = (
        f"System is {overall}: {green}/{len(_NFS)} NFs green, "
        f"MongoDB {mongodb['status']}, TUN {tun['status']}, "
        f"{ran.get('gnbs_connected', 0)} gNB(s) connected."
    )
    return {
        "summary": _summary_str,
        "detail": {
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
        },
    }
