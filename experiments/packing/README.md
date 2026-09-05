# Native cell packing experiment

celld 0.4.0 packs up to 32 cells into one V8 isolate. Packing helps eviction
empty whole heaps; the ceiling bounds how many cells share one heap failure.
It is a fixed engine policy, not a measured Python optimum. Each isolate can
execute one JavaScript/WASM turn at a time, so several CPU-heavy Python cells
can contend for one core even on a machine with spare cores.

The [optional Rust patch](../../patches/celld-0.4.0-cell-density.patch) adds
`CELLD_MAX_CELLS_PER_ISOLATE=1..32`. The default remains 32. It only lowers the
packing ceiling; it does not change packing order, retirement, per-key
serialization, durability, or node memory admission. It cannot increase the
existing failure boundary. The variable is validated at startup and documented
in native help. Stock celld does not support it.

The native change is available in [draft PR #1 on the celld fork](https://github.com/sambhav/celld/pull/1).
It remains an opt-in experiment; the SDK uses stock celld by default. A mixed
fleet can use different packing limits because this is local placement policy,
not a wire or persistence format change. Existing cells are not migrated by
changing a configuration value; a new process reads the value at startup.

## Measurement

The `native cell packing experiment` GitHub workflow builds the fork at
[`1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec`](https://github.com/sambhav/celld/commit/1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec)
using Rust 1.98.1 and celld's `lab` profile (thin LTO). It records the native
source revision and binary checksum with each new report. The recorded comparison
below predates the fork PR and used upstream `a52f990` plus the patch; all four
ported native/doc files are byte-identical to that measured source.
Every configuration uses that identical binary on the same recorded runner.
Do not compare its absolute latency to an official release binary built with a
different profile. Trigger the workflow manually or add `benchmark-packing` to
the draft PR. Normal SDK tests continue to use the stock official binary.

The experiment holds eight independent CPU workers and eight clients constant
while varying the ceiling through 32, 2 and 1. Each handler performs a million
Python iterations and commits a counter increment. Four untimed calls per key
precede each 10-second sample; three rounds alternate density order. Every
reply and consecutive state total is checked. Every cohort gets a fresh store
and process. All use the same local SQLite development object store type;
remote S3 is not measured.

The report records throughput, CPU consumption, active RSS and memory five
seconds after explicitly evicting all eight cells. The expected placement for
eight cells is one, four and eight cell isolates respectively. The pure Rust
tests verify that placement boundary and that retiring heaps remain excluded.
The [completed GitHub comparison](../results#native-packing-improvement) found
2.14× CPU throughput at density 2 with 14.4% more active RSS. Density 1 provided
no additional throughput and used more memory. Density 2 is a starting point
for CPU-heavy pools on similar hardware, not a universal replacement for 32.

The SDK's host and its Pyodide loader are unchanged. More isolates still cannot
make one state key execute concurrently or create more host CPU resources.

## Build from the fork

```sh
git clone https://github.com/sambhav/celld.git celld-native
git -C celld-native checkout 1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec
cargo test --manifest-path celld-native/tools/packing-checks/Cargo.toml --locked
cargo build --manifest-path celld-native/Cargo.toml -p celld --profile lab --locked
```

Set `CELLD_MAX_CELLS_PER_ISOLATE=2` when starting this native binary on a
CPU-heavy pool to try the tested density. Use the same build profile and workload
when comparing it with 32. The Python SDK and app code need no changes.
The fork's [focused GitHub checks](https://github.com/sambhav/celld/actions/runs/33983676627)
pass all five Rust cases.

From the SDK repository, run the density comparison with the fork binary:

```sh
CELLD_NATIVE_REPOSITORY=sambhav/celld \
CELLD_NATIVE_SHA=1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec \
PATH=/path/to/celld-native/target/lab:$PATH python experiments/packing/measure.py
```

See the workflow for Python/Node prerequisites and cache preparation. The
full benchmark is intended for a GitHub runner with recorded CPU and memory.

## Apply the standalone patch instead

For an upstream v0.4.0 checkout that does not already contain this change:

```sh
git -C /path/to/celld apply /path/to/celld-python/patches/celld-0.4.0-cell-density.patch
python experiments/packing/check.py /path/to/celld
cargo build --manifest-path /path/to/celld/Cargo.toml -p celld --profile lab --locked
PATH=/path/to/celld/target/lab:$PATH python experiments/packing/measure.py
```

The small check compiles the exact upstream `env_vars.rs` and `isolate.rs`
modules without linking V8. It tests parsing, density growth, packing around
retiring heaps, and refusal to free an isolate with outstanding requests.
The workflow separately builds the complete binary and runs the actual Python
integration suite before collecting measurements.
