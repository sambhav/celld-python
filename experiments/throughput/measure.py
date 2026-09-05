"""Warm hello throughput, fresh store per sample, checked replies, no retries."""
import argparse
import atexit
import time
import urllib.request
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "experiments/rust-pyodide"), str(ROOT / "tests")]
from harness import measured_node
from test_celld import publish_local
from celld.build import build, lock
from celld.dev import free_port

SOURCE = '''from celld import App
app = App
@app.function
def hello(name: str) -> str:
    return f"Hello, {name}"
'''


def cpu_seconds(pid):
    tail = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(tail[11]) + int(tail[12])) / os.sysconf("SC_CLK_TCK")


def remove_receipts(host):
    """A benchmark-only ablation, never used by the SDK builder/deployer.

    Keep the same cell routing, concurrency gate, interpreter, and wire protocol.
    Remove SQLite initialization, receipt lookup/hash/write, and state reads.
    This explicitly loses retry recovery and must not execute stateful functions.
    """
    start = host.index("    this.initialize();", host.index("  async execute(request)"))
    end = host.index("    this.runtime??=boot", start)
    host = host[:start] + host[end:]
    before = "    const before=this.ctx.storage.sql.exec('SELECT value FROM _python_state WHERE id=1').toArray()[0]?.value??null;"
    assert host.count(before) == 1
    host = host.replace(before, "    const before=null;")
    start = host.index("    // Commit state and receipt")
    end = host.index("    return response(result);", start)
    host = host[:start] + "    if(result.state!==null)throw new Error('Benchmark requires a stateless function');\n" + host[end:]
    return host


def templates(out, modes, upstream_url, baseline_ref):
    projects = {}
    if any(m.startswith(("python", "baseline")) for m in modes):
        app = out / "app"
        (app / "src").mkdir(parents=True)
        (app / "src/app.py").write_text((HERE / "io_app.py").read_text().replace("__UPSTREAM_URL__", upstream_url) if upstream_url else SOURCE)
        (app / "pyproject.toml").write_text('[project]\nname="throughput"\nversion="0.1.0"\ndependencies=[]\n')
        shutil.copytree(ROOT / "examples/.celld-python/cache", app / ".celld-python/cache")
        lock(app)
        project = build(app, out / "python-template")
    for mode in modes:
        target = out / (mode + "-template")
        if mode.startswith(("python", "baseline")):
            shutil.copytree(project, target)
            if mode.startswith("baseline"):
                manifest = target / "manifest.js"
                apps, sdk_json = manifest.read_text().split("\nexport const sdk=", 1)
                sdk = json.loads(sdk_json.strip().removesuffix(";"))
                sdk["celld/app.py"] = subprocess.check_output(["git", "show", baseline_ref + ":celld/app.py"], cwd=ROOT, text=True)
                manifest.write_text(apps + "\nexport const sdk=" + json.dumps(sdk) + ";\n")
            runtime_mode = mode.replace("baseline-", "python-")
            if runtime_mode in {"python-no-receipts", "python-concurrent"}:
                host = target / "host.js"
                source = remove_receipts(host.read_text())
                if runtime_mode == "python-concurrent":
                    gate = 'fetch(request){return this.ctx.blockConcurrencyWhile(()=>this.execute(request)).catch(failure);}'
                    assert source.count(gate) == 1
                    source = source.replace(gate, 'fetch(request){return this.execute(request).catch(failure);}')
                host.write_text(source)
        else:
            target.mkdir()
            (target / "index.js").write_text((HERE / "bare.js").read_text().replace('__MODE__', mode.removeprefix("bare-")).replace('__UPSTREAM_URL__', upstream_url))
            (target / "wrangler.json").write_text(json.dumps(dict(name="throughput", main="index.js", compatibility_date="2026-09-05",
                durable_objects=dict(bindings=[dict(name="CELLS", class_name="BenchCell")]),
                migrations=[dict(tag="v1", new_sqlite_classes=["BenchCell"])])))
        projects[mode] = target
    return projects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=6)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--clients", type=int, nargs="+", default=[1,16,64,256])
    parser.add_argument("--densities", type=int, nargs="+", default=[32,2])
    parser.add_argument("--modes", nargs="+", default=["bare-stateless", "python-durable", "python-no-receipts", "python-concurrent"])
    parser.add_argument("--smoke", action="store_true", help="correctness only; bypass unavailable sandbox /proc, no performance claims")
    parser.add_argument("--io-delay-ms", type=int, default=0)
    parser.add_argument("--confirm-seconds", type=float, default=15)
    parser.add_argument("--confirm-rounds", type=int, default=3)
    parser.add_argument("--baseline-ref", default="9e26fa34c5e5611801d3203507e1c750d6e8a60d")
    args = parser.parse_args()
    import re
    assert re.fullmatch(r"[0-9a-f]{40}", args.baseline_ref)
    assert args.seconds > 0 and args.rounds > 0 and all(c > 0 for c in args.clients)
    assert set(args.modes) <= {"bare-stateless", "bare-cell", "bare-write", "python-durable", "python-no-receipts", "python-concurrent", "baseline-durable", "baseline-concurrent"}
    out = HERE / "build" / uuid.uuid4().hex[:12]
    out.mkdir(parents=True)
    binary = shutil.which("celld")
    assert binary
    if not args.smoke:
        assert "CELLD_MAX_CELLS_PER_ISOLATE" in subprocess.check_output([binary,"--help"], text=True)
    driver = HERE / "build/load"
    subprocess.run(["go", "build", "-o", str(driver), "."], cwd=HERE / "load", check=True)
    upstream_process = None
    upstream_url = ""
    if args.io_delay_ms:
        address = f"127.0.0.1:{free_port()}"
        upstream_process = subprocess.Popen([str(driver), "--serve", address], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atexit.register(upstream_process.terminate)
        stats_url = f"http://{address}/stats"
        upstream_url = f"http://{address}/price?delay_ms={args.io_delay_ms}"
        for attempt in range(100):
            try:
                with urllib.request.urlopen(stats_url) as response: json.load(response)
                break
            except OSError: time.sleep(.05)
        else: raise RuntimeError("Upstream did not start")
    projects = templates(out, args.modes, upstream_url, args.baseline_ref)
    report = dict(run_url=f"https://github.com/{os.environ.get('GITHUB_REPOSITORY','sambhav/celld-python')}/actions/runs/{os.environ.get('GITHUB_RUN_ID','')}",
        head_sha=os.environ.get("BENCH_HEAD_SHA", ""), baseline_ref=args.baseline_ref, native_sha=os.environ.get("CELLD_NATIVE_SHA", ""),
        binary_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        cpu=subprocess.check_output(["lscpu"], text=True), memory=subprocess.check_output(["free","-b"], text=True),
        go=subprocess.check_output(["go","version"], text=True).strip(), profile="lab (thin LTO)",
        store="local SQLite development object store; no remote S3", smoke=args.smoke,
        protocol="HTTP/1.1 keep-alive; closed loop; unique inputs and IDs; every reply checked; no retry/replay",
        slots=16, seconds=args.seconds, rounds=args.rounds, io_delay_ms=args.io_delay_ms, samples=[], rejected=[])
    for round_ in range(args.rounds + args.confirm_rounds):
        phase = "screen" if round_ < args.rounds else "confirm"
        duration = args.seconds if phase == "screen" else args.confirm_seconds
        cases = [(mode,density,c) for mode in args.modes for density in args.densities
                 if mode != "bare-stateless" or density == args.densities[0] for c in args.clients]
        if phase == "confirm":
            groups = {(s["mode"],s["density"]) for s in report["samples"]}
            cases = []
            for mode,density in sorted(groups):
                rows = [s for s in report["samples"] if s["mode"]==mode and s["density"]==density and s["phase"]=="screen"]
                best = max(rows,key=lambda s:s["rps"])
                cases.append((mode,density,best["clients"]))
        if round_ % 2: cases.reverse()
        groups = list(dict.fromkeys((mode,density) for mode,density,_ in cases))
        for mode,density in groups:
            widths = [c for m,d,c in cases if (m,d)==(mode,density)]
            stem = f"{round_}-{mode}-{density}"
            project = out / stem
            shutil.copytree(projects[mode], project, copy_function=os.link)
            publish_local(project, out / (stem + "-publish.log"))
            with measured_node(project, out / (stem + "-node.log"), cell_density=density) as running:
                command = [str(driver),"--url",f"http://127.0.0.1:{running.port}/hello", "--workload", "io" if upstream_url else "hello"]
                # Warm all 16 slots, then the tested connection/concurrency level.
                warm = subprocess.run(command + ["--clients","4","--count","16"], capture_output=True, text=True)
                if warm.returncode: raise RuntimeError(f"{stem} warmup: {warm.stdout} {warm.stderr}")
                for clients in widths:
                    def reject(result, stage):
                        detail = json.loads(result.stdout)
                        record = dict(mode=mode,density=density,clients=clients,round=round_,phase=phase,stage=stage,detail=detail)
                        report["rejected"].append(record)
                        (out / "measurements.json").write_text(json.dumps(report,indent=2)+"\n")
                        print("THROUGHPUT_REJECTED="+json.dumps(record,separators=(",",":")),flush=True)
                        if phase != "screen" or "cell request limit reached" not in detail.get("first_error", ""):
                            raise RuntimeError(f"Failed correctness/confirmation: {record}")
                    warm = subprocess.run(command + ["--clients",str(clients),"--count","2"], capture_output=True, text=True)
                    if warm.returncode:
                        reject(warm,"warmup")
                        break
                    if upstream_process:
                        with urllib.request.urlopen(f"http://{address}/reset") as response: response.read()
                    upstream_cpu = cpu_seconds(upstream_process.pid) if upstream_process and not args.smoke else 0
                    start_cpu = 0 if args.smoke else cpu_seconds(running.process.pid)
                    result = subprocess.run(command + ["--clients",str(clients),"--seconds",str(duration)], capture_output=True, text=True)
                    if result.returncode:
                        reject(result,"measurement")
                        break
                    sample = json.loads(result.stdout)
                    end_cpu = 0 if args.smoke else cpu_seconds(running.process.pid)
                    state = running.state()
                    sample.update(mode=mode, density=density, round=round_, phase=phase,
                        server_cpu_cores=(end_cpu-start_cpu)/sample["elapsed_seconds"], rss_bytes=state["rss_bytes"],
                        resident_cells=len([s for s in state["residents"] if s.startswith(("PythonCell:","BenchCell:"))]))
                    if upstream_process:
                        with urllib.request.urlopen(stats_url) as response: stats = json.load(response)
                        assert stats["completed"] == stats["requests"] == sample["requests"] and stats["active"]==0, (stats,sample)
                        sample.update(upstream=stats, upstream_cpu_cores=(cpu_seconds(upstream_process.pid)-upstream_cpu)/sample["elapsed_seconds"] if not args.smoke else 0)
                    expected_cells = 0 if mode == "bare-stateless" else 16
                    assert sample["resident_cells"] == expected_cells, sample
                    assert sample["errors"] == 0 and sample["requests"] > 0
                    report["samples"].append(sample)
                    (out / "measurements.json").write_text(json.dumps(report, indent=2) + "\n")
                    print("THROUGHPUT_SAMPLE=" + json.dumps(sample, separators=(",",":")), flush=True)
            shutil.rmtree(project)
    report["summary"] = []
    groups = sorted({(s["mode"],s["density"],s["clients"]) for s in report["samples"]})
    for mode,density,clients in groups:
        rows = [s for s in report["samples"] if (s["mode"],s["density"],s["clients"]) == (mode,density,clients) and s["phase"] == ("confirm" if args.confirm_rounds else "screen")]
        if not rows: continue
        summary = dict(mode=mode,density=density,clients=clients,n=len(rows),
            rps_min=min(s["rps"] for s in rows),rps_max=max(s["rps"] for s in rows))
        for key in ("rps","p50_ms","p95_ms","p99_ms","server_cpu_cores","client_cpu_cores","rss_bytes"):
            summary[key] = statistics.median(s[key] for s in rows)
        report["summary"].append(summary)
    (out / "measurements.json").write_text(json.dumps(report, indent=2) + "\n")
    print("THROUGHPUT_REPORT=" + json.dumps(report, separators=(",",":")))


if __name__ == "__main__":
    main()
