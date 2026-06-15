"""Shared log-parsing utilities used by tail_nf_logs, ue_trace, and system_health_snapshot."""

import re
from datetime import datetime, timezone

# Open5GS log line: optional ANSI escape + MM/DD HH:MM:SS.mmm: [COMPONENT] LEVEL: message
_LINE_RE = re.compile(
    r"^(?:\x1b\[[0-9;]*m)?"
    r"(\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d+)"   # group 1: MM/DD HH:MM:SS.mmm
    r":\s+\[(\w+)\]"                             # group 2: component
    r"\s+(\w+)"                                  # group 3: LEVEL
    r":\s+(.+?)$"                                # group 4: message
)
_ANSI_RE   = re.compile(r"\x1b\[[0-9;]*m")
_SOURCE_RE = re.compile(r"\(([^)]+:\d+)\)$")    # (file:line) suffix on messages


def _parse_line(raw: str, year: int) -> dict | None:
    """Parse a raw Open5GS log line into a structured record dict, or None if unmatched."""
    clean = _ANSI_RE.sub("", raw).rstrip()
    m = _LINE_RE.match(clean)
    if not m:
        return None
    ts_str, component, level, message = m.group(1), m.group(2), m.group(3), m.group(4)
    ts = parse_log_ts(ts_str, year)
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


def parse_log_ts(ts_str: str, year: int) -> datetime | None:
    """Parse 'MM/DD HH:MM:SS.mmm' log timestamp into a UTC-aware datetime.

    Handles year rollover: if the parsed date is in the future (e.g. a
    Dec 31 log entry read on Jan 2) the year is rolled back by one.
    Log timestamps are UTC; comparison uses datetime.utcnow() to avoid
    local-timezone skew on non-UTC servers.
    """
    try:
        dt = datetime.strptime(f"{year}/{ts_str}", "%Y/%m/%d %H:%M:%S.%f")
    except ValueError:
        try:
            dt = datetime.strptime(f"{year}/{ts_str}", "%Y/%m/%d %H:%M:%S")
        except ValueError:
            return None
    if dt > datetime.utcnow():
        dt = dt.replace(year=year - 1)
    return dt.replace(tzinfo=timezone.utc)
