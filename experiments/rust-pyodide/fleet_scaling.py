"""Compare 1/2/4 celld processes sharing one development object store.

Eight independent keys and eight clients are constant. Only node count changes.
All nodes remain on the same fixed GitHub CPU allocation; this does not model
adding physical machines or the latency of a remote S3 service.
"""
import argparse
import json
import os
import shutil
import statistics
import uuid
from contextlib import ExitStack

from bridge import HERE
from compare import ROOT, timed
from harness import measured_node
from scaling import SOURCE, scenario
from celld.build import build, lock
from test_celld import publish_local


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--nodes", type=int, nargs="+", default=[1,2,4])
    parser.add_argument("--check", action="store_true", help="Verify peer routing/state only; do not collect performance samples")
    args = parser.parse_args()
    if args.seconds < 1 or args.rounds < 1 or any(n not in (1,2,4) for n in args.nodes):
        parser.error("Use positive rounds, at least one second, and node counts 1, 2 or 4")
    run_id = uuid.uuid4().hex[:12]
    target = HERE / "build" / ("fleet-scaling-app-" + run_id)
    (target / "src").mkdir(parents=True, exist_ok=True)
    (target / "src/app.py").write_text(SOURCE)
    (target / "pyproject.toml").write_text('[project]\nname="fleet-scaling"\nversion="0.1.0"\ndependencies=[]\n')
    shutil.copytree(ROOT / "examples/.celld-python/cache", target / ".celld-python/cache", dirs_exist_ok=True)
    lock(target)
    report = {"celld":"0.4.0","cpu_count":os.cpu_count(),"cpu_affinity":sorted(os.sched_getaffinity(0)),
              "store":"one shared local SQLite development object store", "samples":[]}
    for round_ in range(args.rounds):
        widths = args.nodes if round_ % 2 == 0 else list(reversed(args.nodes))
        for width in widths:
            # Each cohort starts with its own store, avoiding predecessor logs
            # and expired peer records from an earlier node-count scenario.
            project = build(target, HERE / "build" / f"fleet-project-{run_id}-{round_}-{width}")
            publish_local(project, HERE / "build" / f"fleet-publish-{round_}-{width}.log")
            with ExitStack() as stack:
                nodes = [stack.enter_context(measured_node(project, HERE / "build" / f"fleet-{round_}-{width}-{i}.log",
                    node_suffix=None if i == 0 else str(i))) for i in range(width)]
                # Access one key through every node; independent stores would
                # incorrectly return 1 every time. This also checks peer routing.
                key = uuid.uuid4().hex
                for i, node in enumerate(nodes):
                    _, result = timed(node.port, "/small", {"key":key}, uuid.uuid4().hex)
                    assert result == {"total":i+1,"value":0}, result
                if args.check:
                    print(f"Verified one state key through {width} nodes sharing one store", flush=True)
                    continue
                result = scenario(nodes, "cpu", 8, "independent", args.seconds)
                result.update(nodes=width, round=round_)
                report["samples"].append(result)
                (HERE / "build/fleet_scaling.json").write_text(json.dumps(report, indent=2) + "\n")
                print(f"fleet round {round_} nodes={width}: {result['rps']:.1f} rps, CPU cores {result['cpu_cores_used']:.2f}, {result['latency']}", flush=True)
    if args.check:
        return
    baseline = statistics.median(r["rps"] for r in report["samples"] if r["nodes"] == min(args.nodes))
    report["summary"] = []
    for width in args.nodes:
        rows = [r for r in report["samples"] if r["nodes"] == width]
        rps = statistics.median(r["rps"] for r in rows)
        report["summary"].append({"nodes":width,"median_rps":rps,"speedup":rps/baseline,
            "cpu_cores_used":statistics.median(r["cpu_cores_used"] for r in rows),
            "rss_bytes":statistics.median(r["rss_bytes"] for r in rows)})
    (HERE / "build/fleet_scaling.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
