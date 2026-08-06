"""read_nf_config — read and return the YAML configuration for any Open5GS NF."""

from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml

from tools._schema_util import ErrorDetail
from tools.nf_lifecycle import VALID_NFS, _SCRIPT

_CONFIG_DIR = _SCRIPT.parent / "install" / "etc" / "open5gs"

# webui is managed by Node.js and has no YAML config in this directory
_NFS_WITHOUT_YAML = frozenset({"webui"})

# NFs whose YAML config is available (used by the open5gs://config/{nf} resource)
CONFIG_NFS = sorted(VALID_NFS - _NFS_WITHOUT_YAML)


class NfConfigError(Exception):
    """Raised by _load_nf_config; str(exc) is a caller-facing message."""


def _load_nf_config(nf: str) -> tuple[Any, Path]:
    """Validate `nf` and parse its YAML config. Returns (data, config_file_path).

    Shared by the read_nf_config tool (below) and the open5gs://config/{nf}
    resource in server.py — both need identical validation and load logic.
    """
    nf = nf.lower().strip()
    if nf not in VALID_NFS:
        raise NfConfigError(f"Unknown NF '{nf}'. Valid: {sorted(VALID_NFS)}")
    if nf in _NFS_WITHOUT_YAML:
        raise NfConfigError(
            f"'{nf}' has no YAML config (it is managed by Node.js). "
            f"NFs with YAML configs: {CONFIG_NFS}"
        )

    cfg_path = _CONFIG_DIR / f"{nf}.yaml"
    if not cfg_path.exists():
        raise NfConfigError(f"Config file not found: {cfg_path}")

    try:
        with open(cfg_path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise NfConfigError(f"YAML parse error in {cfg_path}: {exc}") from exc
    except OSError as exc:
        raise NfConfigError(f"Cannot read {cfg_path}: {exc}") from exc

    return data, cfg_path


# ── structured output schema ─────────────────────────────────────────────────

class ReadConfigDetail(TypedDict):
    ok: Literal[True]
    nf: str
    config_file: str
    path: str | None
    config: Any  # arbitrary YAML subtree — shape depends on `path`


class ReadConfigResult(TypedDict):
    summary: str
    detail: ReadConfigDetail | ErrorDetail


def _resolve_path(data: Any, path: str) -> Any:
    """Traverse a parsed YAML tree using dot-separated path, e.g. 'amf.sbi.server'."""
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                available = list(current.keys()) if isinstance(current, dict) else []
                raise KeyError(
                    f"Key '{part}' not found. "
                    f"Available keys: {available}"
                )
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except ValueError:
                raise KeyError(f"Expected integer index at '{part}', got a list")
            except IndexError:
                raise KeyError(f"List index {part} out of range (length {len(current)})")
        else:
            raise KeyError(
                f"Cannot traverse into {type(current).__name__} with key '{part}'"
            )
    return current


def read_nf_config(nf: str, path: str | None = None) -> ReadConfigResult:
    """
    Read and return the YAML configuration for any Open5GS network function.

    Parses install/etc/open5gs/<nf>.yaml and returns the full config tree, or
    a specific subtree when path is supplied. Useful for verifying SBI addresses,
    NRF/SCP URIs, slice configs, and interface bindings without opening files manually.

    A resource is also available for browsing without a tool call:
    open5gs://config/{nf} returns the same full config tree.

    Args:
        nf:   NF name. Valid: amf smf upf ausf udm udr pcf nssf bsf nrf scp
        path: Optional dot-separated path into the config tree.
              Examples:
                "amf.sbi"                → SBI server/client block
                "amf.sbi.client.scp"     → SCP URI the AMF talks to
                "smf.pfcp.client.upf"    → UPF address SMF sends PFCP to
                "smf.session"            → UE subnet pool
                "amf.guami"              → GUAMI (PLMN + AMF ID)
                "logger"                 → log file path and level
              List items can be indexed numerically: "amf.sbi.server.0"

    Returns:
        {
          "ok": True,
          "nf": str,
          "config_file": str,         # absolute path to the YAML file
          "path": str | None,         # the path argument, echoed back
          "config": dict | list | ...  # parsed subtree (full tree if path omitted)
        }

        On error:
        {
          "ok": False,
          "error": str
        }
    """
    try:
        data, cfg_path = _load_nf_config(nf)
    except NfConfigError as exc:
        return {"summary": f"Error: {exc}", "detail": {"ok": False, "error": str(exc)}}
    nf = nf.lower().strip()

    if path:
        try:
            subtree = _resolve_path(data, path)
        except KeyError as exc:
            return {"summary": f"Error: {exc}", "detail": {"ok": False, "error": str(exc)}}
    else:
        subtree = data

    _path_clause = f" at path \"{path}\"" if path else ""
    return {
        "summary": f"Read {nf} config{_path_clause}.",
        "detail": {
            "ok": True,
            "nf": nf,
            "config_file": str(cfg_path),
            "path": path,
            "config": subtree,
        },
    }
