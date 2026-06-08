"""amf_plmn_manage — add or remove PLMNs from the live AMF configuration."""

import json
import subprocess
from pathlib import Path
from typing import Literal

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


def _oam_request(method: str, url: str, body: dict | None = None) -> tuple[dict | None, int, str]:
    """HTTP/2 prior-knowledge request via curl. Returns (data, status_code, error)."""
    cmd = ["curl", "-s", "--http2-prior-knowledge", "-X", method,
           "-w", "\n%{http_code}", url]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        # curl -w appends status code on its own line
        parts = result.stdout.rsplit("\n", 1)
        status = int(parts[-1]) if len(parts) == 2 and parts[-1].isdigit() else 0
        body_text = parts[0].strip()
        if result.returncode != 0 and not body_text:
            return None, status, f"curl error {result.returncode}: {result.stderr.strip()}"
        data = json.loads(body_text) if body_text else {}
        return data, status, ""
    except subprocess.TimeoutExpired:
        return None, 0, "request timed out"
    except json.JSONDecodeError as exc:
        return None, 0, f"invalid JSON: {exc}"


def amf_plmn_manage(
    action: Literal["add", "delete"],
    mcc: str,
    mnc: str,
    s_nssai: list[dict] | None = None,
) -> dict:
    """
    Add or remove a PLMN from the live AMF configuration.

    Changes take effect immediately — the AMF sends an AMFConfigurationUpdate
    over NGAP to all connected gNBs. On delete, UEs on that PLMN are released.

    Args:
        action:  "add" or "delete".
        mcc:     Mobile Country Code string, e.g. "999".
        mnc:     Mobile Network Code string, e.g. "70" or "001".
        s_nssai: Required for "add". List of slice dicts, each with:
                   - "sst" (int, required)
                   - "sd"  (str hex, optional, e.g. "000001")
                 Ignored for "delete".

    Returns:
        {
          "ok": bool,
          "action": "add" | "delete",
          "plmn_id": str,          # e.g. "99970"
          "message": str,
          "error": str             # only present on failure
        }
    """
    if not mcc or not mnc:
        return {"ok": False, "error": "mcc and mnc are required"}

    if action == "add" and not s_nssai:
        return {"ok": False, "error": "s_nssai is required for action=add"}

    plmn_id = f"{mcc}{mnc}"
    base = _amf_sbi_url()

    if action == "add":
        payload = {"plmn_id": {"mcc": mcc, "mnc": mnc}, "s_nssai": s_nssai}
        data, status, err = _oam_request("POST", f"{base}/namf-oam/v1/plmns", payload)
    else:
        data, status, err = _oam_request("DELETE", f"{base}/namf-oam/v1/plmns/{plmn_id}")

    if data is None:
        return {"ok": False, "action": action, "plmn_id": plmn_id, "error": err}

    if status not in (200, 201):
        detail = data.get("description") or data.get("message") or str(data)
        return {"ok": False, "action": action, "plmn_id": plmn_id,
                "error": f"HTTP {status}: {detail}"}

    return {
        "ok": True,
        "action": action,
        "plmn_id": plmn_id,
        "message": data.get("message", "success"),
    }
