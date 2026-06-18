"""Tests for nf_resource_usage tool."""

from unittest.mock import patch, MagicMock

import pytest
import psutil

from tools.nf_resource_usage import nf_resource_usage
from tools.system_health_snapshot import _NFS
from conftest import unwrap


# ── psutil mock helpers ────────────────────────────────────────────────────────

def _make_proc(cpu=5.0, rss_mb=50.0, vms_mb=200.0, mem_pct=1.0, threads=4,
               read_bytes=1024, write_bytes=512):
    proc = MagicMock()
    proc.cpu_percent.return_value = cpu
    mem = MagicMock()
    mem.rss = int(rss_mb * 1024 ** 2)
    mem.vms = int(vms_mb * 1024 ** 2)
    proc.memory_info.return_value = mem
    proc.memory_percent.return_value = mem_pct
    proc.num_threads.return_value = threads
    io = MagicMock()
    io.read_bytes = read_bytes
    io.write_bytes = write_bytes
    proc.io_counters.return_value = io
    return proc


def _make_sys_mem(total_mb=8192.0, available_mb=4096.0, used_mb=4096.0, percent=50.0):
    mem = MagicMock()
    mem.total = int(total_mb * 1024 ** 2)
    mem.available = int(available_mb * 1024 ** 2)
    mem.used = int(used_mb * 1024 ** 2)
    mem.percent = percent
    return mem


def _make_disk_io(read=1000, write=500):
    io = MagicMock()
    io.read_bytes = read
    io.write_bytes = write
    return io


# ── input validation ──────────────────────────────────────────────────────────

@pytest.mark.unit
class TestValidation:
    def test_sample_interval_too_low(self):
        r = unwrap(nf_resource_usage(sample_interval=0.0))
        assert r["ok"] is False

    def test_sample_interval_too_high(self):
        r = unwrap(nf_resource_usage(sample_interval=11.0))
        assert r["ok"] is False

    def test_invalid_nf_name(self):
        r = unwrap(nf_resource_usage(nfs=["bogus"]))
        assert r["ok"] is False
        assert "bogus" in r["error"]

    def test_invalid_nf_in_list(self):
        r = unwrap(nf_resource_usage(nfs=["amf", "fake_nf"]))
        assert r["ok"] is False


# ── happy path ────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestHappyPath:
    @patch("tools.nf_resource_usage.time.sleep")
    @patch("tools.nf_resource_usage.psutil.disk_io_counters")
    @patch("tools.nf_resource_usage.psutil.cpu_percent")
    @patch("tools.nf_resource_usage.psutil.virtual_memory")
    @patch("tools.nf_resource_usage.psutil.Process")
    @patch("tools.nf_resource_usage._get_nf_pid")
    def test_running_nf_has_metrics(
        self, mock_pid, mock_proc_cls, mock_vmem, mock_cpu_pct, mock_disk, mock_sleep
    ):
        mock_pid.return_value = 1234
        mock_proc_cls.return_value = _make_proc()
        mock_vmem.return_value = _make_sys_mem()
        mock_cpu_pct.return_value = 10.0
        mock_disk.return_value = _make_disk_io()

        r = unwrap(nf_resource_usage(nfs=["amf"], sample_interval=0.1))
        assert r["ok"] is True
        nf = r["nfs"]["amf"]
        assert nf["status"] == "running"
        assert nf["pid"] == 1234
        assert "cpu_percent" in nf
        assert "memory" in nf
        assert "threads" in nf

    @patch("tools.nf_resource_usage.time.sleep")
    @patch("tools.nf_resource_usage.psutil.disk_io_counters")
    @patch("tools.nf_resource_usage.psutil.cpu_percent")
    @patch("tools.nf_resource_usage.psutil.virtual_memory")
    @patch("tools.nf_resource_usage.psutil.Process")
    @patch("tools.nf_resource_usage._get_nf_pid")
    def test_not_running_nf(
        self, mock_pid, mock_proc_cls, mock_vmem, mock_cpu_pct, mock_disk, mock_sleep
    ):
        mock_pid.return_value = None
        mock_vmem.return_value = _make_sys_mem()
        mock_cpu_pct.return_value = 5.0
        mock_disk.return_value = _make_disk_io()

        r = unwrap(nf_resource_usage(nfs=["amf"], sample_interval=0.1))
        assert r["ok"] is True
        assert r["nfs"]["amf"]["status"] == "not_running"
        assert r["nfs"]["amf"]["pid"] is None

    @patch("tools.nf_resource_usage.time.sleep")
    @patch("tools.nf_resource_usage.psutil.disk_io_counters")
    @patch("tools.nf_resource_usage.psutil.cpu_percent")
    @patch("tools.nf_resource_usage.psutil.virtual_memory")
    @patch("tools.nf_resource_usage.psutil.Process")
    @patch("tools.nf_resource_usage._get_nf_pid")
    def test_output_schema(
        self, mock_pid, mock_proc_cls, mock_vmem, mock_cpu_pct, mock_disk, mock_sleep
    ):
        mock_pid.return_value = 1234
        mock_proc_cls.return_value = _make_proc()
        mock_vmem.return_value = _make_sys_mem()
        mock_cpu_pct.return_value = 20.0
        mock_disk.return_value = _make_disk_io()

        r = unwrap(nf_resource_usage(nfs=["amf"], sample_interval=0.1))
        assert r["ok"] is True
        for key in ("timestamp", "sample_interval_s", "nfs", "aggregates", "system", "open5gs_share"):
            assert key in r, f"missing key: {key}"

        sys_ = r["system"]
        for key in ("cpu_count_logical", "cpu_percent_used", "memory_total_mb", "memory_used_mb"):
            assert key in sys_

        agg = r["aggregates"]
        for key in ("nfs_running", "total_cpu_percent", "total_rss_mb"):
            assert key in agg

    @patch("tools.nf_resource_usage.time.sleep")
    @patch("tools.nf_resource_usage.psutil.disk_io_counters")
    @patch("tools.nf_resource_usage.psutil.cpu_percent")
    @patch("tools.nf_resource_usage.psutil.virtual_memory")
    @patch("tools.nf_resource_usage.psutil.Process")
    @patch("tools.nf_resource_usage._get_nf_pid")
    def test_sleep_called_with_interval(
        self, mock_pid, mock_proc_cls, mock_vmem, mock_cpu_pct, mock_disk, mock_sleep
    ):
        mock_pid.return_value = None
        mock_vmem.return_value = _make_sys_mem()
        mock_cpu_pct.return_value = 0.0
        mock_disk.return_value = _make_disk_io()

        nf_resource_usage(nfs=["amf"], sample_interval=0.5)
        mock_sleep.assert_called_once_with(0.5)

    @patch("tools.nf_resource_usage.time.sleep")
    @patch("tools.nf_resource_usage.psutil.disk_io_counters")
    @patch("tools.nf_resource_usage.psutil.cpu_percent")
    @patch("tools.nf_resource_usage.psutil.virtual_memory")
    @patch("tools.nf_resource_usage.psutil.Process")
    @patch("tools.nf_resource_usage._get_nf_pid")
    def test_defaults_to_all_nfs(
        self, mock_pid, mock_proc_cls, mock_vmem, mock_cpu_pct, mock_disk, mock_sleep
    ):
        mock_pid.return_value = None
        mock_vmem.return_value = _make_sys_mem()
        mock_cpu_pct.return_value = 0.0
        mock_disk.return_value = _make_disk_io()

        r = unwrap(nf_resource_usage(sample_interval=0.1))
        assert r["ok"] is True
        assert set(r["nfs"].keys()) == set(_NFS)

    @patch("tools.nf_resource_usage.time.sleep")
    @patch("tools.nf_resource_usage.psutil.disk_io_counters")
    @patch("tools.nf_resource_usage.psutil.cpu_percent")
    @patch("tools.nf_resource_usage.psutil.virtual_memory")
    @patch("tools.nf_resource_usage.psutil.Process")
    @patch("tools.nf_resource_usage._get_nf_pid")
    def test_io_unavailable_graceful(
        self, mock_pid, mock_proc_cls, mock_vmem, mock_cpu_pct, mock_disk, mock_sleep
    ):
        mock_pid.return_value = 1234
        proc = _make_proc()
        proc.io_counters.side_effect = psutil.AccessDenied(pid=1234)
        mock_proc_cls.return_value = proc
        mock_vmem.return_value = _make_sys_mem()
        mock_cpu_pct.return_value = 5.0
        mock_disk.return_value = _make_disk_io()

        r = unwrap(nf_resource_usage(nfs=["upf"], sample_interval=0.1))
        assert r["ok"] is True
        nf = r["nfs"]["upf"]
        assert nf["status"] == "running"
        assert "io_note" in nf  # graceful degradation message
