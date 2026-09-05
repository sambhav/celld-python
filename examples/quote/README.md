# An async quote service

This function validates its arguments, calls a pricing/inventory HTTP service,
validates the upstream JSON, and returns a typed quote. It includes a dependency
for the upstream URL and middleware that attaches a request trace. Each response
contains the customer, price, currency, and trace of that invocation.

From the repository root, start the example pricing service in one terminal:

```sh
cd experiments/throughput/load
go run . --serve 127.0.0.1:9090
```

In another terminal at the repository root:

```sh
pycelld dev examples/quote
```

With both servers running, call the function from a third terminal:

```sh
pycelld call quote name=customer-123 quantity=2
```

The local pricing fixture waits 50 ms and returns a unit price of 1,999 cents.
The quote returns `total_cents=3998`. Replace `UPSTREAM_URL` in `src/app.py` with
your service URL when adapting this example. The Go server is only a demo and
benchmark fixture; celld itself still needs only its object store.

`pycelld build examples/quote` generates the worker and a client at
`examples/quote/.celld-python/build/quote_client.py`. Copy the client into your
client project:

```python
from quote_client import Client

client = Client()  # Local pycelld dev on port 9876
result = client.quote(name="customer-123", quantity=2)
print(result.total_cents)
```

The example uses the default stateless pool: asynchronous lookups overlap, and
no replay receipts are written. Repeating a call can perform the lookup again.
The client makes one attempt unless you pass a `RetryPolicy`. For saved-result
recovery, change the decorator to `@app.function(replay=True)`; that selects the
serialized, durable cell path. See the [throughput experiment](../../experiments/throughput)
for the measured tradeoffs.
