# celld-python

Python worker functions and durable state on [celld](https://github.com/denoland/celld).
One S3 bucket is the only external persistence/coordination service. **Experimental.**

```python
from celld_python import App

app = App

@app.function
def hello(name: str = "world") -> str:
    return f"Hello, {name}"
```

Builds generate a Python client with real signatures:

```python
from hello_client import Client

client = Client("http://localhost:9876")
print(client.hello(name="Sam"))  # Hello, Sam
```

The OSS package supplies functions, Pydantic validation, middleware, dependency
injection, context, retries, and keyed state. Your platform owns authentication,
tenancy, quotas, and deployment policy. There is no account registry or auth database.

## Start a worker

Build requirements: Python 3.11+, Node.js 22+, npm, **celld 0.4.0**, and esbuild on PATH.
Python runs as Pyodide **314.0.6 / Python 3.14.2** inside celld's V8/WASM runtime.

```sh
# From this checkout:
python -m pip install -e .
npm install -g esbuild@0.25.12
# Install celld 0.4.0 using its upstream release instructions.

celld-py init hello
celld-py lock hello
celld-py dev hello
```

Edit `hello/src/app.py`; the worker rebuilds and restarts. Invalid edits keep the
last working deployment serving. Local state survives restarts. `dev` binds only
to loopback and uses celld's local SQLite object store; it needs no cloud credentials.

The client is generated at `hello/.celld-python/build/hello_client.py`. Copy it
into your caller project, or add that directory to `PYTHONPATH`. Install
`celld-python` in the caller environment too. `App()` also works when you want
an explicit app instance; `app = App` registers functions in their defining module.

## Typed calls and context

Arguments and results are validated with Pydantic. Both sync and async functions
work; neither needs a request object, URL, status code, or response envelope.

```python
from celld_python import App, Context, ClientInfo
from pydantic import BaseModel

app = App

class Caller(BaseModel):
    actor: str

@app.function
def greet(name: str, ctx: Context[Caller]) -> str:
    return f"Hello {name}, from {ctx.data.actor}"
```

```python
# Caller and both clients are generated from your worker contract.
from hello_client import Caller, Client, AsyncClient
from celld_python import ClientInfo

client = Client(endpoint, context=Caller(actor="Sam"),
                client_info=ClientInfo(name="dashboard", version="1.0"))
client.greet(name="Alex")
client.with_context(Caller(actor="Taylor")).greet(name="Alex")

async_client = AsyncClient(endpoint, context=Caller(actor="Sam"))
await async_client.greet(name="Alex")
```

`with_context` returns an independent client and snapshots its data. Server
context includes `data`, `client`, `call_id`, `request_id`, `attempt`, and a
per-call `local` dictionary for middleware. `scope` and `host` come exclusively
from the platform adapter. Caller data and declared client information are
untrusted; schema validation does not authenticate them.

Clients preserve Pydantic model shapes, aliases, common constraints, unions,
lists and literals. Custom Python validators stay on the server. Unsupported
structural schemas fail generation explicitly. Injected dependencies and state
never become client arguments. Regenerate a saved contract with:

```sh
celld-py client hello/.celld-python/build/hello.schema.json --out hello_client.py
```

## Durable state

```python
from celld_python import App, State
from pydantic import BaseModel

app = App

class Counter(BaseModel):
    total: int = 0

@app.function(key="counter_id", namespace="counters")
def increment(counter_id: str, state: State[Counter], amount: int = 1) -> Counter:
    state.value.total += amount
    return state.value

@app.function(key="counter_id", namespace="counters")
def read(counter_id: str, state: State[Counter]) -> Counter:
    return state.value
```

```python
client.increment(counter_id="cart-123", amount=2).total  # 2
client.read(counter_id="cart-123").total                 # 2
```

The state address is `(host scope, app name, namespace, key)`. Functions sharing
a namespace share the same model. Keep these names stable across deployments.
A state key is a required string argument. A new key starts with the model's
defaults, so state models must be constructible without arguments.

celld's native concurrency gate serializes complete calls for a key, including
awaits. Each invocation gets fresh, validated state. Success commits state and
the result receipt in one SQLite transaction, behind celld's durability gate.
Application errors, failed validation, dependency cleanup failures, and thrown
exceptions discard that invocation's state changes. Globals are ephemeral;
use `State` for persistence. Additive state changes can use model defaults;
other schema migrations are currently application-managed.

## Dependencies, middleware, errors

```python
from typing import Annotated
from celld_python import App, Context, Depends, Error, Invocation

app = App

def actor(ctx: Context[Caller]) -> str:
    return ctx.data.actor

@app.middleware
async def trace(invocation: Invocation, call_next):
    invocation.context.local["trace"] = invocation.context.call_id
    return await call_next(invocation)

@app.function
def greet(name: str, user: Annotated[str, Depends(actor)]) -> str:
    if not name:
        raise Error("A name is required", code="missing_name")
    return f"{user} says hello to {name}"
```

Dependencies are cached once per call. Sync/async generator dependencies can
`yield` a resource and clean it up in `finally`. Middleware runs in registration
order, outermost first, and receives a function invocation and its Python result.
Malformed context and unknown arguments are rejected before middleware.
`Error` becomes a `RemoteError` with the same code and message in the client.
Unexpected exceptions are logged by the host and return a generic execution error.

## Retry behavior

The client retries transport failures and transient 429/502/503/504 responses,
using exponential jitter and one logical call ID. It does not retry application,
validation or authorization errors, or arbitrary execution failures.

```python
from celld_python import RetryPolicy, RemoteError

client = Client(endpoint, retries=RetryPolicy(attempts=3), timeout=60)
operation = client.with_call_id("checkout-operation-0001")
operation.increment(counter_id="cart-123")
# Repeating this exact call returns the committed result for 24 hours.
```

Receipts are scoped to the worker cell. Reusing an ID within that cell with
different arguments or context fails with `idempotency_conflict`. After a
transport failure, `RemoteError.call_id` lets you recover that same logical call.
External effects made before a crash are outside the state transaction; use
`ctx.call_id` with the external service's own idempotency support.

`timeout` budgets retries and socket waits. It is not server cancellation;
canceling an async client task does not undo a submitted operation.

## Predeclared packages

Declare imports in your app's `pyproject.toml`:

```toml
[project]
name = "math"
version = "0.1.0"
dependencies = ["numpy==2.4.6"]

[tool.celld-python]
entrypoint = "app"  # default
source = "src"     # default
# Pure Python or matching WASM wheels outside the pinned Pyodide catalog:
# wheels = ["vendor/my_library-1.0-py3-none-any.whl"]
```

```python
import numpy as np
from celld_python import App

app = App

@app.function
def mean(values: list[float]) -> float:
    return float(np.mean(values))
```

`lock` downloads the pinned runtime and resolves catalog/local-wheel dependencies,
including Python/WASM compatibility and environment markers. `celld.lock.json`
pins SHA-256 hashes. Commit it. For packages outside the catalog, supply their
pure Python or matching Emscripten wheels and their dependencies explicitly;
ordinary Linux/macOS extension wheels cannot run in WASM. Add each top-level
package to `dependencies`; `wheels` supplies its distribution artifact.

`build` verifies cached artifacts, checks app imports inside the pinned WASM
runtime, and extracts schemas. It runs offline. Package/stdlib loads at runtime
resolve only from the bundle; there is no CDN or PyPI fallback. App module
imports execute during build, so keep top-level initialization deterministic.
On a fresh checkout, `lock` repopulates the cache; review any lock-file changes.

## Shared celld pool and deployment

Compose several apps into one fleet bundle with a fleet TOML file; see
[examples/fleet.toml](examples/fleet.toml). Each app has its own imports and
Python interpreter instances. celld manages the underlying shared V8 isolate pool.

```sh
celld-py lock examples/fleet.toml
celld-py dev examples/fleet.toml
celld-py build examples/fleet.toml --out dist
celld-py deploy examples/fleet.toml -- --bucket my-celld-bucket
```

Deployment uses your configured celld/S3 credentials. Bucket configuration and
node provisioning follow [celld's instructions](https://github.com/denoland/celld).
The generated project contains the application, runtime, wheels, schemas,
clients and Wrangler configuration. The examples mount hello at `/hello`,
counters at `/counters`, and NumPy at `/math`; supply the app's mounted endpoint
to its generated client.

To embed your platform policy, pass a self-contained ES module with
`--host platform.mjs` to `build`, `dev` or `deploy`:

```javascript
export async function resolveContext(request, env, operation) {
  // Implement verification in your platform. Return a Response to reject.
  const identity = await yourPlatformVerification(request, operation.app);
  return {scope: identity.stableScope, attributes: {principal: identity.subject}};
}
```

The host overwrites scope/host headers before invoking Python. The resolver
also controls access to schemas. The default resolver supplies scope `default`
and empty attributes, with no authentication. The native Python client accepts
an optional `token` for a platform that uses Bearer authentication.

## Current limits and internals

- celld 0.4.0 and Pyodide 314.0.6 are pinned. No celld fork is required.
- State and result payloads are limited to 1 MiB; caller context to 16 KiB;
  client information to 4 KiB. These are implementation bounds, not tenant quotas.
- Calls run under celld's native 30-second concurrency gate, including cold boot.
  This is a short-function primitive; background jobs, streaming and native
  subprocesses are not implemented.
- Stateless calls use 16 stable cells per app/scope; stateful calls use one per
  key. Each active cell has a Python interpreter. Cold starts and memory usage
  have not been optimized with snapshots.
- Each cell retains up to 4,096 call receipts for 24 hours. At capacity it
  returns a retryable capacity error rather than evicting an unexpired receipt.
- Replay covers committed state/results, not arbitrary external effects.
  Independent app deployment and state migration orchestration belong to the
  embedding platform. Hostile uploaded-code isolation is not an audited feature.

The heavy execution, scheduling, SQLite and durability paths already run in
celld's Rust runtime. A small JavaScript adapter connects Pyodide to those APIs.
Its version-checked changes disable inapplicable Node/browser paths and reuse
celld's process-wide compiled WASM module cache. The optional
[Rust watcher patch](patches/README.md) addresses an upstream development issue.

## Tests and examples

```sh
python -m pip install -e . pytest pytest-asyncio
pytest -q
# After locking the examples, with celld and esbuild on PATH:
CELLD_E2E=1 pytest tests/test_celld.py -q
```

Real celld tests cover Python/Pydantic, NumPy, generated clients, keyed concurrent
updates, process restart, replay/conflicts, and host context. Framework tests
cover dependency lifetimes, context separation, rollback and package checks.
See [examples](examples) for minimal functions, counters and predeclared NumPy;
the hello HTTP example is a lower-level escape hatch.

Apache-2.0. Bundled runtimes/packages retain their own licenses; see
[third-party notices](THIRD_PARTY.md).
