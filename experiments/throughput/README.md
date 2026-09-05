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

These implementations isolate costs:

| Mode | Behavior |
| --- | --- |
| `bare-stateless` | Native celld JavaScript HTTP handler, no Python/state/replay |
| `python-durable` | Unmodified SDK, serialized interpreter per cell, durable receipts |
| `python-no-receipts` | Diagnostic: same Python/cell routing and serialization, no storage |
| `python-concurrent` | Diagnostic: no storage, overlapping async calls per interpreter in cells |
| `python-stateless` | Diagnostic: Python directly in the native stateless pool; no cell routing, ownership, state, or receipts |

The diagnostics modify only generated benchmark projects. They remove replay
recovery; they reject state writes and are **not shipped runtime modes**. The
concurrent case is an experiment in stateless async isolation, not a guarantee
that arbitrary user libraries or application globals are concurrency safe.
Optional `bare-cell` and `bare-write` controls isolate cell routing and SQLite writes.

A Go standard-library load generator uses HTTP/1.1 keep-alive, unique call IDs and
arguments, balanced distribution over all 16 SDK stateless slots, and no retries.
At 16+ clients, each client stays on one slot so outstanding work stays balanced;
below 16, clients rotate through slots. Admission failures are retained separately
and excluded from successful capacity results. Confirmation failures fail the run.
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

The driver's cell tests default to at most 512 clients (32 per cell), leaving
headroom below celld's 64-request admission ceiling. A response can arrive before
the background event driver drops its admission permit. A 1,024-client cell
screen passed but later returned 503 during confirmation; that setting is not
reported as reliable capacity. Stateless-pool tests still reach 1,024 clients.

The `benchmark-throughput-final` label compares baseline/current durable Python,
direct stateless Python, and bare JavaScript with this headroom. It reuses the
identical native binary artifact built and tested in run `33988408853`.

The `benchmark-throughput-tuned` label compares the SDK before and after cached
Pydantic/DI metadata on the same runner. `baseline-durable` and
`baseline-concurrent` embed `celld/app.py` from immutable commit
`9e26fa34c5e5611801d3203507e1c750d6e8a60d` in the otherwise identical generated
project. The matched `python-*` cases use current code. The cache changes neither
validation nor replay guarantees; it avoids rebuilding schemas and inspecting
static function signatures for every request.

The `benchmark-throughput-stateless` label compares the current dispatcher in
concurrent cells against the native stateless pool and bare JavaScript on one
runner per upstream delay. `python-stateless` retains the same Python function,
validation, DI, middleware, context and wire format, but boots one interpreter
per app/scope in each native stateless isolate. It has zero Python cells, does
not persist replay receipts, and rejects keyed state. The native stateless pool
uses celld's CPU-based default size; the cell packing limit does not apply to
this mode. All of this routing remains inside the benchmark builder.

The `benchmark-throughput-typescript` label runs Python, TypeScript, and the bare
JavaScript control on the same native stateless pool and runner per delay.
`typescript/worker.ts` uses pinned Zod 4.5.4 input, upstream, context/client, and
output schemas, middleware, and a per-invocation dependency cache. Quote constraints,
default quantity, pricing request, response shape, and trace correlation match the
Python workload. Hello uses a string argument/result in both languages. Zod and
Pydantic have different coercion rules; the measured inputs are valid for both.
TypeScript is bundled into JavaScript by esbuild; celld executes it in V8 without
Node.js. This is a matched application implementation, not a TypeScript SDK.
No receipts or state are used by either language. The minimal bare control omits
these validation/middleware/DI layers. The native binary is reused from run
`33988408853`. Run `npm ci && npm test` in `typescript/` for correctness checks.
