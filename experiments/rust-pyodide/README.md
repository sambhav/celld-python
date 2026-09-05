# Rust / Pyodide experiment

This is an opt-in experiment; the shipped SDK has no Rust build dependency.
Rust WASM imports the Pyodide loader, mounts the locked sources, owns Python
proxies, awaits invocations, and checks replies. It works on stock celld 0.4.0.
This does **not** statically link CPython into Rust or remove Pyodide's JS loader.

The adoption criterion is a measured speed improvement or a simpler runtime.
Moving a JS call behind a Rust binding is not sufficient by itself. Results and
the resulting decision will be recorded here after the GitHub benchmark runs.

## Reproduce

Use the normal checkout prerequisites plus Rust 1.98.1,
`rustup target add wasm32-unknown-unknown`, and wasm-bindgen CLI 0.2.128.
The workflow pins and verifies the official CLI archives.

```sh
celld-py lock examples/hello
celld-py lock examples/fleet.toml
CELLD_E2E=1 pytest experiments/rust-pyodide/test_runtime.py -q
python experiments/rust-pyodide/compare.py --rounds 4 --calls 100
```

Both backends run sequentially on the same GitHub runner, alternating order.
They use the same Python sources, package lock, host and local SQLite development
store. Every measured call executes; receipts are never replayed. Stateless calls
use one stable slot. Raw samples, CPU/RAM details and celld logs are artifacts.
Runner hardware can differ between jobs, so compare within a run.

Cold timings start with the first Python request after process startup. Warm
timings include localhost HTTP, Python, SQLite state/receipts and the response
durability gate. They are not a Python-only microbenchmark. The Rust experiment
also validates replies in Rust, so it is an implementation comparison, not an
isolated measurement of the cost of crossing the WASM boundary.

The local store substitutes for S3; these numbers do not establish production
S3 latency or cross-node scaling throughput. Celld's idle eviction can release
worker cells while a host remains running; fleet machines are provisioned by
the embedding platform.
