"""ue_trace — collect full e2e trace for a UE across all Open5GS NFs."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.nf_lifecycle import _SCRIPT

_LOG_DIR = _SCRIPT.parent / "install" / "var" / "log" / "open5gs"

_TRACE_NFS = ["amf", "ausf", "udm", "udr", "smf", "pcf", "nrf", "upf"]

_LINE_RE = re.compile(
    r"^(?:\x1b\[[0-9;]*m)?"
    r"(\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d+)"
    r":\s+\[(\w+)\]"
    r"\s+(\w+)"
    r":\s+(.+?)$"
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SOURCE_RE = re.compile(r"\(([^)]+:\d+)\)$")

_TAIL_BYTES = 2 * 1024 * 1024  # 2 MB

_SEID_RE = re.compile(r"seid[:\s]+(?:0x)?([0-9a-fA-F]+)", re.I)
_UE_IP_RE = re.compile(
    r"\b(10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)\b"
)
_DNN_RE = re.compile(r"dnn[:\s]+['\"]?(\w+)['\"]?", re.I)
_NGAP_ID_RE = re.compile(r"(?:amf_ue_ngap_id|ran_ue_ngap_id)[:\s]+(\d+)", re.I)
_PDU_SESSION_ID_RE = re.compile(r"pdu[_\s]?session[_\s]?id[:\s]+(\d+)", re.I)
_CAUSE_RE = re.compile(r"(?:5gmm|gmm)[_\s]?cause[_\s]?(?:code|value)?[:\s]+(\w+)", re.I)

# (compiled pattern, message_type, from_entity, to_entity)
# None from/to → will be filled from the NF context at call time
_MSG_RULES: list[tuple[re.Pattern, str, str | None, str | None]] = [
    (re.compile(r"Registration Request", re.I),               "Registration Request",              "UE",   "AMF"),
    (re.compile(r"Registration Accept", re.I),                "Registration Accept",               "AMF",  "UE"),
    (re.compile(r"Registration Complete", re.I),              "Registration Complete",             "UE",   "AMF"),
    (re.compile(r"Registration Reject", re.I),                "Registration Reject",               "AMF",  "UE"),
    (re.compile(r"Deregistration Request.*UE", re.I),         "Deregistration Request",            "UE",   "AMF"),
    (re.compile(r"Deregistration Request.*AMF", re.I),        "Deregistration Request",            "AMF",  "UE"),
    (re.compile(r"\bAuthentication Request", re.I),            "Authentication Request",            "AMF",  "UE"),
    (re.compile(r"\bAuthentication Response", re.I),          "Authentication Response",           "UE",   "AMF"),
    (re.compile(r"\bAuthentication Failure", re.I),           "Authentication Failure",            "UE",   "AMF"),
    (re.compile(r"Security Mode Command", re.I),              "Security Mode Command",             "AMF",  "UE"),
    (re.compile(r"Security Mode Complete", re.I),             "Security Mode Complete",            "UE",   "AMF"),
    (re.compile(r"Security Mode Reject", re.I),               "Security Mode Reject",              "UE",   "AMF"),
    (re.compile(r"PDU Session Establishment Request", re.I),  "PDU Session Establishment Request", "UE",   "AMF"),
    (re.compile(r"PDU Session Establishment Accept", re.I),   "PDU Session Establishment Accept",  "AMF",  "UE"),
    (re.compile(r"PDU Session Establishment Reject", re.I),   "PDU Session Establishment Reject",  "AMF",  "UE"),
    (re.compile(r"PDU Session (Release|Deletion)", re.I),     "PDU Session Release",               None,   None),
    (re.compile(r"PDU Session Modification", re.I),           "PDU Session Modification",          None,   None),
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
    (re.compile(r"Initial UE Message", re.I),                 "Initial UE Message",                "gNB",  "AMF"),
    (re.compile(r"Initial Context Setup", re.I),              "Initial Context Setup",             "AMF",  "gNB"),
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

def _normalize_supi(supi: str) -> tuple[str, str]:
    """Return (full_supi, bare_imsi_digits).

    Accepts: "imsi-999700000000001", "999700000000001", "IMSI:999700000000001"
    """
    s = supi.strip()
    if re.match(r"(?i)imsi[-:]", s):
        digits = re.sub(r"(?i)^imsi[-:]", "", s).strip()
    else:
        digits = s

    if not re.match(r"^\d{10,15}$", digits):
        raise ValueError(f"Invalid SUPI/IMSI '{supi}': expected 10-15 digits after prefix")

    return f"imsi-{digits}", digits


def _parse_ts_str(ts_str: str, year: int) -> datetime | None:
    """Parse 'MM/DD HH:MM:SS.mmm' into UTC datetime."""
    try:
        dt = datetime.strptime(f"{year}/{ts_str}", "%Y/%m/%d %H:%M:%S.%f")
    except ValueError:
        try:
            dt = datetime.strptime(f"{year}/{ts_str}", "%Y/%m/%d %H:%M:%S")
        except ValueError:
            return None
    now = datetime.now()
    if dt.month > now.month and (dt.month - now.month) > 6:
        dt = dt.replace(year=year - 1)
    return dt.replace(tzinfo=timezone.utc)


def _parse_line(raw: str, year: int) -> dict | None:
    clean = _ANSI_RE.sub("", raw).rstrip()
    m = _LINE_RE.match(clean)
    if not m:
        return None
    ts_str, component, level, message = m.group(1), m.group(2), m.group(3), m.group(4)
    ts = _parse_ts_str(ts_str, year)
    if ts is None:
        return None
    src_m = _SOURCE_RE.search(message)
    source = src_m.group(1) if src_m else None
    if source:
        message = message[: src_m.start()].rstrip()
    return {
        "ts": ts,
        "ts_str": ts_str,
        "component": component,
        "level": level.upper(),
        "message": message.strip(),
        "source": source,
    }


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

    for raw_line in text.splitlines():
        if bare_imsi not in raw_line:
            continue
        rec = _parse_line(raw_line, year)
        if rec is None:
            continue
        if rec["ts"] < cutoff_ts:
            continue

        result["lines"].append({
            "ts": rec["ts"],
            "ts_str": rec["ts_str"],
            "level": rec["level"],
            "message": rec["message"],
            "raw": _ANSI_RE.sub("", raw_line).rstrip(),
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
) -> dict:
    """Collect full e2e trace for a UE across Open5GS NFs.

    Args:
        supi:                 IMSI/SUPI string (various formats accepted).
        time_window_minutes:  How far back to search in AMF log (default 60).
        include_nfs:          NFs to search; defaults to all trace NFs.

    Returns structured trace data suitable for Mermaid sequence diagram generation.
    """
    # ── input validation ──────────────────────────────────────────────────────
    try:
        full_supi, bare_imsi = _normalize_supi(supi)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if not (1 <= time_window_minutes <= 1440):
        return {"ok": False, "error": "time_window_minutes must be between 1 and 1440"}

    if include_nfs is None:
        nfs_to_search = list(_TRACE_NFS)
    else:
        nfs_to_search = [n.lower() for n in include_nfs]
        invalid = [n for n in nfs_to_search if n not in _TRACE_NFS]
        if invalid:
            return {"ok": False, "error": f"Unknown NF(s): {invalid}. Valid: {_TRACE_NFS}"}

    # ── Step 1: anchor on AMF ─────────────────────────────────────────────────
    nf_errors: dict[str, str] = {}
    amf_result: dict = {"lines": [], "first_ts": None, "last_ts": None,
                        "ngap_ids": [], "pdu_session_ids": [], "cause_codes": [], "error": None}

    if "amf" in nfs_to_search:
        amf_result = _search_amf_log(bare_imsi, time_window_minutes)
        if amf_result["error"]:
            nf_errors["amf"] = amf_result["error"]

    # ── Step 2: derive search window ──────────────────────────────────────────
    if amf_result["first_ts"] and amf_result["last_ts"]:
        search_start = amf_result["first_ts"] - timedelta(seconds=2)
        search_end = amf_result["last_ts"] + timedelta(seconds=2)
    else:
        search_end = datetime.now(timezone.utc)
        search_start = search_end - timedelta(minutes=time_window_minutes)

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
        if nf == "smf":
            smf_seids = data.get("seids", [])

    if "upf" in nfs_to_search:
        upf_data = _search_nf_by_time(
            "upf", search_start, search_end, seids=smf_seids or None
        )
        nf_data["upf"] = upf_data
        if upf_data["error"]:
            nf_errors["upf"] = upf_data["error"]

    # ── Step 4: build structured events ───────────────────────────────────────
    all_events: list[dict] = []
    raw_log_lines: dict[str, list[str]] = {}

    for nf, data in nf_data.items():
        lines = data.get("lines", [])
        raw_log_lines[nf] = [ln["raw"] for ln in lines]
        for line_data in lines:
            direction, from_e, to_e, msg_type = _infer_event(nf, line_data["message"])
            all_events.append({
                "timestamp": line_data["ts"].isoformat(),
                "nf": nf,
                "level": line_data["level"],
                "direction": direction,
                "message_type": msg_type,
                "from": from_e,
                "to": to_e,
                "raw": line_data["raw"],
            })

    all_events.sort(key=lambda e: e["timestamp"])

    # ── Step 5: build summary and output ──────────────────────────────────────
    registration_success = any(
        "Registration Accept" in e["message_type"] for e in all_events
    )
    pdu_session_success = any(
        "PDU Session Establishment Accept" in e["message_type"] for e in all_events
    )

    ue_ips = list(nf_data.get("smf", {}).get("ue_ips", []))
    ue_ip_assigned = ue_ips[0] if ue_ips else None

    error_lines = [
        e["raw"] for e in all_events
        if e["level"] in ("ERROR", "CRIT", "FATAL", "WARNING", "WARN")
    ]

    # Participants in order of first appearance, filtered to known set
    seen: list[str] = []
    for e in all_events:
        for p in (e["from"], e["to"]):
            if p and p not in seen:
                seen.append(p)
    active_participants = [p for p in _DEFAULT_PARTICIPANTS if p in seen]
    mermaid_hint = "sequenceDiagram\n" + "\n".join(
        f"    participant {p}" for p in active_participants
    )

    result: dict = {
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
        },
        "events": all_events,
        "raw_log_lines": raw_log_lines,
        "mermaid_hint": mermaid_hint,
    }

    if nf_errors:
        result["nf_errors"] = nf_errors

    return result
