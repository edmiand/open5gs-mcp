"""tail_nf_logs — filtered log reads across one or more Open5GS NF log files."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.nf_lifecycle import _SCRIPT

_LOG_DIR = _SCRIPT.parent / "install" / "var" / "log" / "open5gs"

_ALL_NFS = ["nrf", "scp", "amf", "smf", "upf", "ausf", "udm", "udr", "pcf", "nssf", "bsf", "webui"]

# Log line: optional ANSI + timestamp: [component] LEVEL: message
_LINE_RE = re.compile(
    r"^(?:\x1b\[[0-9;]*m)?"
    r"(\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d+)"   # group 1: MM/DD HH:MM:SS.mmm
    r":\s+\[(\w+)\]"                             # group 2: component
    r"\s+(\w+)"                                  # group 3: LEVEL
    r":\s+(.+?)$"                                # group 4: message
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SOURCE_RE = re.compile(r"\(([^)]+:\d+)\)$")   # (file:line) at end of message

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


def _parse_ts(ts_str: str, year: int) -> datetime | None:
    """Parse 'MM/DD HH:MM:SS.mmm' into a timezone-aware datetime."""
    try:
        dt = datetime.strptime(f"{year}/{ts_str}", "%Y/%m/%d %H:%M:%S.%f")
    except ValueError:
        try:
            dt = datetime.strptime(f"{year}/{ts_str}", "%Y/%m/%d %H:%M:%S")
        except ValueError:
            return None
    # Handle year-rollover (log in Dec read in Jan)
    now = datetime.now()
    if dt.month > now.month and (dt.month - now.month) > 6:
        dt = dt.replace(year=year - 1)
    return dt.replace(tzinfo=timezone.utc)


def _min_level_int(level_filter: str) -> int:
    canonical = _LEVEL_ALIASES.get(level_filter.lower(), level_filter.upper())
    return _LEVELS.get(canonical, 0)


def _parse_line(raw: str, year: int) -> dict | None:
    """Return a parsed log record dict or None if the line doesn't match."""
    clean = _ANSI_RE.sub("", raw).rstrip()
    m = _LINE_RE.match(clean)
    if not m:
        return None
    ts_str, component, level, message = m.group(1), m.group(2), m.group(3), m.group(4)
    ts = _parse_ts(ts_str, year)
    if ts is None:
        return None

    # Extract source file reference from end of message
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


def _read_nf_log(
    nf: str,
    min_level: int,
    pattern: re.Pattern | None,
    since: datetime | None,
    max_lines: int,
) -> tuple[list[dict], str | None]:
    """
    Read and filter one NF's log file.
    Returns (records, error_msg).  error_msg is None on success.
    """
    logfile = _LOG_DIR / f"{nf}.log"
    if not logfile.exists():
        return [], "log file not found"

    try:
        with open(logfile, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            raw_bytes = fh.read()
    except PermissionError:
        return [], "permission denied (UPF log requires root)"
    except OSError as exc:
        return [], str(exc)

    text = raw_bytes.decode("utf-8", errors="replace")
    year = datetime.now().year
    records: list[dict] = []

    for raw_line in text.splitlines():
        rec = _parse_line(raw_line, year)
        if rec is None:
            continue

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
    return records[-max_lines:], None


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
        return {
            "ok": False,
            "error": f"Unknown NF(s): {invalid}. Valid: {_ALL_NFS}",
        }

    if not (1 <= lines <= 500):
        return {"ok": False, "error": "lines must be between 1 and 500"}

    level = level.lower()
    if level not in {"debug", "info", "warn", "warning", "error"}:
        return {"ok": False, "error": "level must be one of: debug info warn error"}

    # Compile grep pattern
    pattern: re.Pattern | None = None
    if grep:
        try:
            pattern = re.compile(grep, re.IGNORECASE)
        except re.error as exc:
            return {"ok": False, "error": f"Invalid grep pattern: {exc}"}

    # Parse since
    try:
        since_dt = _parse_since(since)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    min_level = _min_level_int(level)

    # ── read each NF ────────────────────────────────────────────────────────

    all_records: list[dict] = []
    nf_counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    # Per-NF limit: avoid one noisy NF drowning out others
    per_nf_limit = max(lines, 200)

    for n in nf_list:
        recs, err = _read_nf_log(n, min_level, pattern, since_dt, per_nf_limit)
        if err:
            errors[n] = err
        nf_counts[n] = len(recs)
        all_records.extend(recs)

    # ── sort by timestamp and cap ────────────────────────────────────────────

    all_records.sort(key=lambda r: r["ts"])
    total_matched = len(all_records)
    all_records = all_records[-lines:]  # keep the most recent `lines` entries

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

    return {
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
        **({"errors": errors} if errors else {}),
    }
