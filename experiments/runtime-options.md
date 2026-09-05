# Fast Python runtimes and warm pools

Assessment as of 2026-09-05. These are architecture options, not implemented
backends or promised cold-start numbers. The supported SDK uses Pyodide.

## Recommendation for this project

Keep full Python/Pydantic/WASM-wheel compatibility in the default backend. The
stateless path removes receipts and cell routing, and reuses interpreters across
calls. The next cold-start experiment should investigate snapshots after loading
the runtime, dependencies, and application, keyed by immutable code/dependency
revision. Cloudflare already performs this work at deployment and restores WASM
linear-memory snapshots on demand; our current celld integration does not.

[Cloudflare's deployment lifecycle](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/)
describes the mechanism. This requires runtime integration; simply copying a WASM
buffer is not a demonstrated restore protocol for the current celld/Pyodide build.
Imports with external side effects, live handles, clocks, or randomness require a
defined initialization/restore lifecycle. It also does not accelerate execution
of already-warm Python bytecode.

For full Python throughput, a separate benchmark of native CPython under a Rust
supervisor is warranted. Preload dependencies, reuse isolated worker processes,
and retire application workers when idle. That would preserve ordinary native
wheels and move scheduling into Rust while Python still executes on CPython.
It is a proposed backend and process protocol, not a feature of the current celld
worker API. [PyO3](https://github.com/pyo3/pyo3) supports embedding CPython, but
embedding alone supplies neither a worker pool nor tenant isolation.

## Where Monty fits

Monty is a restricted interpreter written in Rust. Current documentation supports
functions, decorators, simple classes, and async host calls, but excludes third-party
packages, class inheritance, generator functions, and substantial parts of the
standard library. The existing SDK depends on several of those missing features.
It cannot run the current Pydantic/NumPy applications unchanged.

- [Current language limitations](https://pydantic.dev/docs/monty/limitations/)
- [Rust interpreter and subprocess pool](https://pydantic.dev/docs/monty/quickstart/rust/)
- [WebAssembly build](https://pydantic.dev/docs/monty/quickstart/javascript/)

Running Monty in WASM does not make Python WASM wheels loadable. Pyodide wheels
target CPython's extension interfaces and a matching PyEmscripten platform;
[the ABI requirements](https://pyodide.org/en/stable/development/abi.html) go well
beyond a common `.wasm` file format.

A Monty backend could expose specific host functions backed by Rust or standalone
WASM modules, translating values across that boundary. Each API would need an
adapter. Running Pydantic/NumPy in a separate Pyodide or CPython instance behind
such an adapter is possible in principle, but retains that instance's startup
and memory cost. It is a hybrid design, not general package import support.
Monty's own interpreter-start benchmarks are not HTTP service throughput results.

Use Monty as an explicit restricted backend for small transformations and
orchestration over host-provided operations if that product becomes useful.
Do not silently substitute it for full Python or claim faster compute merely
because its implementation language is Rust.

## Scale to zero versus warm capacity

Per-app scale to zero can coexist with a shared warm base-runtime pool. An idle
app releases its dedicated instances; a new invocation borrows a clean base or
restores its versioned snapshot, then active instances expand up to a configured
limit. The shared pool still consumes resources. Zero machines requires a
separate request activator/provisioner and incurs machine-start latency.

Preloading a base interpreter does not eliminate app/package initialization.
Snapshots or warm entries per dependency set address that additional work. Never
reuse a tenant-mutated interpreter as a clean base for another tenant.

Those pool/controller policies belong to the hosting platform. S3 can continue
to store bundles and future snapshots without introducing Redis, a database
service, or Kubernetes as required SDK dependencies.

For a hosted option today, [Modal's autoscaler](https://modal.com/docs/guide/scale)
supports scale to zero, a maximum, minimum warm containers, and an active buffer;
[memory snapshots](https://modal.com/docs/guide/memory-snapshots) reduce initialization
work. Cloudflare Python Workers is another hosted Pyodide option. Neither is the
S3-only OSS backend being built here.
