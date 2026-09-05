"""Compare complete celld requests, alternating backends to reduce ordering bias.

This is a local development-store experiment, not a production throughput claim.
Build/publish/startup are outside timings; cold means the first Python invocation
after node startup. Call IDs target one stable stateless slot and never replay.
"""
import argparse
import json
import platform
import statistics
import sys
import time
import uuid
from pathlib import Path

from bridge import HERE, build_rust, compile_runtime
from celld_python.build import build

ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_celld import node, publish_local, request


def call_ids(prefix):
    n = 0
    while True:
        value = f"{prefix}-{n:08d}"
        n += 1
        slot = 0
        for c in value:
            slot = (slot * 31 + ord(c)) % 16
        if slot == 0:
            yield value


def timed(port, path, args, call_id, headers=None):
    start = time.perf_counter_ns()
    status, body, response_headers = request(port, path, args, headers={"x-celld-call-id":call_id, **(headers or {})})
    elapsed = (time.perf_counter_ns() - start) / 1_000_000
    assert status == 200, (status, body)
    assert not response_headers.get("x-celld-replayed"), "Benchmark must execute, not replay"
    return elapsed, json.loads(body)["result"]


def summarize(values):
    ordered = sorted(values)
    return {"n":len(values), "median_ms":round(statistics.median(values), 3),
            "p95_ms":round(ordered[min(len(ordered)-1, int(len(ordered)*.95))], 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--calls", type=int, default=100)
    parser.add_argument("--out", type=Path, default=HERE / "build/comparison.json")
    args = parser.parse_args()
    if args.rounds < 2 or args.calls < 10:
        parser.error("Use at least two rounds and ten calls per workload")
    compile_runtime()
    projects = {}
    for backend, builder in (("javascript", build), ("rust", build_rust)):
        directory = HERE / "build" / backend
        projects[backend] = builder(ROOT / "examples/fleet.toml", directory)
        publish_local(directory, HERE / "build" / f"{backend}-publish.log")
    samples = []
    for round_ in range(args.rounds):
        order = ("javascript", "rust") if round_ % 2 == 0 else ("rust", "javascript")
        for backend in order:
            with node(projects[backend], HERE / "build" / f"{backend}-{round_}.log") as port:
                ids = call_ids(uuid.uuid4().hex)
                workloads = [
                    ("hello", "/hello/hello", {"name":"Sam"}, {}, lambda result, i: result == "Hello, Sam"),
                    ("numpy", "/math/mean", {"values":list(range(100))}, {}, lambda result, i: result == 49.5),
                    ("counter", "/counters/increment", {"counter_id":uuid.uuid4().hex},
                     {"x-celld-context":'{"actor":"benchmark"}'}, lambda result, i: result["total"] == i + 1),
                ]
                for name, path, arguments, headers, check in workloads:
                    cold, result = timed(port, path, arguments, next(ids), headers)
                    assert check(result, 0), result
                    warm = []
                    for i in range(args.calls):
                        elapsed, result = timed(port, path, arguments, next(ids), headers)
                        assert check(result, i + 1), result
                        warm.append(elapsed)
                    record = {"backend":backend, "round":round_, "workload":name, "cold_ms":cold,
                              "warm_ms":warm, "summary":summarize(warm)}
                    samples.append(record)
                    print(f"{backend} round {round_} {name}: cold {cold:.1f} ms, warm {record['summary']}", flush=True)
    summary = {}
    for backend in projects:
        summary[backend] = {}
        for workload in ("hello", "numpy", "counter"):
            rows = [r for r in samples if r["backend"] == backend and r["workload"] == workload]
            summary[backend][workload] = {"cold":summarize([r["cold_ms"] for r in rows]),
                                         "warm":summarize([v for r in rows for v in r["warm_ms"]])}
    report = {"platform":platform.platform(), "python":platform.python_version(), "rounds":args.rounds,
              "calls_per_round":args.calls, "celld":"0.4.0", "pyodide":"314.0.6", "summary":summary,
              "extra_artifact_bytes":{p.name:p.stat().st_size for p in (HERE / "build").glob("rust_runtime*")},
              "samples":samples}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(args.out)


if __name__ == "__main__":
    main()
