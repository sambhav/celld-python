"""Check memory after eviction has settled, separately from immediate residency."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "experiments/rust-pyodide"))
from compare import call_ids, timed
from harness import measured_node, wait_inactive
from celld.build import build
from test_celld import publish_local


def snapshot(state):
    return {key:state[key] for key in ("rss_bytes", "in_use_bytes", "occupied", "residents")}


def main():
    directory = HERE / "build"
    project = build(ROOT / "examples/fleet.toml", directory / "project")
    publish_local(project, directory / "publish.log")
    report = {"celld":"0.4.0","pyodide":"314.0.6","cpu":subprocess.check_output(["lscpu"], text=True),
              "run_url":f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'sambhav/celld-python')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
              "head_sha":os.environ.get("BENCH_HEAD_SHA", ""),"samples":[]}
    for name, path, arguments, expected in [
        ("hello", "/hello/hello", {}, "Hello, world"),
        ("numpy", "/math/mean", {"values":[1,2,6]}, 3.0),
    ]:
        with measured_node(project, directory / f"{name}.log", idle_seconds=2) as running:
            baseline = snapshot(running.state())
            ids = call_ids(uuid.uuid4().hex)
            cold, result = timed(running.port, path, arguments, next(ids))
            assert result == expected
            active = snapshot(running.state())
            scopes = [s for s in active["residents"] if s.startswith("PythonCell:")]
            assert scopes
            inactive = wait_inactive(running, scopes)
            checkpoints = []
            start = time.monotonic()
            for seconds in (0,1,5,15):
                time.sleep(max(0, seconds - (time.monotonic()-start)))
                state = snapshot(running.state())
                assert not any(s.startswith("PythonCell:") for s in state["residents"])
                checkpoints.append({"seconds_after_inactive":seconds, **state})
            wake, result = timed(running.port, path, arguments, next(ids))
            assert result == expected
            report["samples"].append({"app":name,"startup_ms":running.startup_ms,"cold_ms":cold,"wake_ms":wake,
                "baseline":baseline,"active":active,"inactive_wait_ms":inactive["wait_ms"],"checkpoints":checkpoints})
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = ["# Memory after idle eviction", "", "One local-store diagnostic per app; all post-eviction checkpoints verify zero Python cells.", "",
        "| App | Checkpoint | RSS (MiB) | Allocator in use (MiB) |", "|---|---|---:|---:|"]
    for sample in report["samples"]:
        for label, state in [("baseline",sample["baseline"]),("active",sample["active"]), *[(f"idle + {s['seconds_after_inactive']} s",s) for s in sample["checkpoints"]]]:
            lines.append(f"| {sample['app']} | {label} | {state['rss_bytes']/2**20:.1f} | {state['in_use_bytes']/2**20:.1f} |")
    markdown = "\n".join(lines) + "\n"
    (directory / "report.md").write_text(markdown)
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(markdown)
    print(markdown)
    print("MEMORY_REPORT=" + json.dumps(report, separators=(",", ":")))


if __name__ == "__main__":
    main()
