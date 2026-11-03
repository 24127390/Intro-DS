"""Benchmark helper: wall time, peak/average RAM and output size for any pipeline stage.

Two ways to use it:
  1. CLI wrapper (no code changes for the owner of the stage):
       python -m src.utils.benchmark --label m2_parser \
           --output-dir data/processed --n-items 5000 -- python -m src.latexParser.run
  2. Context manager inside Python / a notebook:
       with measure("m2_parser", output_dir="data/processed", n_items=5000) as r:
           run_parser()
       print(r)

RAM is the resident memory (RSS) of the process AND all its child processes,
sampled every `interval` seconds, so a multi-program system is counted as a whole.
Each run is appended to data/processed/benchmarks.csv.
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
RESULTS_PATH = ROOT / "data" / "processed" / "benchmarks.csv"
MIB = 1024 ** 2

FIELDS = [
    "timestamp_utc", "label", "n_items", "wall_time_s", "time_per_item_s",
    "peak_ram_mib", "avg_ram_mib", "output_size_mib", "peak_disk_mib",
    "n_samples", "returncode", "platform",
]


def folder_size(path) -> int:
    """Total size in bytes of a file or of every file under a folder (0 if missing)."""
    path = Path(path)
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


class _Sampler(threading.Thread):
    """Background thread: samples RSS of a process tree (and disk use of a folder)."""

    def __init__(self, pid: int, interval: float, watch_dir):
        super().__init__(daemon=True)
        self.pid, self.interval, self.watch_dir = pid, interval, watch_dir
        self.stop_event = threading.Event()
        self.ram_samples: list[int] = []
        self.peak_disk = 0

    def _tree_rss(self) -> int:
        try:
            root = psutil.Process(self.pid)
            procs = [root] + root.children(recursive=True)
        except psutil.NoSuchProcess:
            return 0
        total = 0
        for p in procs:
            try:
                total += p.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total

    def run(self):
        tick = 0
        while not self.stop_event.is_set():
            self.ram_samples.append(self._tree_rss())
            # disk walk is slower than a RAM read, so do it about every 2 seconds
            if self.watch_dir and tick % max(1, int(2 / self.interval)) == 0:
                self.peak_disk = max(self.peak_disk, folder_size(self.watch_dir))
            tick += 1
            self.stop_event.wait(self.interval)

    def finish(self):
        self.stop_event.set()
        self.join()


def _build_result(label, n_items, wall, sampler, output_dir, returncode) -> dict:
    ram = sampler.ram_samples or [0]
    out_size = folder_size(output_dir) if output_dir else 0
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "label": label,
        "n_items": n_items if n_items is not None else "",
        "wall_time_s": round(wall, 3),
        "time_per_item_s": round(wall / n_items, 4) if n_items else "",
        "peak_ram_mib": round(max(ram) / MIB, 1),
        "avg_ram_mib": round(sum(ram) / len(ram) / MIB, 1),
        "output_size_mib": round(out_size / MIB, 2),
        "peak_disk_mib": round(max(sampler.peak_disk, out_size) / MIB, 2),
        "n_samples": len(ram),
        "returncode": returncode,
        "platform": f"{sys.platform}, {psutil.cpu_count()} CPU, "
                    f"{psutil.virtual_memory().total / 1024**3:.1f} GiB RAM",
    }


def save_result(result: dict, path: Path = RESULTS_PATH) -> None:
    """Append one result row to the CSV (creates the file with a header if needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(result)


@contextmanager
def measure(label: str, output_dir=None, n_items: int | None = None,
            interval: float = 0.1, save: bool = True):
    """Context manager: measures the current process (and its children)."""
    result: dict = {}
    sampler = _Sampler(os.getpid(), interval, output_dir)
    sampler.start()
    start = time.perf_counter()
    try:
        yield result
    finally:
        wall = time.perf_counter() - start
        sampler.finish()
        result.update(_build_result(label, n_items, wall, sampler, output_dir, 0))
        if save:
            save_result(result)


def run_command(command: list[str], label: str, output_dir=None,
                n_items: int | None = None, interval: float = 0.1,
                save: bool = True) -> dict:
    """Run a command as a subprocess and measure it end to end."""
    start = time.perf_counter()
    proc = subprocess.Popen(command, cwd=ROOT)
    sampler = _Sampler(proc.pid, interval, output_dir)
    sampler.start()
    returncode = proc.wait()
    wall = time.perf_counter() - start
    sampler.finish()
    result = _build_result(label, n_items, wall, sampler, output_dir, returncode)
    if save:
        save_result(result)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark any command.")
    ap.add_argument("--label", required=True, help="e.g. m1_crawler, m2_parser")
    ap.add_argument("--output-dir", help="folder whose size is reported")
    ap.add_argument("--n-items", type=int, help="papers processed (for time per item)")
    ap.add_argument("--interval", type=float, default=0.1, help="RAM sampling period (s)")
    ap.add_argument("--no-save", action="store_true", help="do not append to benchmarks.csv")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="after --, the command to run")
    args = ap.parse_args(argv)

    command = [c for i, c in enumerate(args.command) if not (i == 0 and c == "--")]
    if not command:
        ap.error("give the command to run after --")

    result = run_command(command, args.label, args.output_dir, args.n_items,
                         args.interval, save=not args.no_save)
    for key, value in result.items():
        print(f"{key:>16}: {value}")
    return result["returncode"]


if __name__ == "__main__":
    sys.exit(main())