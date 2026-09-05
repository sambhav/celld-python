# celld-python

Typed Python HTTP workers on a shared celld fleet, with S3 as the only external
persistence/coordination service. **Experimental work in progress.**

```python
from celld_python import App

app = App

@app.function
def hello(name: str = "world") -> str:
    return f"Hello, {name}"
```

The OSS package provides worker functions, Pydantic validation, middleware,
dependency injection, caller context, and `State[Model]` for keyed state.
Authentication and tenancy are platform concerns: an optional host adapter
supplies trusted context and a stable scope, with no built-in account registry.
The bundler targets Pyodide 314.0.6 (Python 3.14), bundles declared packages
before deployment, and produces a celld Wrangler project. Use the pinned
Pyodide catalog or matching Emscripten/pure Python wheels.

## Current verification

- Seven Python framework tests pass, including the hello example, validation,
  dependency cleanup/caching, concurrent requests and state rollback.
- A hello example bundles successfully for celld 0.4.0.
- A real celld 0.4.0 integration test passes: bundled Python and Pydantic,
  hello/greet functions, validation errors, missing routes and method handling.
- A version-pinned JavaScript adapter is sufficient; no celld fork is required
  for the tested runtime. Function-only ergonomics and generated clients are
  being added in the draft.

## Development

Requires Python 3.11+ on the build machine, npm for downloading the pinned
Pyodide runtime, and celld 0.4.0 plus esbuild for deployment.

```sh
python -m pip install -e . pytest pytest-asyncio
pytest -q
celld-py lock examples/hello
celld-py build examples/hello
celld-py dev examples/hello
celld-py deploy examples/hello -- --bucket s3://my-celld-bucket
```

`lock` downloads runtime/package artifacts and writes `celld.lock.json`.
`build` checks their SHA-256 hashes and runs offline. Source lives under each
app's declared `source` directory; the builder checks app imports and extracts schemas inside the pinned WASM runtime.
Runtime package fetches resolve only against the bundle, with no CDN fallback.

celld currently runs one deployment per fleet. Multiple apps are composed into
one fleet bundle. The OSS runtime supplies
worker and state primitives; deployment/auth policy belongs to its embedding platform.

Sources: [celld](https://github.com/denoland/celld),
[celld compatibility](https://github.com/denoland/celld/blob/v0.4.0/docs/cloudflare-compat.md),
[Cloudflare Python packages](https://developers.cloudflare.com/workers/languages/python/packages/),
[Pyodide](https://pyodide.org/en/stable/).
