# Confirmed hello and real HTTP I/O throughput

Each row is the median of three independent confirmation samples at the best screened concurrency for that mode and density. All requests succeeded and every reply was correlated with its unique input. Runs use one celld process, a Go keep-alive load generator, and the local development object store. The upstream service and load generator share the runner CPU with celld. These are measured capacities on this allocation, not remote S3 or multi-machine results.

## Hello world

CPU: AMD EPYC 7763 64-Core Processor. [GitHub run](https://github.com/sambhav/celld-python/actions/runs/33988965087). SDK revision `55aaf8687572981f0560cd8b565cd75d0098a408`; native revision `1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec`.

| Mode | Packing limit | Clients | Requests/sec | Min–max | p50 ms | p95 ms | p99 ms | celld CPU cores | RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bare-stateless | — | 256 | 20105.7 | 20018.9–20762.6 | 11.37 | 26.65 | 35.30 | 2.33 | 236.4 |
| baseline-durable | 2 | 64 | 700.0 | 691.1–736.1 | 46.60 | 289.69 | 678.92 | 3.31 | 1640.4 |
| python-durable | 2 | 256 | 979.5 | 968.3–1009.7 | 174.36 | 766.96 | 1251.25 | 3.53 | 1721.9 |
| python-stateless | — | 256 | 3786.7 | 3750.8–3792.8 | 66.16 | 84.82 | 100.56 | 3.47 | 739.5 |

## Quote service, 10 ms upstream delay

CPU: AMD EPYC 9V74 80-Core Processor. [GitHub run](https://github.com/sambhav/celld-python/actions/runs/33988965087). SDK revision `55aaf8687572981f0560cd8b565cd75d0098a408`; native revision `1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec`.

| Mode | Packing limit | Clients | Requests/sec | Min–max | p50 ms | p95 ms | p99 ms | celld CPU cores | RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bare-stateless | — | 256 | 8731.7 | 8507.5–9157.6 | 28.46 | 41.18 | 49.69 | 2.51 | 259.9 |
| baseline-durable | 2 | 16 | 415.2 | 396.3–417.6 | 34.73 | 61.47 | 100.28 | 3.12 | 1683.7 |
| python-durable | 2 | 16 | 491.4 | 479.1–492.0 | 28.89 | 48.91 | 96.40 | 2.76 | 1700.3 |
| python-stateless | — | 1024 | 1961.8 | 1934.5–1962.3 | 521.67 | 590.13 | 640.42 | 3.36 | 896.5 |

## Quote service, 50 ms upstream delay

CPU: AMD EPYC 7763 64-Core Processor. [GitHub run](https://github.com/sambhav/celld-python/actions/runs/33988965087). SDK revision `55aaf8687572981f0560cd8b565cd75d0098a408`; native revision `1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec`.

| Mode | Packing limit | Clients | Requests/sec | Min–max | p50 ms | p95 ms | p99 ms | celld CPU cores | RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bare-stateless | — | 512 | 6830.4 | 6718.8–6853.5 | 73.52 | 90.90 | 103.48 | 2.53 | 304.6 |
| baseline-durable | 2 | 64 | 241.7 | 241.6–242.5 | 260.11 | 450.76 | 585.99 | 1.93 | 1604.9 |
| python-durable | 2 | 64 | 247.7 | 247.6–248.6 | 254.47 | 385.69 | 466.38 | 1.56 | 1606.8 |
| python-stateless | — | 256 | 1687.7 | 1668.3–1704.3 | 148.83 | 183.75 | 204.07 | 3.34 | 799.4 |

`baseline-*` embeds the dispatcher before validator/DI metadata caching. `python-durable` is the shipped SDK with caching and its replay guarantees. `python-concurrent` is a benchmark-only stateless experiment that removes receipts and the per-call concurrency gate. `python-stateless` runs Python directly in the native stateless isolate pool, with zero Python cells and no durable replay. Cell-based modes use 16 cells. `bare-stateless` executes JavaScript directly in celld. The concurrent experiment does not provide durable replay and is not a public SDK mode.

Receipts are capped at 4,096 per cell for 24 hours (65,536 total across 16 stateless cells). Durable throughput here describes the measured confirmation windows, not an indefinitely sustainable rate after the retained-call capacity is exhausted. No receipt limit or durability guarantee was relaxed for those rows.

The quote service validates Pydantic inputs and outputs, resolves a dependency, runs middleware, awaits a real HTTP JSON pricing/inventory lookup, and returns a customer-specific total and request trace. Upstream latency is controlled; no third-party service was load-tested. Tail latencies include queueing under closed-loop load.

