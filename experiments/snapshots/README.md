# Cloudflare-style Python initialization

Cloudflare snapshots CPython/Pyodide after importing packages and application
code, and restores dynamic-library mappings and Python-to-JavaScript references.
Sources inspected: [Python Workers architecture](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/),
[workerd snapshot implementation](https://github.com/cloudflare/workerd/blob/main/src/pyodide/internal/snapshot.ts),
[Pyodide 314.0.6 snapshot API](https://github.com/pyodide/pyodide/blob/314.0.6/src/js/snapshot.ts).

Our first implementation uses the snapshot API already included in pinned
Pyodide 314.0.6. It snapshots the interpreter and portable stdlib imports only,
compresses the artifact with gzip, and restores it using celld's native Rust
DecompressionStream. Runtime and generator hashes identify the build cache;
application imports and all third-party dynamic libraries run after restore.
This also avoids freezing application secrets, connections, or user globals.

This is deliberately not a complete port of workerd's dedicated snapshots.
A full package/application snapshot needs compatible DSO memory and table
positions, filesystem contents, dlopen handles, JS references and entropy
handling. Copying linear memory alone does not provide those guarantees.

Local validation covers snapshot restore in real celld, Pydantic validation,
NumPy execution, state commit/restart, and the same Wrangler hello without a
snapshot. Local timings are smoke diagnostics, not performance results.
GitHub measurements compare identical code and binaries with snapshots enabled
and disabled, including native readiness separately from the first Python call.

Completed same-runner results: [2026-09-06 snapshots and isolate scaling](../results/2026-09-06-snapshots.md).
