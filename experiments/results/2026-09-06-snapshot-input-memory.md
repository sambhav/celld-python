# Snapshot input retention — 2026-09-06

[GitHub run](https://github.com/sambhav/celld-python/actions/runs/34019509707); SDK `13e7e85cf185f0fd8d895e0e797d337db174d4cb`, native `1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec`.

Same runner for both variants: AMD EPYC 9V74, 2 physical / 4 logical CPUs, three fresh processes per variant and isolate count. The Python application, snapshot, binary and assets are identical; only removal of the bootstrap snapshot reference differs. Local SQLite object store, with no remote S3 download timing.

| Snapshot input | Initialized runtimes | Retained input | Median RSS | Median allocator-adjusted memory |
|---|---:|---:|---:|---:|
| retained | 1 | 30.0 MiB | 470.9 MiB | 449.6 MiB |
| retained | 4 | 120.0 MiB | 1174.6 MiB | 1112.1 MiB |
| released | 1 | 0.0 MiB | 471.6 MiB | 449.3 MiB |
| released | 4 | 0.0 MiB | 1172.7 MiB | 1106.7 MiB |

The loader copied the snapshot into WASM memory but kept the original input in API.config._loadSnapshot. The guarded adapter now deletes that bootstrap-only reference after taking the local restore input. A real Pyodide regression test verifies the field is absent after restoration and both ordinary and async Python still work. The same adapter change is in the native runtime artifact builder.

This removes about 30 MiB of retained input per runtime, but this experiment did not show a material RSS reduction. Four runtimes retained 120 MiB of input in the baseline, yet median RSS changed by less than 2 MiB. Removing a reference does not force V8 garbage collection or return allocator pages to the OS. These samples do not determine whether other references or allocator behavior also contribute; do not advertise a 120 MiB RSS saving.

Each process received 32 concurrent hello requests, then eight checked requests and memory samples at 250 ms intervals. Instrumentation confirmed exactly 1 or 4 runtime initializations, as configured. All responses were verified and no request retries were used. This is a short post-startup memory observation, not a sustained workload, throughput result or leak test.

The raw report preserves all 12 process samples, CPU metadata, exact binary checksum and per-process retained-input observations. Earlier startup/throughput/RSS results remain in [the snapshot benchmark](2026-09-06-snapshots.md); the figures above must not be interpreted as improvements over runs on different hosts or workloads.
