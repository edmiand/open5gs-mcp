"""amf_ran_query — query live RAN state from AMF OAM API."""

import json
import subprocess
from pathlib import Path

import yaml

_CONFIG_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "open5gs" / "install" / "etc" / "open5gs"
)


def _amf_sbi_url() -> str:
    """Read AMF SBI address and port from amf.yaml."""
    try:
        cfg_path = _CONFIG_DIR / "amf.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        srv = cfg["amf"]["sbi"]["server"][0]
        port = srv.get("port", 7777)
        return f"http://{srv['address']}:{port}"
    except Exception:
        return "http://127.0.0.5:7777"


def _oam_get(url: str) -> tuple[dict | None, str]:
    """HTTP/2 prior-knowledge GET via curl. Returns (data, error)."""
    try:
        result = subprocess.run(
            ["curl", "-sf", "--http2-prior-knowledge", url],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None, f"curl error {result.returncode}: {result.stderr.strip()}"
        return json.loads(result.stdout), ""
    except subprocess.TimeoutExpired:
        return None, "request timed out"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"


def amf_ran_query() -> dict:
    """
    Query live RAN state from the AMF OAM API.

    Returns a summary of connected gNBs, registered UEs, and configured
    PLMNs with their S-NSSAI slices.

    Returns:
        {
          "ok": bool,
          "connected_gnbs": int,
          "registered_ues": int,
          "total_plmns": int,
          "plmns": [
            {
              "plmn_id": str,      # e.g. "99970"
              "mcc": int,
              "mnc": int,
              "s_nssai": [{"sst": int, "sd": str | None}]
            }
          ],
          "error": str             # only present on failure
        }
    """
    base = _amf_sbi_url()
    data, err = _oam_get(f"{base}/namf-oam/v1/plmns")
    if data is None:
        return {"ok": False, "error": err}

    return {
        "ok": True,
        "connected_gnbs": data.get("connected_gnbs", 0),
        "registered_ues": data.get("registered_ues", 0),
        "total_plmns": data.get("total_plmns", 0),
        "plmns": data.get("plmns", []),
    }
