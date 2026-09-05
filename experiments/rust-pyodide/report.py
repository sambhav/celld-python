"""Publish a small, reproducible summary beside the raw benchmark samples."""
import json
import os
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent


def median(rows, key):
    return round(statistics.median(row[key] for row in rows), 3)


def main():
    directory = HERE / "build"
    comparison = json.loads((directory / "comparison.json").read_text())
    lifecycle = json.loads((directory / "lifecycle.json").read_text())
    scaling = json.loads((directory / "scaling.json").read_text())
    fleet = json.loads((directory / "fleet_scaling.json").read_text())
    report = {"run_url":f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'sambhav/celld-python')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
              "head_sha":os.environ.get("BENCH_HEAD_SHA", ""), "tested_sha":os.environ.get("GITHUB_SHA", ""),
              "cpu":(directory / "cpu.txt").read_text(), "memory":(directory / "memory.txt").read_text(),
              "latency":comparison["summary"], "extra_artifact_bytes":comparison["extra_artifact_bytes"],
              "startup":{}, "idle":[], "reload":[], "scaling":[],
              "scaling_config":{k:v for k,v in scaling.items() if k not in ("samples", "summary")},
              "fleet_scaling":fleet["summary"]}
    for backend in ("javascript", "rust"):
        rows = [r for r in comparison["startups"] if r["backend"] == backend]
        report["startup"][backend] = {"n":len(rows),"median_ms":median(rows,"startup_ms")}
        for workload in ("hello", "numpy", "counter"):
            rows = [r for r in lifecycle["idle"] if r["backend"] == backend and r["workload"] == workload]
            item = {"backend":backend,"workload":workload,"n":len(rows)}
            for key in ("startup_ms", "first_request_ms", "wake_request_ms"):
                item[key] = median(rows, key)
            item["idle_wait_ms"] = median([r["inactive"] for r in rows], "wait_ms")
            item["resident_cells_after_idle"] = [len(r["inactive"]["state"]["residents"]) for r in rows]
            item["active_rss_bytes"] = median([r["active"] for r in rows], "rss_bytes")
            item["inactive_rss_bytes"] = median([r["inactive"]["state"] for r in rows], "rss_bytes")
            report["idle"].append(item)
        rows = [r for r in lifecycle["reload"] if r["backend"] == backend]
        report["reload"].append({"backend":backend,"n":len(rows), **{key:median(rows,key) for key in (
            "initial_ready_ms", "initial_first_request_ms", "edit_to_ready_ms", "first_new_request_ms", "edit_to_result_ms")}})
    for item in scaling["summary"]:
        rows = [r for r in scaling["samples"] if all(r[k] == item[k] for k in ("workload", "mode", "clients"))]
        report["scaling"].append({**item,"n":len(rows),"median_cpu_cores":median(rows,"cpu_cores_used"),
                                  "median_rss_bytes":median(rows,"rss_bytes")})
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = ["# celld Python runtime benchmarks", "", f"[GitHub run]({report['run_url']}) · source `{report['head_sha']}`", "",
        "One fixed GitHub runner; local SQLite object store, no external S3. Raw samples and logs are attached as artifacts.", "",
        "## Warm calls and first Python requests", "",
        "Per-app first requests; later apps in the process reuse the compiled core. The lifecycle table measures fresh processes.", "",
        "| Backend | App | First request median (ms) | Warm median (ms) | Warm p95 (ms) |",
        "|---|---|---:|---:|---:|"]
    for backend, apps in report["latency"].items():
        for name, values in apps.items():
            lines.append(f"| {backend} | {name} | {values['cold']['median_ms']:.1f} | {values['warm']['median_ms']:.2f} | {values['warm']['p95_ms']:.2f} |")
    lines += ["", "## Idle eviction and wakeup", "", "2-second idle policy; all cases verify eviction and state recovery. Node machines stay running.", "",
        "| Backend | App | Node startup (ms) | First request (ms) | Wake request (ms) | RSS active / idle (MiB) |",
        "|---|---|---:|---:|---:|---:|"]
    for r in report["idle"]:
        lines.append(f"| {r['backend']} | {r['workload']} | {r['startup_ms']:.1f} | {r['first_request_ms']:.1f} | {r['wake_request_ms']:.1f} | {r['active_rss_bytes']/2**20:.1f} / {r['inactive_rss_bytes']/2**20:.1f} |")
    lines += ["", "## Source updates", "", "SDK rebuild/publish/process restart; not an in-place production rollout.", "",
        "| Backend | Edit to ready (ms) | First new request (ms) | Edit to result (ms) |", "|---|---:|---:|---:|"]
    for r in report["reload"]:
        lines.append(f"| {r['backend']} | {r['edit_to_ready_ms']:.1f} | {r['first_new_request_ms']:.1f} | {r['edit_to_result_ms']:.1f} |")
    lines += ["", "## Worker scaling", "", "Independent keys versus one serialized key. More clients do not add host CPUs. Warm workers, no replay, all results checked.", "",
        f"Stateless isolate limit: {scaling.get('stateless_isolates', 'one (legacy harness)')}. V8 heap limit: 256 MiB per isolate.", "",
        "| Work | Key mode | Clients | Requests/sec | Speedup | Efficiency | CPU cores used |", "|---|---|---:|---:|---:|---:|---:|"]
    for r in report["scaling"]:
        lines.append(f"| {r['workload']} | {r['mode']} | {r['clients']} | {r['median_rps']:.1f} | {r['speedup']:.2f}× | {r['linear_efficiency']:.0%} | {r['median_cpu_cores']:.2f} |")
    lines += ["", "## Node scaling on the same host", "",
        "Eight clients and eight independent CPU workers, spread over 1/2/4 celld processes sharing one object store. Host CPU is unchanged; not a multi-machine or remote S3 result.", "",
        "| Nodes | Requests/sec | Speedup | CPU cores used | RSS (MiB) |", "|---:|---:|---:|---:|---:|"]
    for r in report["fleet_scaling"]:
        lines.append(f"| {r['nodes']} | {r['median_rps']:.1f} | {r['speedup']:.2f}× | {r['cpu_cores_used']:.2f} | {r['rss_bytes']/2**20:.1f} |")
    lines += ["", "## Runner", "", "```text", report["cpu"], report["memory"], "```", ""]
    markdown = "\n".join(lines)
    (directory / "report.md").write_text(markdown)
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(markdown)
    print(markdown)
    # Machine-readable summary also survives in job logs if an artifact URL expires.
    print("BENCHMARK_REPORT=" + json.dumps(report, separators=(",", ":")))


if __name__ == "__main__":
    main()
