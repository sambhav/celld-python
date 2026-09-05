"""Summarize confirmed capacity only; screening peaks are never final results."""
import argparse
import json
import statistics
from pathlib import Path


def summarize(report):
    assert not report["smoke"], "Sandbox smoke tests are not performance evidence"
    rows = [s for s in report["samples"] if s["phase"] == "confirm"]
    assert rows and all(s["errors"] == 0 for s in rows)
    summaries = []
    for mode,density,clients in sorted({(s["mode"],s["density"],s["clients"]) for s in rows}):
        samples = [s for s in rows if (s["mode"],s["density"],s["clients"]) == (mode,density,clients)]
        assert len(samples) >= 3, "Require three independent confirmations"
        item = dict(mode=mode,density=density,clients=clients,n=len(samples),
            rps_min=min(s["rps"] for s in samples),rps_max=max(s["rps"] for s in samples))
        for key in ("rps","p50_ms","p95_ms","p99_ms","rss_bytes","server_cpu_cores","client_cpu_cores"):
            item[key] = statistics.median(s[key] for s in samples)
        if report["io_delay_ms"]:
            assert all(s["upstream"]["requests"] == s["upstream"]["completed"] == s["requests"] for s in samples)
            item["upstream_peak"] = max(s["upstream"]["peak"] for s in samples)
        summaries.append(item)
    return summaries


def markdown(reports):
    lines = ["# Confirmed hello and real HTTP I/O throughput", "",
        "Each row is the median of three independent confirmation samples at the best screened concurrency for that mode and density. All requests succeeded and every reply was correlated with its unique input. Runs use one celld process, a Go keep-alive load generator, and the local development object store. The upstream service and load generator share the runner CPU with celld. These are measured capacities on this allocation, not remote S3 or multi-machine results.", ""]
    for report in sorted(reports,key=lambda r:r["io_delay_ms"]):
        delay = report["io_delay_ms"]
        cpu = next(line.split(":",1)[1].strip() for line in report["cpu"].splitlines() if line.startswith("Model name:"))
        lines += [f"## {'Hello world' if not delay else f'Quote service, {delay} ms upstream delay'}", "",
            f"CPU: {cpu}. [GitHub run]({report['run_url']}). SDK revision `{report['head_sha']}`; native revision `{report['native_sha']}`.", "",
            "| Mode | Packing limit | Clients | Requests/sec | Min–max | p50 ms | p95 ms | p99 ms | celld CPU cores | RSS MiB |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for r in summarize(report):
            packing = "—" if r["mode"] in {"bare-stateless", "python-stateless", "typescript-stateless"} else str(r["density"])
            lines.append(f"| {r['mode']} | {packing} | {r['clients']} | {r['rps']:.1f} | {r['rps_min']:.1f}–{r['rps_max']:.1f} | {r['p50_ms']:.2f} | {r['p95_ms']:.2f} | {r['p99_ms']:.2f} | {r['server_cpu_cores']:.2f} | {r['rss_bytes']/2**20:.1f} |")
        lines.append("")
    lines += ["`baseline-*` embeds the dispatcher before validator/DI metadata caching. `python-durable` is the shipped SDK with caching and its replay guarantees. `python-concurrent` is a benchmark-only stateless experiment that removes receipts and the per-call concurrency gate. `python-stateless` runs Python directly in the native stateless isolate pool, with zero Python cells and no durable replay. Cell-based modes use 16 cells. `bare-stateless` executes JavaScript directly in celld. The concurrent experiment does not provide durable replay and is not a public SDK mode.", "",
        "Receipts are capped at 4,096 per cell for 24 hours (65,536 total across 16 stateless cells). Durable throughput here describes the measured confirmation windows, not an indefinitely sustainable rate after the retained-call capacity is exhausted. No receipt limit or durability guarantee was relaxed for those rows.", "",
        "The quote service validates Pydantic inputs and outputs, resolves a dependency, runs middleware, awaits a real HTTP JSON pricing/inventory lookup, and returns a customer-specific total and request trace. Upstream latency is controlled; no third-party service was load-tested. Tail latencies include queueing under closed-loop load.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    args = parser.parse_args()
    print(markdown([json.loads(path.read_text()) for path in args.reports]))
