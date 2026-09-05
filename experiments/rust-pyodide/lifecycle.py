"""Measure idle eviction/wakeup, process readiness and the SDK's source reload.

Idle mode is explicitly enabled; celld 0.4.0 disables age-based eviction by
default. Scale to zero means no resident Python cells, not zero host machines.
Source updates use the actual SDK supervisor: build, publish, process restart.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from bridge import HERE, build_rust, compile_runtime
from compare import ROOT, call_ids, summarize, timed
from harness import measured_node, wait_inactive
from celld.build import build, lock
from celld.dev import environment, free_port
from test_celld import publish_local, request


def idle_bench(backend, builder, rounds):
    project = builder(ROOT / "examples/fleet.toml", HERE / "build" / f"idle-{backend}")
    publish_local(project, HERE / "build" / f"idle-{backend}-publish.log")
    records = []
    workloads = [
        ("hello", "/hello/hello", {"name":"Sam"}, {}, "Hello, Sam"),
        ("numpy", "/math/mean", {"values":[1,2,6]}, {}, 3.0),
        ("counter", "/counters/increment", {}, {"x-celld-context":'{"actor":"bench"}'}, None),
    ]
    # One workload per process: eviction cannot be confused with other cells.
    for round_ in range(rounds):
        for name, path, arguments, headers, expected in workloads:
            arguments = dict(arguments)
            if name == "counter":
                arguments["counter_id"] = uuid.uuid4().hex
            ids = call_ids(uuid.uuid4().hex)
            log = HERE / "build" / f"idle-{backend}-{round_}-{name}.log"
            with measured_node(project, log, idle_seconds=2) as running:
                cold, result = timed(running.port, path, arguments, next(ids), headers)
                assert result == expected if name != "counter" else result["total"] == 1
                before = running.state()
                scopes = [scope for scope in before["residents"] if scope.startswith("PythonCell:")]
                assert scopes, before
                inactive = wait_inactive(running, scopes)
                assert not [s for s in inactive["state"]["residents"] if s.startswith("PythonCell:")]
                wake, result = timed(running.port, path, arguments, next(ids), headers)
                assert result == expected if name != "counter" else result["total"] == 2
                after = running.state()
                record = {"backend":backend,"workload":name,"round":round_,"idle_seconds":2,
                          "startup_ms":running.startup_ms,"first_request_ms":cold,"wake_request_ms":wake,
                          "active":before,"inactive":inactive,"awake":after}
                records.append(record)
                print(f"idle {backend} {name}: startup {running.startup_ms:.1f}, first {cold:.1f}, wake {wake:.1f} ms", flush=True)
    return records


SOURCE = '''from celld import App, State
from pydantic import BaseModel
app = App
class Counter(BaseModel):
    total: int = 0
@app.function
def hello() -> str:
    return "VERSION"
@app.function(key="key")
def increment(key: str, state: State[Counter]) -> int:
    state.value.total += 1
    return state.value.total
'''


def wait_ready(process, log, count=1):
    start = time.perf_counter_ns()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if log.read_text().count("ready http") >= count:
            return (time.perf_counter_ns()-start)/1_000_000
        if process.poll() is not None:
            raise RuntimeError(log.read_text()[-6000:])
        time.sleep(.01)
    raise RuntimeError("SDK startup/reload timed out: " + log.read_text()[-6000:])


def reload_bench(backend, rounds):
    project = HERE / "build" / f"reload-{backend}"
    source = project / "src/app.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(SOURCE.replace("VERSION", "v0"))
    (project / "pyproject.toml").write_text('[project]\nname="lifecycle"\nversion="0.1.0"\ndependencies=[]\n')
    shutil.copytree(ROOT / "examples/.celld-python/cache", project / ".celld-python/cache", dirs_exist_ok=True)
    lock(project)
    port = free_port()
    log = HERE / "build" / f"reload-{backend}.log"
    ids = call_ids(uuid.uuid4().hex)
    records = []
    with log.open("w") as output:
        launched = time.perf_counter_ns()
        process = subprocess.Popen([sys.executable, str(HERE / "dev.py"), backend, str(project), str(port)],
                                   env=environment(), stdout=output, stderr=output)
        try:
            wait_ready(process, log)
            initial_ready = (time.perf_counter_ns()-launched)/1_000_000
            first, value = timed(port, "/hello", {}, next(ids))
            assert value == "v0"
            assert timed(port, "/increment", {"key":"persistent"}, next(ids))[1] == 1
            for round_ in range(rounds):
                start = time.perf_counter_ns()
                source.write_text(SOURCE.replace("VERSION", f"v{round_ + 1}"))
                wait_ready(process, log, round_ + 2)
                ready = (time.perf_counter_ns()-start)/1_000_000
                first_new, value = timed(port, "/hello", {}, next(ids))
                assert value == f"v{round_ + 1}", value
                assert timed(port, "/increment", {"key":"persistent"}, next(ids))[1] == round_ + 2
                record = {"backend":backend,"round":round_,"initial_ready_ms":initial_ready,"initial_first_request_ms":first,
                          "edit_to_ready_ms":ready,"first_new_request_ms":first_new,"edit_to_result_ms":ready+first_new}
                records.append(record)
                print(f"reload {backend}: edit to ready {ready:.1f}, first new {first_new:.1f} ms", flush=True)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--backend", choices=["javascript", "rust", "both"], default="both")
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    if args.backend != "javascript":
        compile_runtime()
    report = {"celld":"0.4.0","store":"local SQLite development object store","idle":[],"reload":[]}
    for backend, builder in (("javascript", build), ("rust", build_rust)):
        if args.backend not in (backend, "both"):
            continue
        report["idle"].extend(idle_bench(backend, builder, args.rounds))
        report["reload"].extend(reload_bench(backend, args.rounds))
        (HERE / "build/lifecycle.json").write_text(json.dumps(report, indent=2) + "\n")
    for backend in ("javascript", "rust"):
        for workload in ("hello", "numpy", "counter"):
            rows = [r for r in report["idle"] if r["backend"] == backend and r["workload"] == workload]
            if rows:
                print(backend, workload, {key:summarize([r[key] for r in rows]) for key in ("startup_ms", "first_request_ms", "wake_request_ms")})
    print(HERE / "build/lifecycle.json")


if __name__ == "__main__":
    main()
