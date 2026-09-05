# Matched TypeScript and Python workers on celld

Each row is the median of three independent confirmation samples at the best screened concurrency for that mode and density. All requests succeeded and every reply was correlated with its unique input. Runs use one celld process, a Go keep-alive load generator, and the local development object store. The upstream service and load generator share the runner CPU with celld. These are measured capacities on this allocation, not remote S3 or multi-machine results.

## Hello world

CPU: INTEL(R) XEON(R) PLATINUM 8573C. [GitHub run](https://github.com/sambhav/celld-python/actions/runs/33991660727). SDK revision `ac56cf10217ceefb012ab8476f4c985d00f6391b`; native revision `1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec`.

| Mode | Packing limit | Clients | Requests/sec | Min–max | p50 ms | p95 ms | p99 ms | celld CPU cores | RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bare-stateless | — | 512 | 41856.4 | 39988.1–43011.4 | 10.99 | 25.66 | 33.36 | 2.17 | 297.8 |
| python-stateless | — | 256 | 5666.0 | 5502.6–5770.0 | 43.96 | 58.63 | 75.17 | 3.52 | 744.1 |
| typescript-stateless | — | 1024 | 30082.3 | 28830.1–30677.5 | 31.96 | 65.69 | 82.93 | 2.55 | 371.3 |

## Quote service, 10 ms upstream delay

CPU: AMD EPYC 9V74 80-Core Processor. [GitHub run](https://github.com/sambhav/celld-python/actions/runs/33991660727). SDK revision `ac56cf10217ceefb012ab8476f4c985d00f6391b`; native revision `1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec`.

| Mode | Packing limit | Clients | Requests/sec | Min–max | p50 ms | p95 ms | p99 ms | celld CPU cores | RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bare-stateless | — | 256 | 9348.1 | 9313.8–9390.7 | 26.64 | 38.08 | 44.97 | 2.51 | 273.0 |
| python-stateless | — | 256 | 2042.3 | 2039.8–2048.8 | 122.96 | 151.72 | 175.29 | 3.34 | 822.3 |
| typescript-stateless | — | 256 | 8211.9 | 8178.4–8266.3 | 30.40 | 42.87 | 50.29 | 2.64 | 365.3 |

## Quote service, 50 ms upstream delay

CPU: AMD EPYC 7763 64-Core Processor. [GitHub run](https://github.com/sambhav/celld-python/actions/runs/33991660727). SDK revision `ac56cf10217ceefb012ab8476f4c985d00f6391b`; native revision `1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec`.

| Mode | Packing limit | Clients | Requests/sec | Min–max | p50 ms | p95 ms | p99 ms | celld CPU cores | RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bare-stateless | — | 512 | 6816.4 | 6804.6–6911.1 | 73.90 | 90.08 | 100.60 | 2.52 | 311.2 |
| python-stateless | — | 256 | 1706.9 | 1693.9–1709.7 | 147.87 | 181.01 | 204.48 | 3.34 | 808.5 |
| typescript-stateless | — | 512 | 6135.8 | 6115.5–6191.7 | 81.75 | 101.13 | 116.87 | 2.63 | 362.7 |

`baseline-*` embeds the dispatcher before validator/DI metadata caching. `python-durable` uses the SDK receipt path with caching and replay guarantees. `python-concurrent` is a benchmark-only stateless experiment that removes receipts and the per-call concurrency gate. `python-stateless` runs Python directly in the native stateless isolate pool, with zero Python cells and no durable replay. Cell-based modes use 16 cells. `typescript-stateless` is the matched TypeScript application with Zod validation; `bare-stateless` is the minimal JavaScript control without those layers. TypeScript and Python execute in the same native stateless pool, with no receipts. The concurrent experiment does not provide durable replay and is not a public SDK mode.

Receipts are capped at 4,096 per cell for 24 hours (65,536 total across 16 stateless cells). Durable throughput here describes the measured confirmation windows, not an indefinitely sustainable rate after the retained-call capacity is exhausted. No receipt limit or durability guarantee was relaxed for those rows.

The quote service validates Pydantic inputs and outputs, resolves a dependency, runs middleware, awaits a real HTTP JSON pricing/inventory lookup, and returns a customer-specific total and request trace. Upstream latency is controlled; no third-party service was load-tested. Tail latencies include queueing under closed-loop load.


The TypeScript capacity is 5.31× Python for hello, 4.02× for 10 ms I/O, and
3.59× for 50 ms I/O **within each runner**. The hello job used an Intel Xeon
Platinum 8573C, the 10 ms job an AMD EPYC 9V74, and the 50 ms job an AMD EPYC
7763, each with two physical/four logical CPUs. Absolute rates across these jobs
or older runs do not isolate a language or code effect.

The TypeScript worker includes pinned Zod 4.5.4 validation of input, context/client
information, pricing data and output; the I/O case includes middleware and a
per-invocation dependency resolver. Python uses the equivalent Pydantic function
and its full SDK dispatch path. The valid benchmark inputs satisfy both; the two
validators are not claimed to have identical coercion rules. The TypeScript
source is bundled by esbuild into JavaScript, executed by celld's V8 runtime.
Node is build/test tooling, not the server runtime.

The Go fixture performs one actual upstream HTTP lookup per quote. Its completed
requests exactly matched client successes in every sample. All confirmation and
screening samples had zero errors; no samples were rejected. The three TypeScript
correctness tests also passed on each runner. The native executable checksum is
identical across jobs and matches the prior stateless experiment.

These numbers measure the Python prototype at the recorded source revision,
before the supported bounded stateless cache was added. They do not measure
Monty, native CPython, cold starts, snapshot restoration, or machine autoscaling.
