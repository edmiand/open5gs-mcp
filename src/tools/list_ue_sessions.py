"""list_ue_sessions — query live UE registrations and PDU sessions from AMF and SMF."""

from pathlib import Path

import httpx
import yaml

_CONFIG_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "open5gs" / "install" / "etc" / "open5gs"
)


# ── config helpers ─────────────────────────────────────────────────────────────

def _metrics_url(nf: str) -> str:
    """Read metrics address and port from the NF YAML config."""
    try:
        cfg_path = _CONFIG_DIR / f"{nf}.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        srv = cfg[nf]["metrics"]["server"][0]
        return f"http://{srv['address']}:{srv['port']}"
    except Exception:
        # Fallback to known defaults
        defaults = {"amf": "http://127.0.0.5:9090", "smf": "http://127.0.0.4:9090"}
        return defaults.get(nf, f"http://127.0.0.1:9090")


# ── data fetchers ──────────────────────────────────────────────────────────────

def _fetch(url: str, timeout: float = 3.0) -> tuple[dict | None, str]:
    """GET url, return (parsed_json, status_str)."""
    try:
        r = httpx.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json(), "ok"
    except httpx.ConnectError:
        return None, "unreachable"
    except httpx.TimeoutException:
        return None, "timeout"
    except Exception as exc:
        return None, f"error: {exc}"


def _fetch_all_pages(base_url: str, path: str, page_size: int = 100) -> tuple[list, str]:
    """Fetch all pages from a paginated endpoint, return (items, status)."""
    items: list = []
    page = 0
    while True:
        url = f"{base_url}{path}?page={page}&page_size={page_size}"
        data, status = _fetch(url)
        if data is None:
            return items, status
        batch = data.get("items", [])
        items.extend(batch)
        pager = data.get("pager", {})
        count = pager.get("count", len(batch))
        if len(items) >= count or len(batch) < page_size:
            break
        page += 1
    return items, "ok"


# ── normalisation ──────────────────────────────────────────────────────────────

def _imsi(supi: str) -> str:
    """Extract IMSI digits from 'imsi-<digits>' or return as-is."""
    return supi.removeprefix("imsi-") if supi else supi


def _merge_sessions(amf_ue: dict, smf_by_supi: dict) -> list[dict]:
    """
    Merge AMF PDU session stubs with SMF PDU session detail (keyed by PSI).
    AMF gives: psi, dnn, snssai, n1_released, n2_released, resource_status.
    SMF gives: psi, dnn, ipv4, ipv6, snssai, qos_flows, n3, pdu_state.
    """
    supi = amf_ue.get("supi", "")
    smf_ue = smf_by_supi.get(supi, {})
    smf_pdus: dict[int, dict] = {p["psi"]: p for p in smf_ue.get("pdu", []) if "psi" in p}

    merged: list[dict] = []
    for amf_sess in amf_ue.get("pdu_sessions", []):
        psi = amf_sess.get("psi")
        smf = smf_pdus.get(psi, {})

        # Determine state: prefer SMF which knows about N3/UP state
        state = smf.get("pdu_state")
        if not state:
            n1 = amf_sess.get("n1_released", False)
            n2 = amf_sess.get("n2_released", False)
            state = "released" if (n1 or n2) else "established"

        sess: dict = {
            "psi": psi,
            "dnn": amf_sess.get("dnn") or smf.get("dnn"),
            "snssai": amf_sess.get("snssai") or smf.get("snssai"),
            "ipv4": smf.get("ipv4"),
            "ipv6": smf.get("ipv6"),
            "state": state,
        }
        if smf.get("qos_flows"):
            sess["qos_flows"] = smf["qos_flows"]
        if smf.get("n3"):
            sess["n3"] = smf["n3"]
        merged.append(sess)

    # Include any SMF sessions not seen in AMF (edge case: AMF context stale)
    amf_psis = {s.get("psi") for s in amf_ue.get("pdu_sessions", [])}
    for psi, smf_s in smf_pdus.items():
        if psi not in amf_psis:
            merged.append({
                "psi": psi,
                "dnn": smf_s.get("dnn"),
                "snssai": smf_s.get("snssai"),
                "ipv4": smf_s.get("ipv4"),
                "ipv6": smf_s.get("ipv6"),
                "state": smf_s.get("pdu_state", "unknown"),
                "qos_flows": smf_s.get("qos_flows"),
                "n3": smf_s.get("n3"),
                "_source": "smf_only",
            })

    return merged


# ── main ───────────────────────────────────────────────────────────────────────

def list_ue_sessions(
    imsi_filter: str | None = None,
    include_idle: bool = True,
) -> dict:
    """
    List all active UE registrations and their PDU sessions.

    Queries the AMF for UE registration context and the SMF for PDU session
    detail (including assigned IP addresses), then joins the two views by SUPI.

    Args:
        imsi_filter: Optional IMSI or SUPI prefix to filter results.
                     Accepts digits or "imsi-<digits>" format.
        include_idle: If False, return only UEs with at least one active PDU
                      session. Default True (include all registered UEs).

    Returns:
        {
          "ok": bool,
          "timestamp": ISO-8601 UTC,
          "ue_count": int,
          "ues": [
            {
              "supi": "imsi-<digits>",
              "imsi": "<digits>",
              "cm_state": "connected" | "idle",
              "ue_activity": "active" | "idle" | "unknown",
              "pdu_sessions": [
                {
                  "psi": int,
                  "dnn": str,
                  "snssai": {"sst": int, "sd": str},
                  "ipv4": str | None,
                  "ipv6": str | None,
                  "state": "active" | "inactive" | "released" | "established" | "unknown",
                  "qos_flows": [{"qfi": int, "5qi": int}],
                  "n3": {"gnb": {...}, "upf": {...}}   # optional
                }
              ],
              "pdu_session_count": int,
              "allowed_slices": [{"sst": int, "sd": str}],
              "location": {"nr_tai": {...}, "nr_cgi": {...}},
              "ambr_bps": {"downlink": int, "uplink": int}
            }
          ],
          "sources": {"amf": "ok"|"unreachable"|"timeout"|"error", "smf": str}
        }
    """
    from datetime import datetime, timezone

    amf_base = _metrics_url("amf")
    smf_base = _metrics_url("smf")

    # Fetch from both NFs concurrently (sequential is fine — timeouts are short)
    amf_items, amf_status = _fetch_all_pages(amf_base, "/ue-info")
    smf_items, smf_status = _fetch_all_pages(smf_base, "/pdu-info")

    # Index SMF sessions by SUPI for O(1) join
    smf_by_supi: dict[str, dict] = {u["supi"]: u for u in smf_items if "supi" in u}

    # Normalise filter
    filter_norm: str | None = None
    if imsi_filter:
        filter_norm = imsi_filter.strip().lower().removeprefix("imsi-")

    ues: list[dict] = []
    for amf_ue in amf_items:
        supi = amf_ue.get("supi", "")
        imsi = _imsi(supi)

        # Apply IMSI filter
        if filter_norm and not imsi.startswith(filter_norm):
            continue

        pdu_sessions = _merge_sessions(amf_ue, smf_by_supi)

        # Apply idle filter
        smf_ue = smf_by_supi.get(supi, {})
        ue_activity = smf_ue.get("ue_activity", "unknown")
        if not include_idle and ue_activity != "active":
            if not any(s["state"] == "active" for s in pdu_sessions):
                continue

        ues.append({
            "supi": supi,
            "imsi": imsi,
            "cm_state": amf_ue.get("cm_state", "unknown"),
            "ue_activity": ue_activity,
            "pdu_sessions": pdu_sessions,
            "pdu_session_count": len(pdu_sessions),
            "allowed_slices": amf_ue.get("allowed_slices", []),
            "location": amf_ue.get("location"),
            "ambr_bps": amf_ue.get("ambr"),
        })

    return {
        "ok": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ue_count": len(ues),
        "ues": ues,
        "sources": {"amf": amf_status, "smf": smf_status},
    }
