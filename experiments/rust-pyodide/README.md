# Rust / Pyodide experiment

This is an opt-in experiment; the shipped SDK has no Rust build dependency.
Rust WASM imports the Pyodide loader, mounts the locked sources, owns Python
proxies, awaits invocations, and checks replies. It works on stock celld 0.4.0.
This does **not** statically link CPython into Rust or remove Pyodide's JS loader.

The adoption criterion is a measured speed improvement or a simpler runtime.
The paired GitHub measurements show no meaningful latency improvement, so this
bridge stays experimental. See [measured results and limits](../results) for
the worker-scaling, lifecycle and memory evidence.

## Reproduce

Use the normal checkout prerequisites plus Rust 1.98.1,
`rustup target add wasm32-unknown-unknown`, and wasm-bindgen CLI 0.2.128.
The workflow pins and verifies the official CLI archives.
Run it manually from GitHub Actions, or add the `benchmark` label to a PR.
Remove and re-add the label to measure a later revision. Benchmarking is explicit
so documentation commits do not repeat the long performance sweep; ordinary
correctness tests still run on every PR update. The separate idle-memory workflow
uses the `benchmark-memory` label.

```sh
pycelld lock examples/hello
pycelld lock examples/fleet.toml
CELLD_E2E=1 pytest experiments/rust-pyodide/test_runtime.py -q
python experiments/rust-pyodide/compare.py --rounds 4 --calls 100
python experiments/rust-pyodide/lifecycle.py --rounds 3
python experiments/rust-pyodide/scaling.py --seconds 10 --rounds 3
python experiments/rust-pyodide/fleet_scaling.py --seconds 10 --rounds 3
```

Both backends run sequentially on the same GitHub runner, alternating order.
They use the same Python sources, package lock, host and local SQLite development
store. Every measured call executes; receipts are never replayed. Stateless calls
use one stable slot. Raw samples, CPU/RAM details and celld logs are artifacts.
Runner hardware can differ between jobs, so compare within a run.

The comparison's cold timings are each app's first request in a process; later
apps reuse the already compiled core. The separate lifecycle benchmark starts
a fresh process for each app and requires public health readiness before timing
the first call. Warm
timings include localhost HTTP, Python, SQLite state/receipts and the response
durability gate. They are not a Python-only microbenchmark. The Rust experiment
also validates replies in Rust, so it is an implementation comparison, not an
isolated measurement of the cost of crossing the WASM boundary.

The local store substitutes for S3; these numbers do not establish production
S3 latency or cross-node scaling throughput. Celld's idle eviction can release
worker cells while a host remains running; fleet machines are provisioned by
the embedding platform.

Lifecycle measurements prove inactivity through the operator census, then verify
the next counter increment continues from durable state. They record RSS before
and after eviction; zero resident cells does not imply zero process memory.
Source updates use the SDK's real rebuild/publish/restart supervisor and validate
the new result and preserved state. These are local developer reload timings,
not an in-place production rollout measurement.

The scaling sweep warms workers before timing, alternates concurrency order,
and measures 1/2/4/8 clients with independent keys and one shared-key control.
It includes small state mutations, a million-iteration Python CPU calculation,
and a 50 ms async wait. Four untimed calls per key precede each 10-second sample.
All responses
must be successful, with no replay or lost/duplicated state increments. Samples
include throughput, latency, celld process CPU and RSS. Cells are evicted between
scenarios so earlier workers do not accumulate and change placement. This runs
on one fixed CPU allocation: it tests worker concurrency, not adding machines.
Current runs use celld's default stateless isolate limit (available CPUs), with
a 256 MiB V8 heap limit per isolate. Runs before this correction explicitly used
one stateless isolate and must not be presented as default-pool scaling results.

The node sweep holds eight clients and eight independent keys constant while
varying celld process count (1/2/4). Each cohort gets a fresh development object
store shared by its nodes, and separate local replication directories. Accessing
one key through every node must produce consecutive increments before timing.
The same runner CPU allocation is shared by all processes; this is not a
multi-machine benchmark or an S3 latency claim. The shorter `--check --nodes 2
--rounds 1` mode verifies peer routing without collecting performance samples.
