"""open5gs_version — installed Open5GS build version, read from a compiled binary."""

import re
import subprocess
from typing import Literal, TypedDict

from typing_extensions import NotRequired

from tools._nf_util import _OPEN5GS
from tools._schema_util import ErrorDetail
from tools.read_nf_config import CONFIG_NFS

BIN_DIR = _OPEN5GS / "install" / "bin"

# Every open5gs-*d binary is built from the same src/main.c and embeds the
# same OPEN5GS_VERSION string, so any one works — amf is tried first since
# it's present on virtually every deployment; the rest are a fallback for
# unusual installs (e.g. an AMF-less UPF-only edge node).
_PREFERRED_ORDER = ["amf"] + [nf for nf in CONFIG_NFS if nf != "amf"]

# meson.build bakes in either `git describe --abbrev=7 --dirty=+` (e.g.
# "v2.8.0-68-gb811f1d+") or, with no .git present, plain "v<project_version>".
_VERSION_RE = re.compile(
    r"Open5GS\s+v?(?P<version>\d+\.\d+\.\d+)"
    r"(?:-(?P<commits>\d+)-g(?P<hash>[0-9a-f]+))?"
    r"(?P<dirty>\+)?"
)


class VersionDetail(TypedDict):
    ok: Literal[True]
    raw: str
    version: str
    tag: str
    commits_since_tag: int | None
    commit_hash: NotRequired[str]
    dirty: bool
    checked_binary: str


class VersionResult(TypedDict):
    summary: str
    detail: VersionDetail | ErrorDetail


def open5gs_version() -> VersionResult:
    """
    Report the installed Open5GS version.

    Reads it from a compiled open5gs-*d binary's `-v` output rather than any
    source file, so it reflects what's actually running/installed (not a
    build directory that may have been cleaned up). `-v` just prints the
    version and exits per src/main.c — it doesn't start the daemon or touch
    any running NF.

    The version string is baked in at build time (src/meson.build): either
    `git describe --abbrev=7 --dirty=+` against the source tree, or plain
    'v' + project_version if the tree had no .git. Use `tag` to compare
    against GitHub release tags.

    Returns:
        {
          "ok": True,
          "raw": str,                       # e.g. "Open5GS v2.8.0-68-gb811f1d"
          "version": str,                   # base semver, e.g. "2.8.0"
          "tag": str,                       # e.g. "v2.8.0" -- matches GitHub release tags
          "commits_since_tag": int | None,  # None if built exactly at a tag
          "commit_hash": str,               # short git hash; omitted if unavailable
          "dirty": bool,                    # source tree had uncommitted changes at build time
          "checked_binary": str,            # e.g. "open5gs-amfd"
        }

        On error:
        {"ok": False, "error": str}
    """
    binary = None
    for nf in _PREFERRED_ORDER:
        candidate = BIN_DIR / f"open5gs-{nf}d"
        if candidate.exists():
            binary = candidate
            break

    if binary is None:
        _e = f"No open5gs-*d binaries found in {BIN_DIR}"
        return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

    try:
        r = subprocess.run(
            [str(binary), "-v"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _e = f"Failed to run {binary.name} -v: {exc}"
        return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

    m = _VERSION_RE.search(r.stdout)
    if not m:
        _e = f"Could not parse version from {binary.name} -v output: {r.stdout.strip()!r}"
        return {"summary": f"Error: {_e}", "detail": {"ok": False, "error": _e}}

    version = m.group("version")
    commits = int(m.group("commits")) if m.group("commits") else None
    dirty = m.group("dirty") is not None

    detail: dict = {
        "ok": True,
        "raw": r.stdout.strip(),
        "version": version,
        "tag": f"v{version}",
        "commits_since_tag": commits,
        "dirty": dirty,
        "checked_binary": binary.name,
    }
    if m.group("hash"):
        detail["commit_hash"] = m.group("hash")

    _summary = f"Open5GS {version}"
    if commits:
        _extra = f"{commits} commit(s) past tag v{version}"
        if dirty:
            _extra += ", dirty"
        _summary += f" ({_extra})"
    elif dirty:
        _summary += " (dirty)"
    _summary += "."

    return {"summary": _summary, "detail": detail}
