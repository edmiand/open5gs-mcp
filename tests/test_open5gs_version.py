"""Tests for open5gs_version tool."""

import pytest

import tools.open5gs_version as mod
from tools.open5gs_version import open5gs_version
from conftest import unwrap


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "BIN_DIR", tmp_path)
    return tmp_path


def touch_binary(bin_dir, name: str = "open5gs-amfd"):
    path = bin_dir / name
    path.write_text("")
    path.chmod(0o755)
    return path


@pytest.mark.unit
def test_dev_build_with_commits_and_hash(bin_dir, monkeypatch, make_proc):
    touch_binary(bin_dir)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: make_proc(stdout="Open5GS v2.8.0-68-gb811f1d\n"),
    )
    detail = unwrap(open5gs_version())
    assert detail["ok"] is True
    assert detail["version"] == "2.8.0"
    assert detail["tag"] == "v2.8.0"
    assert detail["commits_since_tag"] == 68
    assert detail["commit_hash"] == "b811f1d"
    assert detail["dirty"] is False
    assert detail["checked_binary"] == "open5gs-amfd"


@pytest.mark.unit
def test_dirty_dev_build(bin_dir, monkeypatch, make_proc):
    touch_binary(bin_dir)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: make_proc(stdout="Open5GS v2.8.0-68-gb811f1d+\n"),
    )
    detail = unwrap(open5gs_version())
    assert detail["dirty"] is True
    assert detail["commits_since_tag"] == 68


@pytest.mark.unit
def test_exact_tag_no_git_suffix(bin_dir, monkeypatch, make_proc):
    touch_binary(bin_dir)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: make_proc(stdout="Open5GS v2.8.0\n"),
    )
    detail = unwrap(open5gs_version())
    assert detail["version"] == "2.8.0"
    assert detail["tag"] == "v2.8.0"
    assert detail["commits_since_tag"] is None
    assert "commit_hash" not in detail
    assert detail["dirty"] is False


@pytest.mark.unit
def test_falls_back_to_next_binary_when_amf_missing(bin_dir, monkeypatch, make_proc):
    touch_binary(bin_dir, "open5gs-smfd")
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: make_proc(stdout="Open5GS v2.8.0\n"),
    )
    detail = unwrap(open5gs_version())
    assert detail["checked_binary"] == "open5gs-smfd"


@pytest.mark.unit
def test_no_binaries_found(bin_dir):
    detail = unwrap(open5gs_version())
    assert detail["ok"] is False
    assert "No open5gs-*d binaries found" in detail["error"]


@pytest.mark.unit
def test_unparseable_output(bin_dir, monkeypatch, make_proc):
    touch_binary(bin_dir)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: make_proc(stdout="garbage\n"),
    )
    detail = unwrap(open5gs_version())
    assert detail["ok"] is False
    assert "Could not parse version" in detail["error"]


@pytest.mark.unit
def test_subprocess_failure(bin_dir, monkeypatch):
    touch_binary(bin_dir)

    def _raise(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(mod.subprocess, "run", _raise)
    detail = unwrap(open5gs_version())
    assert detail["ok"] is False
    assert "Failed to run open5gs-amfd -v" in detail["error"]


@pytest.mark.unit
def test_summary_mentions_version(bin_dir, monkeypatch, make_proc):
    touch_binary(bin_dir)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: make_proc(stdout="Open5GS v2.8.0-68-gb811f1d\n"),
    )
    result = open5gs_version()
    assert "2.8.0" in result["summary"]
    assert "68" in result["summary"]
