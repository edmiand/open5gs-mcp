"""Shared log-parsing utilities for tail_nf_logs and ue_trace."""

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
