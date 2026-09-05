"""Measure throughput as concurrent Python workers increase on one fixed CPU host.

Independent keys measure worker concurrency. One shared key is the serialized
state control. CPU, async wait, and small state mutations stress different paths.
This does not add host CPU resources and is not a multi-machine fleet benchmark.
"""
import argparse
import json
import os
import shutil
import statistics
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from bridge import HERE
from compare import ROOT, summarize, timed
from harness import measured_node, wait_inactive
from celld_python.build import build, lock
from test_celld import publish_local

SOURCE = '''import asyncio
from celld_python import App, State
from pydantic import BaseModel
app = App
class Counter(BaseModel):
    total: int = 0
@app.function(key="key")
def small(key: str, state: State[Counter]) -> dict:
    state.value.total += 1
    return {"total":state.value.total,"value":0}
@app.function(key="key")
def cpu(key: str, state: State[Counter]) -> dict:
    value = sum(i*i for i in range(1000000))
    state.value.total += 1
    return {"total":state.value.total,"value":value}
@app.function(key="key")
async def waiting(key: str, state: State[Counter]) -> dict:
    await asyncio.sleep(0.05)
    state.value.total += 1
    return {"total":state.value.total,"value":0}
'''


def cpu_seconds(pid):
    # Linux /proc stat fields 14 and 15. The process name may contain spaces.
    tail = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return (int(tail[11]) + int(tail[12])) / os.sysconf("SC_CLK_TCK")


def evict(running):
    scopes = [s for s in running.state()["residents"] if s.startswith("PythonCell:")]
    for scope in scopes:
        with urllib.request.urlopen(f"http://{running.internal}/evict/{scope}", timeout=30) as response:
            assert response.status == 200
    wait_inactive(running, scopes)


def scenario(running, workload, clients, mode, seconds):
    nodes = running if isinstance(running, list) else [running]
    prefix = uuid.uuid4().hex
    keys = [f"{prefix}-{0 if mode == 'shared' else i}" for i in range(clients)]
    unique_keys = set(keys)
    expected = 1000000 * 999999 * 1999999 // 6 if workload == "cpu" else 0
    placements = dict(zip(keys, [nodes[i % len(nodes)].port for i in range(clients)]))
    # Several untimed calls warm Python specialization and the persistence path.
    for key, port in placements.items():
        for count in range(1, 5):
            _, value = timed(port, "/" + workload, {"key":key}, uuid.uuid4().hex)
            assert value == {"total":count,"value":expected}
    barrier = threading.Barrier(clients + 1)
    stop_at = [0.0]

    def drive(key, port):
        observations = []
        barrier.wait(timeout=30)
        while time.perf_counter() < stop_at[0]:
            elapsed, value = timed(port, "/" + workload, {"key":key}, uuid.uuid4().hex)
            assert value["value"] == expected
            observations.append((elapsed, value["total"], key))
        return observations

    # Sampling errors must not leave worker threads stuck at the start barrier.
    cpu_start = sum(cpu_seconds(n.process.pid) for n in nodes)
    with ThreadPoolExecutor(max_workers=clients) as pool:
        futures = [pool.submit(drive, key, nodes[i % len(nodes)].port) for i, key in enumerate(keys)]
        start = time.perf_counter()
        stop_at[0] = start + seconds
        barrier.wait(timeout=30)
        observations = [item for future in futures for item in future.result()]
        elapsed = time.perf_counter() - start
        cpu = sum(cpu_seconds(n.process.pid) for n in nodes) - cpu_start
    # Every result must be a distinct committed increment for its key.
    for key in unique_keys:
        totals = sorted(total for _, total, k in observations if k == key)
        assert totals == list(range(5, len(totals) + 5)), (key, totals)
    states = [n.state() for n in nodes]
    return {"workload":workload,"clients":clients,"mode":mode,"worker_keys":len(unique_keys),
            "requests":len(observations),"elapsed_seconds":elapsed,"rps":len(observations)/elapsed,
            "latency":summarize([latency for latency, _, _ in observations]),
            "latencies_ms":[latency for latency, _, _ in observations],
            "process_cpu_seconds":cpu,"cpu_cores_used":cpu/elapsed,
            "rss_bytes":sum(state["rss_bytes"] for state in states),
            "resident_python_cells":sum(len([s for s in state["residents"] if s.startswith("PythonCell:")]) for state in states)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.seconds < 1 or args.rounds < 1:
        parser.error("Use positive rounds and at least one second per measurement")
    target = HERE / "build/scaling-app"
    (target / "src").mkdir(parents=True, exist_ok=True)
    (target / "src/app.py").write_text(SOURCE)
    (target / "pyproject.toml").write_text('[project]\nname="scaling"\nversion="0.1.0"\ndependencies=[]\n')
    shutil.copytree(ROOT / "examples/.celld-python/cache", target / ".celld-python/cache", dirs_exist_ok=True)
    lock(target)
    project = build(target)
    publish_local(project, HERE / "build/scaling-publish.log")
    report = {"cpu_count":os.cpu_count(),"cpu_affinity":sorted(os.sched_getaffinity(0)),
              "celld":"0.4.0","stateless_isolates":"celld default (available CPUs)",
              "v8_heap_limit_mb":256,"store":"local SQLite development object store","samples":[]}
    with measured_node(project, HERE / "build/scaling-node.log") as running:
        for round_ in range(args.rounds):
            widths = [1,2,4,8] if round_ % 2 == 0 else [8,4,2,1]
            for workload in ("small", "cpu", "waiting"):
                for mode in ("independent", "shared"):
                    for clients in widths:
                        # Clear old resident cells so placement isn't changed by
                        # accumulating workers from earlier scenarios.
                        evict(running)
                        result = scenario(running, workload, clients, mode, args.seconds)
                        result["round"] = round_
                        report["samples"].append(result)
                        (HERE / "build/scaling.json").write_text(json.dumps(report, indent=2) + "\n")
                        print(f"scale round {round_} {workload} {mode} clients={clients}: {result['rps']:.1f} rps, CPU cores {result['cpu_cores_used']:.2f}, {result['latency']}", flush=True)
    summary = []
    for workload in ("small", "cpu", "waiting"):
        for mode in ("independent", "shared"):
            base = statistics.median(r["rps"] for r in report["samples"] if r["workload"] == workload and r["mode"] == mode and r["clients"] == 1)
            for clients in (1,2,4,8):
                rows = [r for r in report["samples"] if r["workload"] == workload and r["mode"] == mode and r["clients"] == clients]
                rps = statistics.median(r["rps"] for r in rows)
                summary.append({"workload":workload,"mode":mode,"clients":clients,"median_rps":rps,
                                "speedup":rps/base,"linear_efficiency":rps/base/clients})
    report["summary"] = summary
    (HERE / "build/scaling.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
