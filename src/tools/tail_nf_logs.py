"""tail_nf_logs — filtered log reads across one or more Open5GS NF log files."""

import re
from datetime import datetime, timedelta, timezone

from tools._log_util import _parse_line
from tools._nf_util import LOG_DIR as _LOG_DIR

_ALL_NFS = ["nrf", "scp", "amf", "smf", "upf", "ausf", "udm", "udr", "pcf", "nssf", "bsf", "webui"]

# Level hierarchy (higher index = higher severity)
_LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "WARN": 2, "ERROR": 3, "CRIT": 4, "FATAL": 5}
_LEVEL_ALIASES = {"warn": "WARN", "warning": "WARNING"}

# How much of each log file to read from the tail (bytes)
_TAIL_BYTES = 2 * 1024 * 1024   # 2 MB


# ── helpers ────────────────────────────────────────────────────────────────────

def _parse_since(since: str | None) -> datetime | None:
    """Parse since= into a UTC-aware datetime.
    Accepts:
      - relative strings: "15m", "1h", "2h30m", "30s"
      - ISO-8601 datetime string (naive treated as local)
    """
    if not since:
        return None
    since = since.strip()

    # Relative: 15m / 2h / 1h30m / 90s
    rel = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", since)
    if rel and since:
        h = int(rel.group(1) or 0)
        m = int(rel.group(2) or 0)
        s = int(rel.group(3) or 0)
        total = h * 3600 + m * 60 + s
        if total > 0:
            return datetime.now(timezone.utc) - timedelta(seconds=total)

    # ISO datetime
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            dt = datetime.strptime(since, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    raise ValueError(f"Cannot parse since='{since}'. Use e.g. '15m', '1h', '2026-06-03T20:00:00'.")


def _min_level_int(level_filter: str) -> int:
    canonical = _LEVEL_ALIASES.get(level_filter.lower(), level_filter.upper())
    return _LEVELS.get(canonical, 0)


def _read_nf_log(
    nf: str,
    min_level: int,
    pattern: re.Pattern | None,
    since: datetime | None,
    max_lines: int,
) -> tuple[list[dict], datetime | None, str | None]:
    """
    Read and filter one NF's log file.
    Returns (records, window_start, error_msg).
    window_start is the earliest timestamp found in the 2MB window (before filtering),
    used to detect truncation when a since= query predates the available window.
    """
    logfile = _LOG_DIR / f"{nf}.log"
    if not logfile.exists():
        return [], None, "log file not found"

    try:
        with open(logfile, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            raw_bytes = fh.read()
    except PermissionError:
        return [], None, "permission denied (UPF log requires root)"
    except OSError as exc:
        return [], None, str(exc)

    text = raw_bytes.decode("utf-8", errors="replace")
    year = datetime.now().year
    records: list[dict] = []
    window_start: datetime | None = None

    for raw_line in text.splitlines():
        rec = _parse_line(raw_line, year)
        if rec is None:
            continue

        # Track earliest timestamp in the raw window (before any filtering)
        if window_start is None or rec["ts"] < window_start:
            window_start = rec["ts"]

        # Time filter
        if since and rec["ts"] < since:
            continue

        # Level filter
        if _LEVELS.get(rec["level"], 0) < min_level:
            continue

        # Keyword / regex filter
        if pattern and not pattern.search(raw_line):
            continue

        rec["nf"] = nf
        records.append(rec)

    # Keep last max_lines per NF (before merge)
    return records[-max_lines:], window_start, None


# ── main ───────────────────────────────────────────────────────────────────────

def tail_nf_logs(
    nf: str | list[str] = "all",
    level: str = "info",
    grep: str | None = None,
    lines: int = 100,
    since: str | None = None,
) -> dict:
    """
    Filtered log reads across one or more Open5GS NF log files.

    Reads from the tail of each log file, filters by level/keyword/time window,
    then interleaves results from all NFs in chronological order. Ideal for
    correlating events across AMF + AUSF + UDM during a single registration
    attempt.

    Args:
        nf:     NF name, list of names, or "all".
                Valid: amf smf upf ausf udm udr pcf nssf bsf nrf scp webui
        level:  Minimum severity to include: debug | info | warn | error
                (error → only ERROR/CRIT/FATAL; info → INFO and above; etc.)
        grep:   Optional keyword or Python regex applied to the raw log line.
                Case-insensitive. Examples: "imsi-999700", "Registration",
                "5QI|NSSAI"
        lines:  Max total log lines to return across all NFs (default 100, max 500).
        since:  Start of time window. Relative ("15m", "2h") or ISO datetime
                ("2026-06-03T20:00:00"). Omit to read from current tail.

    Returns:
        {
          "ok": bool,
          "query": {nf, level, grep, lines, since},
          "total_matched": int,
          "lines": [
            {
              "nf": str,
              "timestamp": "MM/DD HH:MM:SS.mmm",
              "component": str,
              "level": str,
              "message": str,
              "source": str | None
            }
          ],
          "nf_counts": {"amf": int, ...},
          "errors": {"upf": "permission denied", ...}
        }
    """
    # ── validate inputs ──────────────────────────────────────────────────────

    # Normalise nf list
    if isinstance(nf, str):
        nf_list = _ALL_NFS if nf.lower() == "all" else [nf.lower()]
    else:
        nf_list = [n.lower() for n in nf]

    invalid = [n for n in nf_list if n not in _ALL_NFS]
    if invalid:
        _e = f"Unknown NF(s): {invalid}. Valid: {_ALL_NFS}"
        return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

    if not (1 <= lines <= 500):
        return {"summary": "Error: lines must be between 1 and 500.",
                "detail": {"ok": False, "error": "lines must be between 1 and 500"}}

    level = level.lower()
    if level not in {"debug", "info", "warn", "warning", "error"}:
        return {"summary": "Error: level must be one of: debug info warn error.",
                "detail": {"ok": False, "error": "level must be one of: debug info warn error"}}

    # Compile grep pattern
    pattern: re.Pattern | None = None
    if grep:
        try:
            pattern = re.compile(grep, re.IGNORECASE)
        except re.error as exc:
            _e = f"Invalid grep pattern: {exc}"
            return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

    # Parse since
    try:
        since_dt = _parse_since(since)
    except ValueError as exc:
        return {"summary": f"Error: {exc}", "detail": {"ok": False, "error": str(exc)}}

    min_level = _min_level_int(level)

    # ── read each NF ────────────────────────────────────────────────────────

    all_records: list[dict] = []
    nf_counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    earliest_window: datetime | None = None
    # Per-NF limit: avoid one noisy NF drowning out others
    per_nf_limit = max(lines, 200)

    for n in nf_list:
        recs, window_start, err = _read_nf_log(n, min_level, pattern, since_dt, per_nf_limit)
        if err:
            errors[n] = err
        nf_counts[n] = len(recs)
        all_records.extend(recs)
        if window_start and (earliest_window is None or window_start < earliest_window):
            earliest_window = window_start

    # ── sort by timestamp and cap ────────────────────────────────────────────

    all_records.sort(key=lambda r: r["ts"])
    total_matched = len(all_records)
    all_records = all_records[-lines:]  # keep the most recent `lines` entries

    # Recount per-NF after the global cap so nf_counts matches the returned lines.
    nf_counts = {}
    for r in all_records:
        nf_counts[r["nf"]] = nf_counts.get(r["nf"], 0) + 1

    # ── serialise ────────────────────────────────────────────────────────────

    output = []
    for r in all_records:
        output.append({
            "nf":        r["nf"],
            "timestamp": r["ts_str"],
            "component": r["component"],
            "level":     r["level"],
            "message":   r["message"],
            "source":    r["source"],
        })

    # Detect truncation: since= requested data older than our 2MB window covers
    truncated = (
        since_dt is not None
        and earliest_window is not None
        and since_dt < earliest_window
    )

    detail: dict = {
        "ok": True,
        "query": {
            "nf":    nf_list,
            "level": level,
            "grep":  grep,
            "lines": lines,
            "since": since,
        },
        "total_matched": total_matched,
        "lines": output,
        "nf_counts": nf_counts,
    }
    if truncated:
        detail["truncated"] = True
        detail["earliest_available"] = earliest_window.strftime("%m/%d %H:%M:%S")
    if errors:
        detail["errors"] = errors
    _summary = (f"Returned {len(output)} of {total_matched} matched log line(s) "
                f"from {len(nf_list)} NF(s).")
    return {"summary": _summary, "detail": detail}
