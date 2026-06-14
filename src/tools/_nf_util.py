"""Shared NF path constants and metrics URL resolution."""

from pathlib import Path

import yaml

# Open5GS install root — sibling repo at ../open5gs relative to this project
_OPEN5GS   = Path(__file__).resolve().parent.parent.parent.parent / "open5gs"
CONFIG_DIR = _OPEN5GS / "install" / "etc" / "open5gs"
LOG_DIR    = _OPEN5GS / "install" / "var" / "log" / "open5gs"
RUN_DIR    = _OPEN5GS / "install" / "var" / "run" / "open5gs"

_METRICS_DEFAULTS: dict[str, str] = {
    "amf":  "http://127.0.0.5:9090",
    "smf":  "http://127.0.0.4:9090",
    "upf":  "http://127.0.0.7:9090",
    "ausf": "http://127.0.0.11:9090",
    "udm":  "http://127.0.0.12:9090",
    "udr":  "http://127.0.0.20:9090",
    "pcf":  "http://127.0.0.13:9090",
    "nssf": "http://127.0.0.14:9090",
    "bsf":  "http://127.0.0.15:9090",
    "nrf":  "http://127.0.0.10:9090",
    "scp":  "http://127.0.0.200:9090",
}


def metrics_url(nf: str) -> str:
    """Return the Prometheus metrics base URL for an NF, read from its YAML config."""
    try:
        cfg_path = CONFIG_DIR / f"{nf}.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        srv = cfg[nf]["metrics"]["server"][0]
        return f"http://{srv['address']}:{srv['port']}"
    except Exception:
        return _METRICS_DEFAULTS.get(nf, "http://127.0.0.1:9090")
