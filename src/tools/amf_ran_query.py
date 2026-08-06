"""amf_ran_query — query live RAN state from AMF OAM API and metrics endpoint."""

import json
import subprocess
from typing import Literal, TypedDict

import httpx
import yaml

from tools._nf_util import CONFIG_DIR as _CONFIG_DIR, metrics_url as _metrics_url
from tools._schema_util import ErrorDetail


# ── structured output schema ─────────────────────────────────────────────────

class GnbEntry(TypedDict):
    gnb_id: str | None
    plmn: dict | None
    sctp_peer: str | None
    supported_ta_list: list[dict]
    num_connected_ues: int


class AmfRanDetail(TypedDict):
    ok: Literal[True]
    connected_gnbs: int
    registered_ues: int
    total_plmns: int
    plmns: list[dict]
    gnbs: list[GnbEntry]
    gnbs_status: Literal["ok", "unreachable", "timeout", "error"]


class AmfRanResult(TypedDict):
    summary: str
    detail: AmfRanDetail | ErrorDetail


def _amf_sbi_url() -> str:
    """Read AMF SBI address and port from amf.yaml (SBI, not metrics)."""
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


def _fetch_gnb_info(base_url: str) -> tuple[list, str]:
    """Fetch all pages from /gnb-info. Returns (items, status)."""
    items, page = [], 0
    try:
        while True:
            r = httpx.get(f"{base_url}/gnb-info?page={page}&page_size=100", timeout=3.0)
            data = r.json()
            batch = data.get("items", [])
            items.extend(batch)
            pager = data.get("pager", {})
            if len(items) >= pager.get("count", len(items)):
                break
            page += 1
        return items, "ok"
    except httpx.ConnectError:
        return [], "unreachable"
    except httpx.TimeoutException:
        return [], "timeout"
    except Exception as exc:
        return [], f"error: {exc}"


def amf_ran_query() -> AmfRanResult:
    """
    Query live RAN state from the AMF OAM API and metrics endpoint.

    Calls /namf-oam/v1/plmns for PLMN/slice config and aggregate counts,
    then /gnb-info for per-gNB detail (SCTP peer, TA list, slices, UE count).

    Returns ok, connected_gnbs, registered_ues, total_plmns, plmns list,
    gnbs list (each entry: gnb_id, plmn, sctp_peer, supported_ta_list,
    num_connected_ues), and gnbs_status ("ok"|"unreachable"|"timeout"|"error").
    """
    base = _amf_sbi_url()
    data, err = _oam_get(f"{base}/namf-oam/v1/plmns")
    if data is None:
        return {"summary": f"Error: {err}", "detail": {"ok": False, "error": err}}

    gnb_items, gnbs_status = _fetch_gnb_info(_metrics_url("amf"))

    gnbs = [
        {
            "gnb_id":            g.get("gnb_id"),
            "plmn":              g.get("plmn"),
            "sctp_peer":         g.get("ng", {}).get("sctp", {}).get("peer"),
            "supported_ta_list": g.get("supported_ta_list", []),
            "num_connected_ues": g.get("num_connected_ues", 0),
        }
        for g in gnb_items
    ]

    _connected = data.get("connected_gnbs", 0)
    _registered = data.get("registered_ues", 0)
    _plmns = data.get("total_plmns", 0)
    _summary = (f"AMF reports {_connected} connected gNB(s) and {_registered} "
                f"registered UE(s) across {_plmns} PLMN(s).")
    return {
        "summary": _summary,
        "detail": {
            "ok":             True,
            "connected_gnbs": _connected,
            "registered_ues": _registered,
            "total_plmns":    _plmns,
            "plmns":          data.get("plmns", []),
            "gnbs":           gnbs,
            "gnbs_status":    gnbs_status,
        },
    }
