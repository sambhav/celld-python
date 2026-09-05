# Measured results

## Initial default-pool sweep

[GitHub run](https://github.com/sambhav/celld-python/actions/runs/33978240607) · source `eb74423ad519ef874c1721724ddfdcd8fc0b0649` · [summary JSON](2026-09-05-short-sweep.json)

One Ubuntu 24.04 runner with 4 logical CPUs (AMD EPYC 9V74), celld 0.4.0, Pyodide 314.0.6 and the local SQLite development object store. The stateless isolate limit uses celld’s default; V8 heap limit is 256 MiB per isolate. Compare configurations within this run, not raw times across different GitHub runners.

This initial sweep uses three 3-second samples, one untimed call per key, and a 100,000-iteration Python CPU calculation. It is a short exploratory measurement. A separate longer sweep uses more warmup, a million iterations and 10-second samples.

| Workload, independent keys | 1 worker | 2 workers | 4 workers | 8 workers | 8-worker speedup |
|---|---:|---:|---:|---:|---:|
| Small state update | 96.3 | 176.2 | 301.2 | 418.8 | 4.35× |
| Python CPU calculation | 35.7 | 42.7 | 47.4 | 48.7 | 1.36× |
| 50 ms async wait | 14.7 | 30.1 | 59.1 | 116.2 | 7.90× |

Values are median successful requests/second. Async waiting scales almost linearly here; Python CPU work does not. The shared-key async control changes only from 14.8 to 16.0 requests/second with 1 to 8 clients. Same-key calls serialize even while the function awaits; the small-call control can still benefit from pipelining outside the cell.

The upstream [`place_cell` policy](https://github.com/denoland/celld/blob/a52f9905425bc41134d817694bdc2c50bcc5e856/crates/logic/isolate.rs#L248) packs cells into the fullest non-full V8 isolate. The [per-isolate limit is 32 cells](https://github.com/denoland/celld/blob/a52f9905425bc41134d817694bdc2c50bcc5e856/crates/celld/runtime.rs#L297), and placement sticks for a cell’s lifetime. Adding 1–8 keys therefore does not necessarily spread Python execution across cores. The process sweep separately tests multiple celld processes on the same CPU allocation.

## Rust bridge decision

Keep the Rust/Pyodide bridge experimental. Paired runs show no meaningful latency advantage, and it still requires Pyodide’s JavaScript loader while adding another WASM module and generated bindings. This is evidence about this bridge, not a claim that native Rust execution is slow.

| App | JS warm median / p95 (ms) | Rust warm median / p95 (ms) |
|---|---:|---:|
| hello | 10.11 / 12.48 | 10.24 / 12.88 |
| numpy | 10.59 / 12.32 | 10.33 / 12.36 |
| counter | 10.62 / 12.15 | 10.39 / 12.10 |

Each app/backend has 400 warm calls over four alternating rounds. These are complete localhost calls, including validation, receipts and durability. The bridge adds 230,830 bytes of WASM and 26,775 bytes of JavaScript.

## Idle memory

[GitHub diagnostic](https://github.com/sambhav/celld-python/actions/runs/33979877070) · [raw checkpoints](2026-09-05-memory.json)

This is a separate runner and one diagnostic sample per app. Every idle checkpoint asserts zero resident Python cells. A 2-second idle policy evicts the cell, but even after another 15 seconds process memory remains well above its pre-call baseline. The measurement establishes retained memory over that interval; it does not establish a leak or its cause.

| App | Baseline RSS | Active RSS | RSS 15 s after eviction | Allocator in-use after 15 s |
|---|---:|---:|---:|---:|
| hello | 174.9 MiB | 474.9 MiB | 455.7 MiB | 444.7 MiB |
| numpy | 240.9 MiB | 529.0 MiB | 506.5 MiB | 496.2 MiB |

Wake requests succeed and rebuild the interpreter. The broader lifecycle test also checks that a counter continues from persisted state after eviction. Worker eviction does not stop host machines; this SDK does not implement machine autoscaling.

## Scope of these numbers

- No remote S3 was used. The local development store substitutes for S3; production network/storage latency remains unmeasured.
- Increasing clients, keys or processes on one runner does not add physical CPU resources. No multi-machine linear-scaling claim is made.
- Every measured reply is validated and state totals must be consecutive. No replayed calls, failed requests or lost increments are counted as successful throughput.
- The async workload is a timer, not a benchmark of an external network service.
- Early runs capped stateless isolates at one. They are excluded from the default-pool table above.
- These source revisions predate the package/import rename to `celld` and CLI rename to `pycelld`. The rename has separate framework, actual-celld and installed-wheel verification.
