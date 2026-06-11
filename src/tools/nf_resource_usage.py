"""nf_resource_usage — CPU, RAM, and I/O utilisation per Open5GS NF vs system totals."""

import time
from datetime import datetime, timezone

import psutil

from tools.system_health_snapshot import _NFS, _get_nf_pid
from tools.nf_lifecycle import VALID_NFS

_VALID_MONITOR_NFS = frozenset(VALID_NFS)


def nf_resource_usage(
    nfs: list[str] | None = None,
    sample_interval: float = 1.0,
) -> dict:
    """
    Sample CPU, memory, and I/O for each running Open5GS NF, then compare
    against system-wide totals.

    The tool takes two snapshots separated by `sample_interval` seconds to
    compute per-process CPU % and I/O rates — the call therefore blocks for
    at least that duration.

    Args:
        nfs:             NF names to sample. None = all NFs.
        sample_interval: Sampling window in seconds (0.1 – 10.0, default 1.0).

    Returns:
        {
          "ok": bool,
          "timestamp": ISO-8601 UTC,
          "sample_interval_s": float,
          "nfs": {
            "<name>": {
              "status": "running" | "not_running" | "error",
              "pid": int | None,
              "cpu_percent": float,          # % of one logical CPU core
              "memory": {
                "rss_mb": float,             # resident set size
                "vms_mb": float,             # virtual memory size
                "percent": float,            # % of system RAM
              },
              "io": {
                "read_bytes_per_s": int,
                "write_bytes_per_s": int,
                "read_total_mb": float,      # cumulative since process start
                "write_total_mb": float,
              },
              "threads": int,
            }
          },
          "aggregates": {
            "nfs_running": int,
            "total_cpu_percent": float,
            "total_rss_mb": float,
            "total_io_read_bytes_per_s": int,
            "total_io_write_bytes_per_s": int,
          },
          "system": {
            "cpu_count_logical": int,
            "cpu_count_physical": int,
            "cpu_percent_used": float,
            "memory_total_mb": float,
            "memory_available_mb": float,
            "memory_used_mb": float,
            "memory_percent_used": float,
            "disk_io": {
              "read_bytes_per_s": int,
              "write_bytes_per_s": int,
            },
          },
          "open5gs_share": {
            "cpu_pct_of_system_usage": float | None,  # None if system CPU is 0
            "memory_pct_of_total": float,
          },
        }
    """
    if not (0.1 <= sample_interval <= 10.0):
        return {"ok": False, "error": "sample_interval must be between 0.1 and 10.0 seconds"}

    target_nfs = nfs if nfs else list(_NFS)
    for n in target_nfs:
        if n not in _VALID_MONITOR_NFS:
            return {"ok": False, "error": f"Invalid NF '{n}'. Valid: {sorted(_VALID_MONITOR_NFS)}"}

    # Resolve PIDs
    pid_map: dict[str, int | None] = {nf: _get_nf_pid(nf) for nf in target_nfs}

    # Build psutil.Process objects for running NFs
    procs: dict[str, psutil.Process] = {}
    for nf, pid in pid_map.items():
        if pid is not None:
            try:
                procs[nf] = psutil.Process(pid)
            except psutil.NoSuchProcess:
                pid_map[nf] = None

    # ── First snapshot ──────────────────────────────────────────────────────
    io_t0: dict[str, object] = {}
    for nf, proc in procs.items():
        try:
            proc.cpu_percent(interval=None)  # prime the CPU counter (returns 0 here)
            io_t0[nf] = proc.io_counters()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    psutil.cpu_percent(interval=None)  # prime system CPU counter
    sys_io_t0 = psutil.disk_io_counters()

    time.sleep(sample_interval)

    # ── Second snapshot ─────────────────────────────────────────────────────
    nf_data: dict[str, dict] = {}
    for nf in target_nfs:
        pid = pid_map[nf]
        if pid is None or nf not in procs:
            nf_data[nf] = {"status": "not_running", "pid": None}
            continue

        proc = procs[nf]
        try:
            cpu = proc.cpu_percent(interval=None)
            mem = proc.memory_info()
            mem_pct = proc.memory_percent()
            threads = proc.num_threads()
            io_t1 = proc.io_counters()

            entry: dict = {
                "status": "running",
                "pid": pid,
                "cpu_percent": round(cpu, 2),
                "memory": {
                    "rss_mb": round(mem.rss / 1024**2, 2),
                    "vms_mb": round(mem.vms / 1024**2, 2),
                    "percent": round(mem_pct, 3),
                },
                "threads": threads,
            }

            t0 = io_t0.get(nf)
            if t0 is not None and io_t1 is not None:
                entry["io"] = {
                    "read_bytes_per_s": round((io_t1.read_bytes - t0.read_bytes) / sample_interval),
                    "write_bytes_per_s": round((io_t1.write_bytes - t0.write_bytes) / sample_interval),
                    "read_total_mb": round(io_t1.read_bytes / 1024**2, 2),
                    "write_total_mb": round(io_t1.write_bytes / 1024**2, 2),
                }

            nf_data[nf] = entry

        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            nf_data[nf] = {"status": "error", "pid": pid, "error": str(exc)}

    # ── System totals ───────────────────────────────────────────────────────
    sys_cpu_pct = psutil.cpu_percent(interval=None)
    sys_mem = psutil.virtual_memory()
    sys_io_t1 = psutil.disk_io_counters()

    system: dict = {
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "cpu_percent_used": round(sys_cpu_pct, 2),
        "memory_total_mb": round(sys_mem.total / 1024**2, 2),
        "memory_available_mb": round(sys_mem.available / 1024**2, 2),
        "memory_used_mb": round(sys_mem.used / 1024**2, 2),
        "memory_percent_used": round(sys_mem.percent, 2),
    }

    if sys_io_t0 and sys_io_t1:
        system["disk_io"] = {
            "read_bytes_per_s": round((sys_io_t1.read_bytes - sys_io_t0.read_bytes) / sample_interval),
            "write_bytes_per_s": round((sys_io_t1.write_bytes - sys_io_t0.write_bytes) / sample_interval),
        }

    # ── Aggregates ──────────────────────────────────────────────────────────
    running = {nf: d for nf, d in nf_data.items() if d.get("status") == "running"}
    total_cpu = round(sum(d["cpu_percent"] for d in running.values()), 2)
    total_rss = round(sum(d["memory"]["rss_mb"] for d in running.values()), 2)
    total_io_read = sum(d["io"]["read_bytes_per_s"] for d in running.values() if "io" in d)
    total_io_write = sum(d["io"]["write_bytes_per_s"] for d in running.values() if "io" in d)

    aggregates = {
        "nfs_running": len(running),
        "total_cpu_percent": total_cpu,
        "total_rss_mb": total_rss,
        "total_io_read_bytes_per_s": total_io_read,
        "total_io_write_bytes_per_s": total_io_write,
    }

    open5gs_share: dict = {
        "cpu_pct_of_system_usage": (
            round(total_cpu / sys_cpu_pct * 100, 1) if sys_cpu_pct > 0 else None
        ),
        "memory_pct_of_total": round(
            total_rss / system["memory_total_mb"] * 100, 3
        ),
    }

    return {
        "ok": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sample_interval_s": sample_interval,
        "nfs": nf_data,
        "aggregates": aggregates,
        "system": system,
        "open5gs_share": open5gs_share,
    }
