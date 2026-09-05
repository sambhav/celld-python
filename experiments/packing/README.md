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

This is an experiment, not an SDK default or a published celld fork. A mixed
fleet can use different packing limits because this is local placement policy,
not a wire or persistence format change. Existing cells are not migrated by
changing a configuration value; a new process reads the value at startup.

## Measurement

The `native cell packing experiment` GitHub workflow builds the pinned upstream
commit with this patch using Rust 1.98.1 and celld's `lab` profile (thin LTO).
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

## Check the patch locally

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
