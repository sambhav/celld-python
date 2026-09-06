# Interpreter snapshots and isolate scaling — 2026-09-06

[GitHub run](https://github.com/sambhav/celld-python/actions/runs/34017054806); SDK `88cf37f969411f14ea0866273eb1b6f1543e672a`, native `1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec`.

One Ubuntu 24.04 runner: AMD EPYC 9V74, 2 physical cores / 4 logical CPUs. Three rounds per case. Local SQLite development object store; these numbers exclude remote S3 download/network costs. Native readiness is measured separately from the first Python request. No request retries; all replies checked.

| Cold workload | No snapshot, first request | Snapshot, first request |
|---|---:|---:|
| hello | 3362 ms | 1518 ms |
| numpy | 4211 ms | 2358 ms |
| state | 3475 ms | 1597 ms |

Stateful wake after confirmed idle eviction: 2768 ms without snapshots, 1020 ms with snapshots. Each idle sample confirmed zero active cells and retained state after waking. This does not mean the whole native process or its base isolate pool scales to zero memory.

Native readiness medians were about 4.2 seconds in both arms; individual samples ranged down to 0.25 seconds. Reported end-to-end cold time includes that separate component. Do not read the first-request improvement as a subsecond process startup claim.

| Warm workload | Snapshot | 1 isolate | 2 isolates | 4 isolates |
|---|---|---:|---:|---:|
| hello | no | 1,666 req/s | 2,708 req/s | 4,231 req/s |
| hello | yes | 1,632 req/s | 2,800 req/s | 4,415 req/s |
| io | no | 736 req/s | 1,224 req/s | 2,001 req/s |
| io | yes | 737 req/s | 1,276 req/s | 2,010 req/s |

Warm rows are medians of three 10-second rounds with 256 keep-alive clients, the supported bounded Python worker pool, and unique call IDs. IO makes a real loopback request to a Go upstream with a 10 ms delay; upstream request/completion counts matched every successful response. Every warm sample had zero errors. Four isolates on two physical cores give roughly 2.7× hello/IO throughput over one, not linear 4× scaling. This is a single-node isolate experiment, not a multi-node fleet scaling result.

Snapshots mainly shorten startup: warm differences are small enough that they should not be advertised as a sustained throughput optimization. RSS is higher: four-isolate hello medians were 789 MiB without snapshots versus 1,205 MiB with snapshots; IO was 862 versus 1,282 MiB. RSS includes the process and native/JS/WASM allocations; these samples do not prove a leak or quantify per-request memory.

The snapshot covers the interpreter and selected portable stdlib imports, not application imports or third-party DSOs. These measurements exercise the Python SDK backend; they do not benchmark the newer built-in Cloudflare dispatcher or separate-artifact loader. Keep older TypeScript comparisons separate: those runs used different CPU models and an earlier experimental host.

The [raw report](2026-09-06-snapshots.json) includes all samples, latency percentiles, RSS, CPU metadata and binary checksum.
