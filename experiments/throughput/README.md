# Warm throughput and HTTP I/O

Run `go test -race ./...` in `load/`, then run `python experiments/throughput/measure.py`
with the density-enabled celld fork binary on PATH. The GitHub workflow is enabled
by the `benchmark-throughput` PR label. All performance measurements run there;
`--smoke` is exclusively a local correctness check.

The workload is either a tiny hello function or `io_app.py`: a quote service with
Pydantic argument/result validation, dependency injection, request context,
middleware, and an awaited HTTP JSON pricing/inventory lookup. The real upstream
HTTP server adds a controlled 10 or 50 ms response delay. This is a reproducible
I/O service model, not a measurement against an external production API.

Four implementations isolate costs:

| Mode | Behavior |
| --- | --- |
| `bare-stateless` | Native celld JavaScript HTTP handler, no Python/state/replay |
| `python-durable` | Unmodified SDK, serialized interpreter per cell, durable receipts |
| `python-no-receipts` | Diagnostic: same Python/cell routing and serialization, no storage |
| `python-concurrent` | Diagnostic: no storage, overlapping async calls per interpreter |

The two diagnostics modify only generated benchmark projects. They remove replay
recovery; they reject state writes and are **not shipped runtime modes**. The
concurrent case is an experiment in stateless async isolation, not a guarantee
that arbitrary user libraries or application globals are concurrency safe.
Optional `bare-cell` and `bare-write` controls isolate cell routing and SQLite writes.

A Go standard-library load generator uses HTTP/1.1 keep-alive, unique call IDs and
arguments, balanced distribution over all 16 SDK stateless slots, and no retries.
It validates every response and rejects errors, replay headers, wrong customer
IDs, wrong prices, or leaked request trace IDs. Upstream request/completion counts
must equal successful client responses. The upstream reports peak active requests.
The load generator has tests for correlation, balanced unique IDs, and rejection
of failed, malformed, incorrect, and replayed responses.

Each mode/density screen and each confirmation starts a fresh node and object
store, warms all 16 cells, and then warms the tested concurrency. The five short
concurrency screens for a mode/density share one warm node. This keeps the 4,096-receipt/cell limit from silently
turning a benchmark into 429s. Startup, bundling, and warmup are outside sampling.
Six-second screens search concurrency; the best concurrency for each mode/density
is then confirmed in three independent 15-second samples with reversed order.
Only confirmation samples contribute to the final summary. Throughput includes
draining in-flight work, and p50/p95/p99 include queueing. This is a closed-loop
capacity test; tail latency is not an open-loop overload/SLO measurement.

All modes/packing settings for a given upstream delay share one GitHub runner.
Delay variants use separate runners; inspect recorded CPU models before comparing
them. CPU usage is reported separately for celld, the load generator, and upstream.
These processes share the runner CPU allocation. The local development object
store is used; there is no remote S3 durability/network latency or multi-host
scaling in these measurements. No TTL, packing default, or production replay
guarantee is silently changed to obtain a higher throughput number.
