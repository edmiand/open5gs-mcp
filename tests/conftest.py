"""Shared fixtures for open5gs-mcp tests."""

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Make src/ importable for all test files
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Date/log helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def today() -> str:
    return date.today().strftime("%m/%d")


def log_line(nf: str, level: str, message: str, ts: str | None = None) -> str:
    """Return a valid Open5GS log line (binary-safe: just UTF-8 text)."""
    if ts is None:
        ts = date.today().strftime("%m/%d") + " 10:00:00.000"
    return f"{ts}: [{nf}] {level}: {message}\n"


@pytest.fixture
def make_log_line():
    return log_line


def write_nf_log(log_dir: Path, nf: str, lines: list[str]) -> Path:
    """Write lines to <log_dir>/<nf>.log and return the path."""
    logfile = log_dir / f"{nf}.log"
    logfile.write_bytes("".join(lines).encode("utf-8"))
    return logfile


@pytest.fixture
def write_log():
    return write_nf_log


# ── Subscriber helpers ────────────────────────────────────────────────────────

def make_subscriber(imsi: str = "999700000000001") -> dict:
    """Minimal valid subscriber document (pre-serialised — no bson types)."""
    return {
        "_id": "fake_oid",
        "imsi": imsi,
        "schema_version": 1,
        "msisdn": [],
        "imeisv": [],
        "mme_host": [],
        "mme_realm": [],
        "purge_flag": [],
        "security": {
            "k": "secret_k",
            "opc": "secret_opc",
            "amf": "8000",
            "sqn": 0,
            "rand": None,
            "op": None,
        },
        "ambr": {
            "downlink": {"value": 1, "unit": 3},
            "uplink": {"value": 1, "unit": 3},
        },
        "slice": [
            {
                "sst": 1,
                "default_indicator": True,
                "session": [
                    {
                        "name": "internet",
                        "type": 3,
                        "qos": {
                            "index": 9,
                            "arp": {
                                "priority_level": 8,
                                "pre_emption_capability": 1,
                                "pre_emption_vulnerability": 1,
                            },
                        },
                        "ambr": {
                            "downlink": {"value": 1, "unit": 3},
                            "uplink": {"value": 1, "unit": 3},
                        },
                        "pcc_rule": [],
                    }
                ],
            }
        ],
        "subscriber_status": 0,
        "network_access_mode": 0,
        "operator_determined_barring": 0,
        "access_restriction_data": 32,
        "subscribed_rau_tau_timer": 12,
    }


@pytest.fixture
def fake_subscriber():
    return make_subscriber


def make_mock_col(docs: list[dict] | None = None) -> MagicMock:
    """Return a mock pymongo Collection wired to the given documents."""
    if docs is None:
        docs = [make_subscriber()]

    col = MagicMock()

    def _find_one(query, *args, **kwargs):
        imsi = query.get("imsi")
        return next((d for d in docs if d.get("imsi") == imsi), None)

    col.find_one.side_effect = _find_one
    col.count_documents.return_value = len(docs)

    # Simulate find(...).sort(...)
    cursor = MagicMock()
    cursor.sort.return_value = list(docs)
    col.find.return_value = cursor

    col.insert_one.return_value = MagicMock(inserted_id="fake_oid")
    col.delete_one.return_value = MagicMock(deleted_count=1)
    col.replace_one.return_value = MagicMock(modified_count=1)
    return col


@pytest.fixture
def mock_col():
    return make_mock_col


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def http_response(data: dict, status_code: int = 200) -> MagicMock:
    """Return a mock httpx response."""
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = data
    r.raise_for_status.return_value = None
    return r


def oam_page(items: list, page_size: int = 100) -> dict:
    """Build an AMF OAM paginated JSON response."""
    return {"items": items, "pager": {"count": len(items), "page_size": page_size}}


@pytest.fixture
def make_http_response():
    return http_response


@pytest.fixture
def make_oam_page():
    return oam_page


# ── subprocess helpers ────────────────────────────────────────────────────────

def completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    """Return a mock subprocess.CompletedProcess."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


@pytest.fixture
def make_proc():
    return completed


# ── Tool response helper ──────────────────────────────────────────────────────

def unwrap(result: dict) -> dict:
    """Assert tool result has a string summary, then return its detail dict."""
    assert "summary" in result, f"response missing 'summary' key: {list(result.keys())}"
    assert isinstance(result["summary"], str), (
        f"summary must be a str, got {type(result['summary'])!r}: {result['summary']!r}"
    )
    assert "detail" in result, f"response missing 'detail' key: {list(result.keys())}"
    return result["detail"]
