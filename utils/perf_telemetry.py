import cProfile
import json
import os
import platform
import pstats
import socket
import time
import tracemalloc
from contextlib import contextmanager
from datetime import datetime, timezone
from io import StringIO

from config import (
    EMBEDDING_MODEL,
    ENABLE_CPROFILE,
    ENABLE_PERF_TELEMETRY,
    ENABLE_TRACEMALLOC,
    PERF_REPORTS_DIR,
    get_logger,
)

logger = get_logger(__name__)


class PerfTelemetry:
    """Collects stage timings and counters, and emits a benchmark JSON report."""

    def __init__(self, run_name: str, enabled: bool = ENABLE_PERF_TELEMETRY):
        self.run_name = run_name
        self.enabled = enabled
        self.stage_seconds = {}
        self.counters = {}
        self.errors = []
        self.retries = 0
        self.started_at = time.perf_counter()
        self.started_iso = datetime.now(timezone.utc).isoformat()
        self._profiler = None

    def _inc(self, key: str, value: int | float = 1):
        self.counters[key] = self.counters.get(key, 0) + value

    def add_counter(self, key: str, value: int | float = 1):
        if self.enabled:
            self._inc(key, value)

    def add_stage_time(self, stage: str, seconds: float):
        """Accumulate externally measured stage durations."""
        if not self.enabled:
            return
        self.stage_seconds[stage] = self.stage_seconds.get(stage, 0.0) + max(0.0, seconds)

    def add_retry(self, value: int = 1):
        if self.enabled:
            self.retries += value
            self._inc("retries", value)

    def add_error(self, stage: str, error: str):
        if not self.enabled:
            return
        self._inc("errors", 1)
        self.errors.append({"stage": stage, "error": error})

    @contextmanager
    def stage(self, name: str):
        if not self.enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.stage_seconds[name] = self.stage_seconds.get(name, 0.0) + elapsed

    def maybe_start_profilers(self):
        if not self.enabled:
            return
        if ENABLE_CPROFILE:
            self._profiler = cProfile.Profile()
            self._profiler.enable()
        if ENABLE_TRACEMALLOC:
            tracemalloc.start()

    def maybe_stop_profilers(self):
        if not self.enabled:
            return {"cprofile": None, "memory": None}

        profile_summary = None
        memory_summary = None

        if self._profiler is not None:
            self._profiler.disable()
            stream = StringIO()
            stats = pstats.Stats(self._profiler, stream=stream).sort_stats("cumtime")
            stats.print_stats(25)
            profile_summary = stream.getvalue()

        if ENABLE_TRACEMALLOC and tracemalloc.is_tracing():
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            memory_summary = {
                "current_bytes": current,
                "peak_bytes": peak,
            }

        return {"cprofile": profile_summary, "memory": memory_summary}

    def finalize(self, metadata: dict | None = None) -> dict:
        elapsed = time.perf_counter() - self.started_at
        report = {
            "run_name": self.run_name,
            "started_at": self.started_iso,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "total_wall_time_sec": elapsed,
            "stage_seconds": self.stage_seconds,
            "counters": self.counters,
            "errors": self.errors,
            "retries": self.retries,
            "host": {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "python_version": platform.python_version(),
            },
            "embedding_model": EMBEDDING_MODEL,
            "metadata": metadata or {},
        }

        docs = self.counters.get("docs_processed", 0)
        chunks = self.counters.get("chunks_produced", 0)
        if elapsed > 0:
            report["docs_per_sec"] = docs / elapsed
            report["chunks_per_sec"] = chunks / elapsed

        return report

    def write_report(self, report: dict, suffix: str = "") -> str | None:
        if not self.enabled:
            return None
        os.makedirs(PERF_REPORTS_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix_text = f"_{suffix}" if suffix else ""
        path = os.path.join(PERF_REPORTS_DIR, f"{self.run_name}{suffix_text}_{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info("Performance report written to %s", path)
        return path
