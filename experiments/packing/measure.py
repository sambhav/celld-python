"""Compare packing limits with identical code, clients, CPU and one patched binary."""
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "experiments/rust-pyodide"))
from harness import measured_node
from scaling import SOURCE, scenario, evict
from celld.build import build, lock
from test_celld import publish_local


def main():
    binary = shutil.which("celld")
    assert binary and "CELLD_MAX_CELLS_PER_ISOLATE" in subprocess.check_output([binary, "--help"], text=True), "Use the cell-density patched celld binary"
    target = HERE / "build" / ("app-" + uuid.uuid4().hex[:12])
    (target / "src").mkdir(parents=True)
    (target / "src/app.py").write_text(SOURCE)
    (target / "pyproject.toml").write_text('[project]\nname="packing"\nversion="0.1.0"\ndependencies=[]\n')
    shutil.copytree(ROOT / "examples/.celld-python/cache", target / ".celld-python/cache")
    lock(target)
    report = {"run_url":f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'sambhav/celld-python')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
        "head_sha":os.environ.get("BENCH_HEAD_SHA", ""), "upstream_sha":"a52f9905425bc41134d817694bdc2c50bcc5e856",
        "profile":"lab (thin LTO); same patched binary for every density", "store":"local SQLite development object store",
        "cpu":subprocess.check_output(["lscpu"], text=True), "memory":subprocess.check_output(["free", "-b"], text=True),
        "clients":8,"keys":8,"cpu_iterations":1000000,"warmup_calls_per_key":4,"seconds_per_sample":10,"samples":[]}
    for round_ in range(3):
        for density in ([32,2,1] if round_ % 2 == 0 else [1,2,32]):
            project = build(target, target / f"project-{round_}-{density}")
            publish_local(project, HERE / "build" / f"publish-{round_}-{density}.log")
            with measured_node(project, HERE / "build" / f"node-{round_}-{density}.log", cell_density=density) as running:
                baseline = running.state()
                result = scenario(running, "cpu", 8, "independent", 10)
                assert result["resident_python_cells"] == 8
                evict(running)
                time.sleep(5)
                inactive = running.state()
                assert not any(s.startswith("PythonCell:") for s in inactive["residents"])
                result.update(round=round_, density=density, startup_ms=running.startup_ms,
                    baseline_rss_bytes=baseline["rss_bytes"], inactive_rss_bytes=inactive["rss_bytes"],
                    inactive_in_use_bytes=inactive["in_use_bytes"])
                report["samples"].append(result)
                (HERE / "build/measurements.json").write_text(json.dumps(report, indent=2) + "\n")
                print(f"packing round {round_} density={density}: {result['rps']:.2f} rps, CPU {result['cpu_cores_used']:.2f} cores, RSS {result['rss_bytes']/2**20:.1f} MiB", flush=True)
    report["summary"] = []
    base = statistics.median(s["rps"] for s in report["samples"] if s["density"] == 32)
    for density in (32,2,1):
        rows = [s for s in report["samples"] if s["density"] == density]
        row = {"density":density,"n":len(rows)}
        for key in ("rps","cpu_cores_used","rss_bytes","baseline_rss_bytes","inactive_rss_bytes","inactive_in_use_bytes"):
            row[key] = statistics.median(s[key] for s in rows)
        row.update(speedup=row["rps"]/base, rps_min=min(s["rps"] for s in rows), rps_max=max(s["rps"] for s in rows))
        report["summary"].append(row)
    (HERE / "build/measurements.json").write_text(json.dumps(report, indent=2) + "\n")
    compact = {k:v for k,v in report.items() if k != "samples"}
    (HERE / "build/report.json").write_text(json.dumps(compact, indent=2) + "\n")
    lines = ["# Cell packing experiment", "", "Eight independent CPU workers and clients; same host and patched lab-profile binary. Local store, no S3.", "",
        "| Cells/isolate | Requests/sec | Speedup | CPU cores | Active RSS (MiB) | Idle RSS after 5 s (MiB) |", "|---:|---:|---:|---:|---:|---:|"]
    for r in compact["summary"]:
        lines.append(f"| {r['density']} | {r['rps']:.2f} | {r['speedup']:.2f}× | {r['cpu_cores_used']:.2f} | {r['rss_bytes']/2**20:.1f} | {r['inactive_rss_bytes']/2**20:.1f} |")
    markdown = "\n".join(lines) + "\n"
    (HERE / "build/report.md").write_text(markdown)
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(markdown)
    print(markdown)
    print("PACKING_REPORT=" + json.dumps(compact, separators=(",", ":")))


if __name__ == "__main__":
    main()
