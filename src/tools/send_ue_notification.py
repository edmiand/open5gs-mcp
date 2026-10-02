"""send_ue_notification — push a short text notification to a UE over its PDU session."""

import time
from typing import Literal

import httpx
from typing_extensions import NotRequired, TypedDict

from tools._nf_util import metrics_url as _metrics_url
from tools._schema_util import ErrorDetail
from tools._subscriber_util import normalize_supi as _normalize_supi
from tools.list_ue_sessions import _bare_imsi, _fetch_all_pages

_MAX_MESSAGE_LEN = 500
_MIN_PORT, _MAX_PORT = 1, 65535
_REQUEST_TIMEOUT_S = 5.0


# ── structured output schema ─────────────────────────────────────────────────

class NotificationDetail(TypedDict):
    ok: Literal[True]
    imsi: str
    ue_ip: str
    port: int
    http_status: int
    round_trip_ms: float
    incident_id: NotRequired[str]


class NotificationErrorDetail(ErrorDetail):
    reason: Literal[
        "invalid_input", "no_session", "connection_refused", "timeout", "request_error",
    ]


class NotificationResult(TypedDict):
    summary: str
    detail: NotificationDetail | NotificationErrorDetail


def _error(reason: str, message: str) -> NotificationResult:
    return {
        "summary": f"Error: {message}",
        "detail": {"ok": False, "error": message, "reason": reason},
    }


# ── UE IP resolution ──────────────────────────────────────────────────────────

def _resolve_ue_ip(bare_imsi: str) -> tuple[str | None, str]:
    """Resolve the current IPv4 address of an active PDU session for an IMSI.

    Queries the SMF's /pdu-info endpoint fresh on every call (the same source
    list_ue_sessions joins against) — never cached, since the assigned IP can
    change between PDU sessions.

    Returns (ipv4, status) where status is "ok" (lookup succeeded — ipv4 may
    still be None if no session has an address), "unreachable", "timeout", or
    "error: <msg>" (SMF endpoint itself could not be queried).
    """
    smf_base = _metrics_url("smf")
    smf_items, smf_status = _fetch_all_pages(smf_base, "/pdu-info")
    if smf_status != "ok":
        return None, smf_status

    for ue in smf_items:
        if _bare_imsi(ue.get("supi", "")) != bare_imsi:
            continue
        for pdu in ue.get("pdu", []):
            if pdu.get("ipv4") and pdu.get("pdu_state", "active") != "released":
                return pdu["ipv4"], "ok"

    return None, "ok"


# ── main ───────────────────────────────────────────────────────────────────────

def send_ue_notification(
    imsi: str,
    message: str,
    incident_id: str | None = None,
    port: int = 9000,
) -> NotificationResult:
    """
    Deliver a short text notification to a UE over its active 5G data session.

    Resolves the IMSI to the UE's current PDU session IPv4 address (via the
    SMF, queried fresh — never cached), then sends an HTTP POST from this VM
    to http://<ue_ip>:<port>/notify with JSON body
    {"message": <message>, "incident_id": <incident_id or None>}. The request
    is sent directly (no proxy) so it routes over the UPF's ogstun interface
    like any other core-to-UE traffic, with a 5 second timeout.

    Args:
        imsi: IMSI digits (10-15) or SUPI ("imsi-<digits>").
        message: Notification text, max 500 characters.
        incident_id: Optional caller-supplied identifier included in the
                     notification body, useful for correlating deliveries
                     with an incident/ticket.
        port: TCP port the UE-side listener is on. Default 9000.

    Returns:
        {
          "ok": bool,
          # success:
          "imsi": "<digits>",
          "ue_ip": "<ipv4>",
          "port": int,
          "http_status": int,
          "round_trip_ms": float,
          "incident_id": str,          # only when supplied
          # failure:
          "error": str,
          "reason": "invalid_input" | "no_session" | "connection_refused" |
                    "timeout" | "request_error",
        }
    """
    if not isinstance(imsi, str) or not imsi.strip():
        return _error("invalid_input", "imsi is required")
    try:
        _, bare_imsi = _normalize_supi(imsi)
    except ValueError as exc:
        return _error("invalid_input", str(exc))

    if not isinstance(message, str) or not message.strip():
        return _error("invalid_input", "message is required and must not be empty")
    if len(message) > _MAX_MESSAGE_LEN:
        return _error(
            "invalid_input",
            f"message exceeds {_MAX_MESSAGE_LEN} characters ({len(message)})",
        )

    if incident_id is not None and not isinstance(incident_id, str):
        return _error("invalid_input", "incident_id must be a string")

    if isinstance(port, bool) or not isinstance(port, int) or not (_MIN_PORT <= port <= _MAX_PORT):
        return _error(
            "invalid_input",
            f"port must be an integer in [{_MIN_PORT}, {_MAX_PORT}]",
        )

    ue_ip, smf_status = _resolve_ue_ip(bare_imsi)
    if smf_status != "ok":
        return _error(
            "no_session",
            f"Could not look up sessions for imsi-{bare_imsi}: SMF is {smf_status}",
        )
    if ue_ip is None:
        return _error(
            "no_session",
            f"No active PDU session with an assigned IPv4 address for imsi-{bare_imsi}",
        )

    url = f"http://{ue_ip}:{port}/notify"
    body = {"message": message, "incident_id": incident_id}

    start = time.monotonic()
    try:
        r = httpx.post(url, json=body, timeout=_REQUEST_TIMEOUT_S, trust_env=False)
    except httpx.ConnectError as exc:
        return _error(
            "connection_refused",
            f"Connection refused by {ue_ip}:{port} — no listener on /notify ({exc})",
        )
    except httpx.TimeoutException:
        return _error(
            "timeout",
            f"Timed out after {_REQUEST_TIMEOUT_S:g}s waiting for {ue_ip}:{port}/notify",
        )
    except httpx.RequestError as exc:
        return _error("request_error", f"Request to {ue_ip}:{port}/notify failed: {exc}")
    round_trip_ms = (time.monotonic() - start) * 1000

    detail: dict = {
        "ok": True,
        "imsi": bare_imsi,
        "ue_ip": ue_ip,
        "port": port,
        "http_status": r.status_code,
        "round_trip_ms": round(round_trip_ms, 1),
    }
    if incident_id is not None:
        detail["incident_id"] = incident_id

    _summary = (
        f"Delivered notification to imsi-{bare_imsi} at {ue_ip}:{port} "
        f"(HTTP {r.status_code})."
    )
    return {"summary": _summary, "detail": detail}
