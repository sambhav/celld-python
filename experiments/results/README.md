# Measured results

## Worker scaling on stock celld

[GitHub run](https://github.com/sambhav/celld-python/actions/runs/33979110661) · source `29bac2e239c8464bdca86f43d12a3e7ee4be034d` · [summary JSON](2026-09-05-long-sweep.json)

One Ubuntu 24.04 runner with 4 logical CPUs on 2 physical cores (AMD EPYC 9V74), celld 0.4.0 and Pyodide 314.0.6. Each configuration has three 10-second samples after four untimed calls per key. The CPU handler performs 1,000,000 Python iterations. The stateless isolate limit uses celld’s default; V8 heap limit is 256 MiB per isolate.

**Independent async waits scale approximately linearly; Python CPU work does not scale with these additional cells on one stock process.**

| Workload, independent keys | 1 worker | 2 workers | 4 workers | 8 workers | 8-worker speedup |
|---|---:|---:|---:|---:|---:|
| Small state update | 102.37 | 183.99 | 284.25 | 386.92 | 3.78× |
| Python CPU calculation | 5.36 | 5.51 | 5.59 | 5.49 | 1.03× |
| 50 ms async wait | 13.29 | 28.27 | 59.19 | 114.47 | 8.61× |

Values are median successful requests/second. Speedup divides each median by the one-worker median; it is not a confidence interval. Timer/runner variability and amortized overhead can put the async ratio slightly above worker count; this is not evidence of superlinear compute scaling. The shared-key async control changes only from 13.1 to 15.1 requests/second with 1 to 8 clients. Same-key calls serialize even while the function awaits.

The upstream [`place_cell` policy](https://github.com/denoland/celld/blob/a52f9905425bc41134d817694bdc2c50bcc5e856/crates/logic/isolate.rs#L248) packs cells into the fullest non-full V8 isolate. The [per-isolate limit is 32 cells](https://github.com/denoland/celld/blob/a52f9905425bc41134d817694bdc2c50bcc5e856/crates/celld/runtime.rs#L297), and placement sticks for a cell’s lifetime. Adding 1–8 keys therefore does not necessarily spread Python execution across cores. Packing allows eviction to empty whole heaps; 32 bounds shared heap failures rather than expressing a Python-specific optimum.

## Multiple processes, same host

Eight clients and eight independent CPU keys are held constant. Nodes share one fresh local development object store and use separate replication directories. A preflight accesses one key through every node and requires consecutive increments, proving shared state and peer routing.

| celld processes | Requests/sec | Speedup | Logical CPU cores used | Total RSS (MiB) |
|---:|---:|---:|---:|---:|
| 1 | 5.45 | 1.00× | 1.05 | 941.4 |
| 2 | 10.50 | 1.93× | 2.04 | 1275.2 |
| 4 | 11.42 | 2.10× | 3.88 | 1959.1 |

Two processes nearly double throughput. Four consume almost all four logical CPUs but deliver only 2.10× the one-process throughput. The runner has two physical cores with SMT, so this is not a four-independent-core experiment. CPU sharing and additional runtime/replication overhead can both contribute to the plateau. This result does not measure adding physical machines.

An [optional native density experiment](../packing) tests lowering the per-isolate ceiling while preserving packing and retirement. Its full native build and performance comparison are in progress; no patched-runtime speedup is claimed here.

## Rust bridge decision

Keep the Rust/Pyodide bridge experimental. Paired runs show no meaningful latency advantage, and it still requires Pyodide’s JavaScript loader while adding another WASM module and generated bindings. This is evidence about this bridge, not a claim that native Rust execution is slow.

| App | JS warm median / p95 (ms) | Rust warm median / p95 (ms) |
|---|---:|---:|
| hello | 9.92 / 12.02 | 10.14 / 13.01 |
| numpy | 9.96 / 11.27 | 10.29 / 11.35 |
| counter | 10.33 / 11.34 | 10.52 / 11.39 |

Each app/backend has 400 warm calls over four alternating rounds. These are complete localhost calls, including validation, receipts and durability. The bridge adds 230,830 bytes of WASM and 26,775 bytes of JavaScript.

## Startup, idle wakeup and source changes

The lifecycle test starts a fresh process for each app, waits for public health and operator readiness, and then times its first Python request. A 2-second idle policy is enabled; all three rounds for each app reach zero resident cells, and the counter continues from durable state after wakeup.

| App, shipped JS host | Process to public readiness (s) | First Python request (s) | Request after idle eviction (s) |
|---|---:|---:|---:|
| hello | 4.21 | 3.51 | 3.00 |
| numpy | 4.21 | 4.51 | 3.98 |
| counter | 4.21 | 3.55 | 3.02 |

Local source edits took median **8.68 s to the SDK’s ready message** and **12.22 s to the first new result**. The first new request itself took 3.61 s. The test checks the new code result and state continuity. This includes SDK rebuild/publication/process restart, not an in-place production S3 rollout. The SDK ready marker denotes a listening process; the fresh-process lifecycle test separately requires native public health readiness.

Native fleet readiness and supervisor publication timing vary by run; earlier source reloads took roughly 8–12 seconds to a new result. Backend differences in that timing are not attributed to the Rust bridge.

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
- Early runs capped stateless isolates at one. They are excluded from the default-pool table above. The [earlier short default-pool sweep](2026-09-05-short-sweep.json) used 3-second samples and a smaller CPU calculation; it supports the qualitative split but is not pooled with these samples.
- These source revisions predate the package/import rename to `celld` and CLI rename to `pycelld`. The rename has separate framework, actual-celld and installed-wheel verification.
