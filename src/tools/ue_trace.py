"""ue_trace — collect full e2e trace for a UE across all Open5GS NFs."""

import re
from datetime import datetime, timedelta, timezone

from tools._log_util import _ANSI_RE, _parse_line
from tools._nf_util import LOG_DIR as _LOG_DIR
from tools._subscriber_util import normalize_supi as _normalize_supi_fn

# NRF excluded — its logs contain NF-lifecycle events, not per-UE signaling (Fix 3)
_TRACE_NFS = ["amf", "ausf", "udm", "udr", "smf", "pcf", "upf"]

# 10 MB: AMF/SMF run at debug level and generate 5-15x more volume than info (Fix 4)
_TAIL_BYTES = 10 * 1024 * 1024
_MAX_EVENTS = 200
_MSG_MAX = 120

_SEID_RE = re.compile(r"seid[:\s]+(?:0x)?([0-9a-fA-F]+)", re.I)
_UE_IP_RE = re.compile(
    r"\b(10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)\b"
)
_DNN_RE = re.compile(r"dnn[:\s]+['\"]?(\w+)['\"]?", re.I)
# Fix 1: handle bracket notation used in AMF debug logs: RAN_UE_NGAP_ID[42]
_NGAP_ID_RE = re.compile(r"(?:amf_ue_ngap_id|ran_ue_ngap_id)[\[:\s]+(\d+)", re.I)
_PDU_SESSION_ID_RE = re.compile(r"pdu[_\s]?session[_\s]?id[:\s]+(\d+)", re.I)
_CAUSE_RE = re.compile(r"(?:5gmm|gmm)[_\s]?cause[_\s]?(?:code|value)?[:\s]+(\w+)", re.I)

# Optional SUPI prefix present on some AMF debug lines (Fix 5)
_SUPI_OPT = r"(?:\[imsi-\d+\]\s+)?"

# (compiled pattern, message_type, from_entity, to_entity)
# None from/to → filled from NF context at call time
_MSG_RULES: list[tuple[re.Pattern, str, str | None, str | None]] = [
    # Deregistration rules must come before Registration rules — "Deregistration"
    # contains "Registration" as a substring, so the order prevents false matches.
    (re.compile(r"De.?registration Request.*AMF", re.I),      "Deregistration Request",            "AMF",  "UE"),
    (re.compile(r"De.?registration Request", re.I),           "Deregistration Request",            "UE",   "AMF"),
    (re.compile(r"De.?registration Accept", re.I),            "Deregistration Accept",             "AMF",  "UE"),
    (re.compile(r"Registration Request", re.I),               "Registration Request",              "UE",   "AMF"),
    (re.compile(r"Registration Accept", re.I),                "Registration Accept",               "AMF",  "UE"),
    (re.compile(r"Registration Complete", re.I),              "Registration Complete",             "UE",   "AMF"),
    (re.compile(r"Registration Reject", re.I),                "Registration Reject",               "AMF",  "UE"),
    (re.compile(r"\bAuthentication Request", re.I),           "Authentication Request",            "AMF",  "UE"),
    (re.compile(r"\bAuthentication Response", re.I),          "Authentication Response",           "UE",   "AMF"),
    (re.compile(r"\bAuthentication Failure", re.I),           "Authentication Failure",            "UE",   "AMF"),
    (re.compile(r"Security Mode Command", re.I),              "Security Mode Command",             "AMF",  "UE"),
    (re.compile(r"Security Mode Complete", re.I),             "Security Mode Complete",            "UE",   "AMF"),
    (re.compile(r"Security Mode Reject", re.I),               "Security Mode Reject",              "UE",   "AMF"),
    # Fix 5: debug-level NAS events with optional SUPI prefix
    (re.compile(_SUPI_OPT + r"Configuration update complete", re.I), "Configuration Update Complete", "UE", "AMF"),
    (re.compile(r"PDU Session Establishment Request", re.I),  "PDU Session Establishment Request", "UE",   "AMF"),
    (re.compile(r"PDU Session Establishment Accept", re.I),   "PDU Session Establishment Accept",  "AMF",  "UE"),
    (re.compile(r"PDU Session Establishment Reject", re.I),   "PDU Session Establishment Reject",  "AMF",  "UE"),
    (re.compile(r"PDU Session (Release|Deletion)", re.I),     "PDU Session Release",               None,   None),
    (re.compile(r"PDU Session Modification", re.I),           "PDU Session Modification",          None,   None),
    # Fix 5: N2 PDU resource messages
    (re.compile(r"PDUSessionResourceSetupRequest", re.I),     "PDUSessionResourceSetupRequest",    "AMF",  "gNB"),
    (re.compile(r"PDUSessionResourceSetupResponse", re.I),    "PDUSessionResourceSetupResponse",   "gNB",  "AMF"),
    (re.compile(r"Nausf[_-]?UEAuthentication", re.I),        "Nausf-UEAuthentication",            "AMF",  "AUSF"),
    (re.compile(r"Nudm[_-]?UECM", re.I),                     "Nudm-UECM",                         None,   "UDM"),
    (re.compile(r"Nudm[_-]?SDM", re.I),                      "Nudm-SDM",                          None,   "UDM"),
    (re.compile(r"Nudm[_-]?Authentication", re.I),            "Nudm-UEAuthentication",             "AUSF", "UDM"),
    (re.compile(r"Nsmf[_-]?PDUSession", re.I),               "Nsmf-PDUSession",                   "AMF",  "SMF"),
    (re.compile(r"Npcf[_-]?AMPolicy", re.I),                 "Npcf-AMPolicyControl",              "AMF",  "PCF"),
    (re.compile(r"Npcf[_-]?SMPolicy", re.I),                 "Npcf-SMPolicyControl",              "SMF",  "PCF"),
    (re.compile(r"PFCP Session Establishment", re.I),         "PFCP Session Establishment",        "SMF",  "UPF"),
    (re.compile(r"PFCP Session Modification", re.I),          "PFCP Session Modification",         "SMF",  "UPF"),
    (re.compile(r"PFCP Session Deletion", re.I),              "PFCP Session Deletion",             "SMF",  "UPF"),
    (re.compile(r"PFCP Heartbeat", re.I),                     "PFCP Heartbeat",                    "SMF",  "UPF"),
    (re.compile(r"NRF Registration", re.I),                   "NRF Registration",                  None,   "NRF"),
    # Fix 5: AMF debug-level N2/NAS transport and context messages
    (re.compile(r"NGSetupRequest", re.I),                     "NGSetupRequest",                    "gNB",  "AMF"),
    (re.compile(r"UplinkNASTransport", re.I),                 "UplinkNASTransport",                "UE",   "AMF"),
    (re.compile(r"DownlinkNASTransport", re.I),               "DownlinkNASTransport",              "AMF",  "UE"),
    # Fix 5: camelCase NGAP context setup — placed before the space-separated fallback
    (re.compile(r"InitialContextSetupRequest", re.I),         "InitialContextSetupRequest",        "AMF",  "gNB"),
    (re.compile(r"InitialContextSetupResponse", re.I),        "InitialContextSetupResponse",       "gNB",  "AMF"),
    (re.compile(r"Initial UE Message", re.I),                 "Initial UE Message",                "gNB",  "AMF"),
    (re.compile(r"Initial Context Setup", re.I),              "Initial Context Setup",             "AMF",  "gNB"),
    # Fix 5: camelCase UE context release variants before the generic fallback
    (re.compile(r"UEContextReleaseRequest", re.I),            "UEContextReleaseRequest",           "gNB",  "AMF"),
    (re.compile(r"UEContextReleaseCommand", re.I),            "UEContextReleaseCommand",           "AMF",  "gNB"),
    (re.compile(r"UEContextReleaseComplete", re.I),           "UEContextReleaseComplete",          "gNB",  "AMF"),
    (re.compile(r"UE Context Release", re.I),                 "UE Context Release",                None,   None),
    (re.compile(r"5GMM.*[Cc]ause|[Cc]ause.*5GMM", re.I),     "5GMM Cause",                        None,   None),
]

_NF_ENTITY = {
    "amf": "AMF", "ausf": "AUSF", "udm": "UDM", "udr": "UDR",
    "smf": "SMF", "upf": "UPF",  "pcf": "PCF", "nrf": "NRF",
    "nssf": "NSSF", "scp": "SCP",
}

_DEFAULT_PARTICIPANTS = ["UE", "gNB", "AMF", "AUSF", "UDM", "UDR", "SMF", "UPF", "PCF"]


# ── low-level helpers ──────────────────────────────────────────────────────────

def _read_log_tail(nf: str) -> tuple[str | None, str | None]:
    """Read the tail of an NF log file. Returns (text, error_msg)."""
    logfile = _LOG_DIR / f"{nf}.log"
    if not logfile.exists():
        return None, "log file not found"
    try:
        with open(logfile, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            raw = fh.read()
        return raw.decode("utf-8", errors="replace"), None
    except PermissionError:
        return None, "permission denied"
    except OSError as exc:
        return None, str(exc)


def _infer_event(nf: str, message: str) -> tuple[str, str, str, str]:
    """Return (direction, from_entity, to_entity, message_type) from NF + message."""
    entity = _NF_ENTITY.get(nf, nf.upper())
    for pattern, msg_type, from_e, to_e in _MSG_RULES:
        if pattern.search(message):
            if from_e is None and to_e is None:
                from_e, to_e = entity, entity
            elif from_e is None:
                from_e = entity
            elif to_e is None:
                to_e = entity
            if from_e == to_e:
                direction = "internal"
            elif from_e in ("UE", "gNB"):
                direction = "inbound"
            else:
                direction = "outbound"
            return direction, from_e, to_e, msg_type
    return "internal", entity, entity, message[:80]


# ── log search functions ────────────────────────────────────────────────────────

def _search_amf_log(bare_imsi: str, time_window_minutes: int) -> dict:
    """Search AMF log for SUPI lines. Returns first/last timestamps and IDs."""
    result: dict = {
        "lines": [],
        "first_ts": None,
        "last_ts": None,
        "ngap_ids": set(),
        "pdu_session_ids": set(),
        "cause_codes": [],
        "tail_start_ts": None,
        "error": None,
    }

    text, err = _read_log_tail("amf")
    if err:
        result["error"] = err
        return result
    if not text:
        result["error"] = "empty log"
        return result

    year = datetime.now().year
    cutoff_ts = datetime.now(timezone.utc) - timedelta(minutes=time_window_minutes)

    lines = text.splitlines()
    seen_raws: set[str] = set()

    # Find the earliest timestamp in the tail for log-coverage warnings (Fix 4)
    for raw_line in lines:
        rec = _parse_line(raw_line, year)
        if rec is not None:
            result["tail_start_ts"] = rec["ts"]
            break

    for raw_line in lines:
        if bare_imsi not in raw_line:
            continue
        rec = _parse_line(raw_line, year)
        if rec is None:
            continue
        if rec["ts"] < cutoff_ts:
            continue

        # Open5GS writes each line twice (ANSI + plain); deduplicate by stripped text
        raw_clean = _ANSI_RE.sub("", raw_line).rstrip()
        if raw_clean in seen_raws:
            continue
        seen_raws.add(raw_clean)

        result["lines"].append({
            "ts": rec["ts"],
            "ts_str": rec["ts_str"],
            "level": rec["level"],
            "message": rec["message"],
            "raw": raw_clean,
        })

        if result["first_ts"] is None or rec["ts"] < result["first_ts"]:
            result["first_ts"] = rec["ts"]
        if result["last_ts"] is None or rec["ts"] > result["last_ts"]:
            result["last_ts"] = rec["ts"]

        msg = rec["message"]
        for m in _NGAP_ID_RE.finditer(msg):
            result["ngap_ids"].add(m.group(1))
        for m in _PDU_SESSION_ID_RE.finditer(msg):
            result["pdu_session_ids"].add(m.group(1))
        for m in _CAUSE_RE.finditer(msg):
            result["cause_codes"].append(m.group(1))

    result["ngap_ids"] = sorted(result["ngap_ids"])
    result["pdu_session_ids"] = sorted(result["pdu_session_ids"])
    return result


def _search_amf_pre_auth(
    ngap_ids: list[str],
    before_ts: datetime,
    lookback_seconds: int = 30,
) -> list[dict]:
    """Return AMF log lines in [before_ts - lookback, before_ts) that belong to this UE.

    Matches lines containing any of the extracted NGAP IDs (amf_ue_ngap_id /
    ran_ue_ngap_id). Must only be called when ngap_ids is non-empty — pattern-only
    matching is intentionally omitted because it cannot discriminate between UEs
    and would contaminate the trace with events from concurrent registrations.
    """
    if not ngap_ids:
        return []

    text, err = _read_log_tail("amf")
    if err or not text:
        return []

    ngap_val_alt = "|".join(re.escape(id_) for id_ in ngap_ids)
    ngap_pattern = re.compile(
        r"(?:amf_ue_ngap_id|ran_ue_ngap_id)[\[:\s]+(?:" + ngap_val_alt + r")\b",
        re.I,
    )

    year = datetime.now().year
    window_start = before_ts - timedelta(seconds=lookback_seconds)
    results: list[dict] = []
    seen_raws: set[str] = set()

    for raw_line in text.splitlines():
        rec = _parse_line(raw_line, year)
        if rec is None:
            continue
        if not (window_start <= rec["ts"] < before_ts):
            continue
        if not ngap_pattern.search(raw_line):
            continue

        raw_clean = _ANSI_RE.sub("", raw_line).rstrip()
        if raw_clean in seen_raws:
            continue
        seen_raws.add(raw_clean)
        results.append({
            "ts": rec["ts"],
            "ts_str": rec["ts_str"],
            "level": rec["level"],
            "message": rec["message"],
            "raw": raw_clean,
        })

    return results


def _search_nf_by_time(
    nf: str,
    start_ts: datetime,
    end_ts: datetime,
    bare_imsi: str | None = None,
    seids: list[str] | None = None,
) -> dict:
    """Search an NF log by time window; also match bare IMSI or PFCP SEIDs."""
    result: dict = {
        "lines": [],
        "seids": set(),
        "ue_ips": set(),
        "dnns": set(),
        "tail_start_ts": None,
        "error": None,
    }

    text, err = _read_log_tail(nf)
    if err:
        result["error"] = err
        return result
    if not text:
        return result

    year = datetime.now().year
    seen_raws: set[str] = set()

    for raw_line in text.splitlines():
        rec = _parse_line(raw_line, year)
        if rec is None:
            continue

        if result["tail_start_ts"] is None:
            result["tail_start_ts"] = rec["ts"]

        in_window = start_ts <= rec["ts"] <= end_ts
        has_imsi = bool(bare_imsi and bare_imsi in raw_line)
        has_seid = bool(seids and any(s in raw_line for s in seids))

        if not (in_window or has_imsi or has_seid):
            continue

        raw_clean = _ANSI_RE.sub("", raw_line).rstrip()
        if raw_clean in seen_raws:
            continue
        seen_raws.add(raw_clean)

        result["lines"].append({
            "ts": rec["ts"],
            "ts_str": rec["ts_str"],
            "level": rec["level"],
            "message": rec["message"],
            "raw": raw_clean,
        })

        if nf == "smf":
            msg = rec["message"]
            for m in _SEID_RE.finditer(msg):
                result["seids"].add(m.group(1))
            for m in _UE_IP_RE.finditer(msg):
                result["ue_ips"].add(m.group(1))
            for m in _DNN_RE.finditer(msg):
                result["dnns"].add(m.group(1))

    result["seids"] = sorted(result["seids"])
    result["ue_ips"] = sorted(result["ue_ips"])
    result["dnns"] = sorted(result["dnns"])
    return result


# ── main tool ─────────────────────────────────────────────────────────────────

def get_ue_trace(
    supi: str,
    time_window_minutes: int = 60,
    include_nfs: list[str] | None = None,
    window_padding_seconds: int = 5,
    include_nrf: bool = False,
) -> dict:
    """Collect full e2e trace for a UE across Open5GS NFs.

    Args:
        supi:                   IMSI/SUPI string (various formats accepted).
        time_window_minutes:    How far back to search in AMF log (default 60).
        include_nfs:            NFs to search; defaults to all trace NFs.
        window_padding_seconds: Seconds added to both ends of the derived search
                                window (default 5; increase for loaded systems).
        include_nrf:            If True, also search NRF and include NF-lifecycle
                                events marked direction="internal".

    Returns structured trace data suitable for Mermaid sequence diagram generation.
    """
    # ── input validation ──────────────────────────────────────────────────────
    try:
        full_supi, bare_imsi = _normalize_supi_fn(supi)
    except ValueError as exc:
        return {"summary": f"Error: {exc}", "detail": {"ok": False, "error": str(exc)}}

    if not (1 <= time_window_minutes <= 1440):
        return {"summary": "Error: time_window_minutes must be between 1 and 1440.",
                "detail": {"ok": False, "error": "time_window_minutes must be between 1 and 1440"}}

    if not (0 <= window_padding_seconds <= 60):
        return {"summary": "Error: window_padding_seconds must be between 0 and 60.",
                "detail": {"ok": False, "error": "window_padding_seconds must be between 0 and 60"}}

    _valid_nfs = _TRACE_NFS + ["nrf"]
    if include_nfs is None:
        nfs_to_search = list(_TRACE_NFS)
    else:
        nfs_to_search = [n.lower() for n in include_nfs]
        invalid = [n for n in nfs_to_search if n not in _valid_nfs]
        if invalid:
            _e = f"Unknown NF(s): {invalid}. Valid: {_valid_nfs}"
            return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

    if include_nrf and "nrf" not in nfs_to_search:
        nfs_to_search = nfs_to_search + ["nrf"]

    # ── Step 1: anchor on AMF ─────────────────────────────────────────────────
    nf_errors: dict[str, str] = {}
    amf_result: dict = {
        "lines": [], "first_ts": None, "last_ts": None,
        "ngap_ids": [], "pdu_session_ids": [], "cause_codes": [],
        "tail_start_ts": None, "error": None,
    }

    if "amf" in nfs_to_search:
        amf_result = _search_amf_log(bare_imsi, time_window_minutes)
        if amf_result["error"]:
            nf_errors["amf"] = amf_result["error"]

    # ── Step 1b: collect pre-auth AMF events via NGAP IDs ────────────────────
    if "amf" in nfs_to_search and amf_result.get("first_ts") and amf_result.get("ngap_ids"):
        pre_auth = _search_amf_pre_auth(amf_result["ngap_ids"], amf_result["first_ts"])
        if pre_auth:
            existing_raws = {ln["raw"] for ln in amf_result["lines"]}
            for ln in pre_auth:
                if ln["raw"] not in existing_raws:
                    amf_result["lines"].append(ln)
            pre_first = min(ln["ts"] for ln in pre_auth)
            if pre_first < amf_result["first_ts"]:
                amf_result["first_ts"] = pre_first

    # ── Step 2: derive search window (Fix 2: 5s padding, configurable) ───────
    if amf_result["first_ts"] and amf_result["last_ts"]:
        search_start = amf_result["first_ts"] - timedelta(seconds=window_padding_seconds)
        search_end = amf_result["last_ts"] + timedelta(seconds=window_padding_seconds)
    else:
        search_end = datetime.now(timezone.utc)
        search_start = search_end - timedelta(minutes=time_window_minutes)

    # Warn if AMF log tail doesn't cover the full window (Fix 4)
    if "amf" in nfs_to_search:
        amf_tail_start = amf_result.get("tail_start_ts")
        if amf_tail_start and amf_tail_start > search_start:
            warn = (
                f"log tail starts at {amf_tail_start.strftime('%m/%d %H:%M:%S')}, "
                f"which is after the requested window start "
                f"({search_start.strftime('%m/%d %H:%M:%S')}) "
                f"— trace may be incomplete; consider increasing _TAIL_BYTES"
            )
            nf_errors["amf"] = (nf_errors.get("amf", "") + "; " + warn).lstrip("; ")

    # ── Step 3: search remaining NFs ──────────────────────────────────────────
    nf_data: dict[str, dict] = {}
    if "amf" in nfs_to_search:
        nf_data["amf"] = amf_result

    smf_seids: list[str] = []
    non_amf_non_upf = [n for n in nfs_to_search if n not in ("amf", "upf")]

    for nf in non_amf_non_upf:
        data = _search_nf_by_time(nf, search_start, search_end, bare_imsi)
        nf_data[nf] = data
        if data["error"]:
            nf_errors[nf] = data["error"]
        tail_start = data.get("tail_start_ts")
        if tail_start and tail_start > search_start:
            warn = (
                f"log tail starts at {tail_start.strftime('%m/%d %H:%M:%S')}, "
                f"which is after the requested window start "
                f"— trace may be incomplete; consider increasing _TAIL_BYTES"
            )
            nf_errors[nf] = (nf_errors.get(nf, "") + "; " + warn).lstrip("; ")
        if nf == "smf":
            smf_seids = data.get("seids", [])

    if "upf" in nfs_to_search:
        if smf_seids:
            upf_data = _search_nf_by_time("upf", search_start, search_end, seids=smf_seids)
        else:
            # No PFCP SEIDs available — searching UPF without them returns all
            # traffic in the window, unrelated to this UE.
            upf_data = {
                "lines": [], "seids": [], "ue_ips": [], "dnns": [],
                "tail_start_ts": None, "error": None,
            }
        nf_data["upf"] = upf_data
        if upf_data["error"]:
            nf_errors["upf"] = upf_data["error"]
        tail_start = upf_data.get("tail_start_ts")
        if tail_start and tail_start > search_start:
            warn = (
                f"log tail starts at {tail_start.strftime('%m/%d %H:%M:%S')}, "
                f"which is after the requested window start "
                f"— trace may be incomplete; consider increasing _TAIL_BYTES"
            )
            nf_errors["upf"] = (nf_errors.get("upf", "") + "; " + warn).lstrip("; ")

    # ── Step 4: build structured events ───────────────────────────────────────
    all_events: list[dict] = []

    for nf, data in nf_data.items():
        for line_data in data.get("lines", []):
            direction, from_e, to_e, msg_type = _infer_event(nf, line_data["message"])
            msg = line_data["message"]
            event: dict = {
                "_sort_ts": line_data["ts"],
                "timestamp": line_data["ts_str"],
                "nf": nf,
                "level": line_data["level"],
                "direction": direction,
                "message_type": msg_type,
                "from": from_e,
                "to": to_e,
                "message": msg if len(msg) <= _MSG_MAX else msg[:_MSG_MAX] + "…",
            }
            if nf == "nrf":
                event["direction"] = "internal"
                event["note"] = "NF-lifecycle, not UE-signaling"
            all_events.append(event)

    all_events.sort(key=lambda e: e["_sort_ts"])

    # ── Step 5: build summary and output ──────────────────────────────────────
    ue_ips = list(nf_data.get("smf", {}).get("ue_ips", []))
    ue_ip_assigned = ue_ips[0] if ue_ips else None

    registration_success = any(
        e["message_type"] == "Registration Accept" for e in all_events
    )
    pdu_session_success = (
        any("PDU Session Establishment Accept" in e["message_type"] for e in all_events)
        or ue_ip_assigned is not None
    )

    error_lines = [
        e["message"] for e in all_events
        if e["level"] in ("ERROR", "CRIT", "FATAL", "WARNING", "WARN")
    ]

    total_events = len(all_events)
    if total_events > _MAX_EVENTS:
        keep = _MAX_EVENTS // 2
        all_events = all_events[:keep] + all_events[total_events - keep:]

    for e in all_events:
        del e["_sort_ts"]

    seen: list[str] = []
    for e in all_events:
        for p in (e["from"], e["to"]):
            if p and p not in seen:
                seen.append(p)
    active_participants = [p for p in _DEFAULT_PARTICIPANTS if p in seen]
    mermaid_hint = "sequenceDiagram\n" + "\n".join(
        f"    participant {p}" for p in active_participants
    )

    _reg = "succeeded" if registration_success else "not seen"
    _pdu = "established" if pdu_session_success else "not established"
    _summary_str = (
        f"Trace for {full_supi}: {total_events} event(s) found, "
        f"registration {_reg}, PDU session {_pdu}."
    )

    detail: dict = {
        "ok": True,
        "supi": full_supi,
        "time_range": {
            "start": search_start.isoformat(),
            "end": search_end.isoformat(),
        },
        "summary": {
            "registration_success": registration_success,
            "pdu_session_success": pdu_session_success,
            "ue_ip_assigned": ue_ip_assigned,
            "errors": error_lines[:20],
            "total_events": total_events,
        },
        "events": all_events,
        "mermaid_hint": mermaid_hint,
    }
    if total_events > _MAX_EVENTS:
        detail["events_truncated"] = (
            f"Showing first {_MAX_EVENTS // 2} and last {_MAX_EVENTS // 2} "
            f"of {total_events} events"
        )

    if nf_errors:
        detail["nf_errors"] = nf_errors

    return {"summary": _summary_str, "detail": detail}
