# Confirmed hello and real HTTP I/O throughput

Each row is the median of three independent confirmation samples at the best screened concurrency for that mode and density. All requests succeeded and every reply was correlated with its unique input. Runs use one celld process, a Go keep-alive load generator, and the local development object store. The upstream service and load generator share the runner CPU with celld. These are measured capacities on this allocation, not remote S3 or multi-machine results.

## Quote service, 10 ms upstream delay

CPU: Intel(R) Xeon(R) 6973P-C. [GitHub run](https://github.com/sambhav/celld-python/actions/runs/33988408853). SDK revision `96059cdbf4fd413be622818bfb168280f5443dee`; native revision `1c5e12e0d2c5000e5a2d837413d16ce6123ee1ec`.

| Mode | Packing limit | Clients | Requests/sec | Min–max | p50 ms | p95 ms | p99 ms | celld CPU cores | RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bare-stateless | 2 | 256 | 15497.3 | 15382.6–15581.6 | 16.02 | 21.76 | 25.86 | 2.44 | 309.8 |
| python-concurrent | 2 | 256 | 2123.6 | 2093.3–2146.8 | 118.91 | 158.03 | 182.44 | 3.53 | 1820.4 |
| python-stateless | 2 | 256 | 3014.9 | 2987.7–3095.4 | 83.12 | 104.96 | 121.52 | 3.40 | 854.8 |

`baseline-*` embeds the dispatcher before validator/DI metadata caching. `python-durable` is the shipped SDK with caching and its replay guarantees. `python-concurrent` is a benchmark-only stateless experiment that removes receipts and the per-call concurrency gate. `python-stateless` runs Python directly in the native stateless isolate pool, with zero Python cells and no durable replay. Cell-based modes use 16 cells. `bare-stateless` executes JavaScript directly in celld. The concurrent experiment does not provide durable replay and is not a public SDK mode.

Receipts are capped at 4,096 per cell for 24 hours (65,536 total across 16 stateless cells). Durable throughput here describes the measured confirmation windows, not an indefinitely sustainable rate after the retained-call capacity is exhausted. No receipt limit or durability guarantee was relaxed for those rows.

The quote service validates Pydantic inputs and outputs, resolves a dependency, runs middleware, awaits a real HTTP JSON pricing/inventory lookup, and returns a customer-specific total and request trace. Upstream latency is controlled; no third-party service was load-tested. Tail latencies include queueing under closed-loop load.

