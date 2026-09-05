# celld-python

Typed Python HTTP workers on a shared celld fleet, with S3 as the only external
persistence/coordination service. **Experimental work in progress.**

```python
from celld_python import Worker
from pydantic import BaseModel

app = Worker()

class Greeting(BaseModel):
    name: str

@app.post("/greet")
def greet(body: Greeting):
    return {"message": f"Hello, {body.name}!"}
```

The Python API includes typed request validation, route decorators, middleware,
request-scoped dependency injection and `State[Model]` for keyed state. The
bundler targets Pyodide 314.0.6 (Python 3.14), packages declared dependencies
ahead of deployment, and produces a celld Wrangler project. Native Linux/macOS
wheels are rejected; use the pinned Pyodide catalog or matching Emscripten wheels.

## Current verification

- Seven Python framework tests pass, including the hello example, validation,
  dependency cleanup/caching, concurrent requests and state rollback.
- A hello example bundles successfully for celld 0.4.0.
- Actual Python bootstrap inside celld is still being brought up. The draft PR
  does **not** yet claim a working end-to-end deployment.

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
app's declared `source` directory; the builder does not import application code.
Runtime package fetches resolve only against the bundle, with no CDN fallback.

celld currently runs one deployment per fleet. Multiple apps are composed into
one fleet bundle. This is a pool for trusted applications, not an adversarial
multi-tenant sandbox or independently deployed apps with a scheduler.

Sources: [celld](https://github.com/denoland/celld),
[celld compatibility](https://github.com/denoland/celld/blob/v0.4.0/docs/cloudflare-compat.md),
[Cloudflare Python packages](https://developers.cloudflare.com/workers/languages/python/packages/),
[Pyodide](https://pyodide.org/en/stable/).
