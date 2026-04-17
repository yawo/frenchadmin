#!/usr/bin/env python3

"""Benchmark helper for pipeline performance gates.

Runs a fixed command multiple times, reads generated perf JSON reports, and
compares key metrics against an optional baseline report.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from statistics import mean


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_latest_reports(reports_dir: str, run_prefix: str, limit: int) -> list[str]:
    files = [
        os.path.join(reports_dir, f)
        for f in os.listdir(reports_dir)
        if f.startswith(run_prefix) and f.endswith(".json")
    ]
    files.sort(key=os.path.getmtime, reverse=True)
    return files[:limit]


def _run_once(command: list[str], env: dict):
    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Benchmark command failed with exit code {completed.returncode}")


def _stage_mean(reports: list[dict], stage: str) -> float:
    values = [r.get("stage_seconds", {}).get(stage, 0.0) for r in reports]
    return mean(values) if values else 0.0


def _metric_mean(reports: list[dict], metric: str) -> float:
    values = [r.get(metric, 0.0) for r in reports]
    return mean(values) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pipeline benchmark and optional regression gate")
    parser.add_argument("--command", required=True, help="Shell command to benchmark")
    parser.add_argument("--reports-dir", default="data/perf_reports", help="Telemetry report directory")
    parser.add_argument("--run-prefix", default="process_", help="Report filename prefix")
    parser.add_argument("--runs", type=int, default=3, help="Measured run count")
    parser.add_argument("--baseline", help="Optional baseline JSON report")
    parser.add_argument("--regression-threshold", type=float, default=0.10, help="Allowed slowdown ratio")
    args = parser.parse_args()

    if args.runs < 1:
        raise ValueError("--runs must be >= 1")

    env = os.environ.copy()
    env.setdefault("ENABLE_PERF_TELEMETRY", "true")

    command = shlex.split(args.command)

    # Warm-up run (excluded from metrics)
    _run_once(command, env=env)

    for _ in range(args.runs):
        _run_once(command, env=env)

    report_paths = _find_latest_reports(args.reports_dir, args.run_prefix, args.runs)
    if len(report_paths) < args.runs:
        raise RuntimeError(
            f"Expected {args.runs} report(s), found {len(report_paths)} in {args.reports_dir}"
        )

    reports = [_load_json(path) for path in report_paths]
    summary = {
        "runs": args.runs,
        "total_wall_time_sec_mean": _metric_mean(reports, "total_wall_time_sec"),
        "docs_per_sec_mean": _metric_mean(reports, "docs_per_sec"),
        "chunks_per_sec_mean": _metric_mean(reports, "chunks_per_sec"),
        "embedding_stage_sec_mean": _stage_mean(reports, "embedding"),
        "postgres_insert_stage_sec_mean": _stage_mean(reports, "postgres_insert"),
        "graph_upsert_stage_sec_mean": _stage_mean(reports, "graph_upsert"),
    }

    print(json.dumps(summary, indent=2))

    if args.baseline:
        baseline = _load_json(args.baseline)
        baseline_total = baseline.get("total_wall_time_sec", 0.0)
        if baseline_total > 0:
            delta = (summary["total_wall_time_sec_mean"] - baseline_total) / baseline_total
            if delta > args.regression_threshold:
                print(
                    (
                        f"Regression gate failed: total wall time degraded by {delta:.2%}, "
                        f"threshold is {args.regression_threshold:.2%}."
                    ),
                    file=sys.stderr,
                )
                return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
