"""Real celld/Pyodide integration tests, enabled with CELLD_E2E=1.

Uses celld dev to publish to its local store, then runs the exact dev child
command without its watcher. v0.4.0's Linux watcher also reacts to reads,
causing repeated reloads during bundling. No production storage is accessed.
"""
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from celld.build import build

pytestmark = pytest.mark.skipif(os.environ.get("CELLD_E2E") != "1", reason="Set CELLD_E2E=1 with celld/esbuild and locked artifacts")
ROOT = Path(__file__).parents[1]


def request(port, path="/", data=None, method=None, headers=None):
    if isinstance(data, dict):
        data = json.dumps(data).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"content-type": "application/json", **(headers or {})})
    try:
        response = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read(), response.headers


def port_number():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def environment():
    env = dict(os.environ, CELLD_V8_HEAP_LIMIT_MB="256", CELLD_MAX_STATELESS_ISOLATES="1")
    # A test cannot accidentally inherit a real fleet's credentials/topology.
    for name in ("CELLD_BUCKET", "CELLD_CLOUD", "CELLD_ADDR", "CELLD_INTERNAL_ADDR", "CELLD_ADVERTISE", "CELLD_WATCH",
                 "CELLD_INTERNAL_DEV_STORE", "CELLD_NODE", "CELLD_STORAGE_PROBE", "CELLD_TEST_BUCKET"):
        env.pop(name, None)
    return env


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def publish_local(project, log_path):
    binary = shutil.which("celld")
    assert binary, "celld must be on PATH"
    with log_path.open("w") as log:
        process = subprocess.Popen([binary, "dev", str(project), "--port", str(port_number())],
                                   env=environment(), stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                contents = log_path.read_text()
                if "ready " in contents:
                    return
                if process.poll() is not None:
                    pytest.fail(contents)
                time.sleep(.1)
            pytest.fail(log_path.read_text())
        finally:
            stop(process)


@contextmanager
def node(project, log_path):
    port = port_number()
    state = project / ".celld" / "dev"
    env = environment()
    env.update(CELLD_INTERNAL_DEV_STORE=str(state / "objects.sqlite3"),
               CELLD_WATCH=str(state / "runtime"), CELLD_NODE="python-e2e", RUST_LOG="info")
    with log_path.open("w") as log:
        process = subprocess.Popen([shutil.which("celld"), "--no-control-plane", "--bucket", "celld-dev",
                                   "--listen", f"127.0.0.1:{port}", "--internal-listen", "127.0.0.1:0"],
                                  env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if "celld listening on" in log_path.read_text():
                    break
                if process.poll() is not None:
                    pytest.fail(log_path.read_text())
                time.sleep(.1)
            else:
                pytest.fail(log_path.read_text())
            yield port
        finally:
            stop(process)


def test_hello_on_actual_celld(tmp_path):
    project = build(ROOT / "examples" / "hello", tmp_path / "project")
    publish_local(project, tmp_path / "publish.log")
    with node(project, tmp_path / "node.log") as port:
        status, body, _ = request(port)
        assert status == 200, (body, (tmp_path / "node.log").read_text())
        assert json.loads(body) == {"message": "Hello from Python on celld!"}
        status, body, _ = request(port, "/greet", {"name": "Sam"})
        assert status == 200 and json.loads(body) == {"message": "Hello, Sam!"}
        assert request(port, "/greet", {"name": ""})[0] == 422
        assert request(port, "/greet", b"invalid")[0] == 422
        assert request(port, "/greet", method="DELETE")[0] == 405
        assert request(port, "/missing")[0] == 404


def test_stateless_overlap_no_replay_and_opt_in_recovery(tmp_path):
    from celld.build import lock

    app = tmp_path / "app"
    (app / "src").mkdir(parents=True)
    (app / "pyproject.toml").write_text('[project]\nname="execution"\nversion="0.1.0"\ndependencies=[]\n')
    shutil.copytree(ROOT / "examples/.celld-python/cache", app / ".celld-python/cache")
    (app / "src/app.py").write_text('''
import asyncio
from celld import App, Context, Depends
app = App
seen = 0
active = 0
peak = 0
closed = []

@app.middleware
async def trace(call, next):
    call.context.local["trace"] = call.context.call_id
    return await next(call)

async def resource(ctx: Context):
    try:
        yield ctx
    finally:
        closed.append(ctx.call_id)

@app.function
def touch(value: int = 0) -> int:
    global seen
    seen += 1
    return seen

@app.function(replay=True)
def saved(value: int = 0) -> int:
    global seen
    seen += 1
    return seen

@app.function
async def overlap(name: str, ctx=Depends(resource)) -> dict:
    global active, peak
    active += 1
    peak = max(peak, active)
    try:
        await asyncio.sleep(0.1)
        return dict(name=name, actor=ctx.data["actor"], trace=ctx.local["trace"], peak=peak)
    finally:
        active -= 1

@app.function
def cleanup_count() -> int:
    return len(closed)
''')
    lock(app)
    project = build(app, tmp_path / "project")
    publish_local(project, tmp_path / "publish.log")
    call = {"x-celld-call-id": "repeat-the-same-call-001"}
    with node(project, tmp_path / "node.log") as port:
        schema = json.loads(request(port, "/__celld/schema")[1])["functions"]
        assert schema["touch"]["replay"] is False and schema["saved"]["replay"] is True
        for count in (1, 2):
            status, raw, headers = request(port, "/touch", {}, headers=call)
            assert status == 200 and json.loads(raw) == {"result": count}
            assert headers.get("x-celld-replayed") is None
        assert json.loads(request(port, "/touch", {"value": 99}, headers=call)[1]) == {"result": 3}
        saved = request(port, "/saved", {}, headers=call)
        repeated = request(port, "/saved", {}, headers=call)
        assert saved[0] == repeated[0] == 200 and saved[1] == repeated[1]
        assert repeated[2]["x-celld-replayed"] == "true"
        assert request(port, "/saved", {"value": 99}, headers=call)[0] == 409
        def invoke(i):
            return request(port, "/overlap", {"name": str(i)}, headers={
                "x-celld-call-id": f"concurrent-call-{i:04}",
                "x-celld-context": json.dumps({"actor": f"actor-{i}"})})
        with ThreadPoolExecutor(max_workers=8) as executor:
            replies = list(executor.map(invoke, range(8)))
        for i, (status, raw, _) in enumerate(replies):
            assert status == 200, raw
            value = json.loads(raw)["result"]
            assert (value["name"], value["actor"], value["trace"]) == (str(i), f"actor-{i}", f"concurrent-call-{i:04}")
        assert max(json.loads(raw)["result"]["peak"] for _, raw, _ in replies) > 1
        assert json.loads(request(port, "/cleanup_count", {})[1]) == {"result": 8}
    with node(project, tmp_path / "restart.log") as port:
        assert json.loads(request(port, "/touch", {}, headers=call)[1]) == {"result": 1}
        repeated = request(port, "/saved", {}, headers=call)
        assert repeated[2]["x-celld-replayed"] == "true" and repeated[1] == saved[1]


def test_shared_fleet_state_concurrency_restart_and_numpy(tmp_path):
    project = build(ROOT / "examples" / "fleet.toml", tmp_path / "project")
    publish_local(project, tmp_path / "publish.log")
    context = {"x-celld-context": '{"actor":"Sam"}'}
    call = {**context, "x-celld-call-id": "persisted-call-000001"}
    with node(project, tmp_path / "node.log") as port:
        assert json.loads(request(port, "/hello/hello", {})[1]) == {"result": "Hello, world"}
        import sys
        endpoint = f"http://127.0.0.1:{port}/hello"
        result = subprocess.run([sys.executable, "-m", "celld.cli", "call", "hello", "name=Sam", "--endpoint", endpoint],
                                capture_output=True, text=True, timeout=60, check=True)
        assert json.loads(result.stdout) == "Hello, Sam"
        result = subprocess.run([sys.executable, "-m", "celld.cli", "functions", "--endpoint", endpoint],
                                capture_output=True, text=True, timeout=60, check=True)
        assert "hello(name: str = 'world') -> str" in result.stdout
        status, body, _ = request(port, "/math/mean", {"values": [1, 2, 6]})
        assert status == 200, (body, (tmp_path / "node.log").read_text())
        assert json.loads(body) == {"result": 3.0}
        status, body, _ = request(port, "/counters/increment", {"counter_id": "a", "amount": 2}, headers=call)
        assert status == 200, (body, (tmp_path / "node.log").read_text())
        assert json.loads(body)["result"] == {"total": 2, "last_actor": "Sam"}
        replay = request(port, "/counters/increment", {"counter_id": "a", "amount": 2}, headers=call)
        assert replay[2]["x-celld-replayed"] == "true"
        assert replay[1] == body
        assert request(port, "/counters/increment", {"counter_id": "a", "amount": 8}, headers=call)[0] == 409
        assert request(port, "/counters/increment", {"counter_id": "a"})[0] == 422
        assert request(port, "/counters/increment", {"counter_id": "a", "amount": -1}, headers=context)[0] == 422
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: request(port, "/counters/increment", {"counter_id": "a"}, headers=context), range(8)))
        assert all(status == 200 for status, _, _ in results), results
        assert sorted(json.loads(body)["result"]["total"] for _, body, _ in results) == list(range(3, 11))
        assert json.loads(request(port, "/counters/read", {"counter_id": "b"})[1])["result"]["total"] == 0
    with node(project, tmp_path / "restart.log") as port:
        assert json.loads(request(port, "/counters/read", {"counter_id": "a"})[1])["result"]["total"] == 10
        replay = request(port, "/counters/increment", {"counter_id": "a", "amount": 2}, headers=call)
        assert replay[2]["x-celld-replayed"] == "true"
        assert json.loads(replay[1])["result"]["total"] == 2


def test_generated_clients_and_host_context_hook(tmp_path):
    from test_codegen import load
    from celld import ClientInfo, RemoteError
    import asyncio

    # This fixture is platform policy, deliberately outside the OSS runtime.
    host = tmp_path / "platform.mjs"
    host.write_text('''export function resolveContext(request) {
      const scope = {'Bearer one':'one', 'Bearer two':'two'}[request.headers.get('authorization')];
      if (!scope) return Response.json({error:{code:'denied',message:'Host rejected caller'}},{status:403});
      return {scope, attributes:{principal:scope}};
    }''')
    project = build(ROOT / "examples" / "fleet.toml", tmp_path / "project", host=host)
    generated = load(project / "counter_client.py")
    publish_local(project, tmp_path / "publish.log")
    with node(project, tmp_path / "node.log") as port:
        endpoint = f"http://127.0.0.1:{port}/counters"
        one = generated.Client(endpoint, token="one", context=generated.Caller(actor="Sam"), client_info=ClientInfo(name="test"))
        two = generated.Client(endpoint, token="two", context=generated.Caller(actor="Sam"))
        assert "increment" in one.describe()["functions"]
        assert one.increment(counter_id="same", amount=2).total == 2
        assert two.read(counter_id="same").total == 0
        other = one.with_context(generated.Caller(actor="other"))
        assert other.increment(counter_id="same").last_actor == "other"
        assert one.increment(counter_id="same").last_actor == "Sam"
        details = one.details()
        assert (details.actor, details.scope, details.client, details.host, details.traced) == ("Sam", "one", "test", {"principal":"one"}, True)
        status, raw, _ = request(port, "/counters/details", {}, headers={
            "authorization":"Bearer one", "x-celld-scope":"forged", "x-celld-host":'{"principal":"forged"}',
            "x-celld-context":'{"actor":"Sam"}'})
        assert status == 200 and json.loads(raw)["result"]["host"] == {"principal":"one"}
        assert request(port, "/counters/__celld/schema")[0] == 403
        with pytest.raises(RemoteError, match="denied"):
            generated.Client(endpoint).read(counter_id="same")
        async def check():
            client = generated.AsyncClient(endpoint, token="one")
            assert (await client.read(counter_id="same")).total == 4
        asyncio.run(check())


def test_dev_reloads_python_sources_and_survives_invalid_edits(tmp_path):
    import sys
    from celld import Client
    from celld.build import lock

    target = tmp_path / "minimal"
    shutil.copytree(ROOT / "examples" / "minimal", target)
    # Existing locked artifacts make this test independent of the network.
    shutil.copytree(ROOT / "examples" / ".celld-python" / "cache", target / ".celld-python" / "cache")
    # A declared pure Python wheel outside the Pyodide catalog is bundled too.
    from test_build import wheel
    wheel(target / "demo-1.0-py3-none-any.whl")
    config = target / "pyproject.toml"
    config.write_text(config.read_text().replace("dependencies = []", 'dependencies = ["demo==1.0"]') +
                      '\n[tool.celld]\nwheels = ["demo-1.0-py3-none-any.whl"]\n')
    source = target / "src" / "app.py"
    source.write_text("from demo import VALUE\nassert VALUE == 42\n" + source.read_text())
    lock(target)
    port = port_number()
    log_path = tmp_path / "dev.log"
    with log_path.open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "celld.cli", "dev", str(target), "--port", str(port)],
                                   env=environment(), stdout=log, stderr=log)
        def wait_for(text, count=1):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if log_path.read_text().count(text) >= count:
                    return
                assert process.poll() is None, log_path.read_text()
                time.sleep(.1)
            pytest.fail(log_path.read_text())
        try:
            wait_for("ready http")
            client = Client(f"http://127.0.0.1:{port}")
            assert client.hello(name="Sam") == "Hello, Sam"
            source = target / "src" / "app.py"
            original = source.read_text()
            source.write_text(original + "\nthis is invalid python !!\n")
            wait_for("reload failed")
            assert client.hello(name="Sam") == "Hello, Sam"
            source.write_text(original.replace("Hello,", "Welcome,"))
            wait_for("ready http", 2)
            assert client.hello(name="Sam") == "Welcome, Sam"
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                pytest.fail("dev supervisor did not stop its child")


def test_wrangler_python_with_and_without_snapshot(tmp_path):
    from celld.build import lock
    app = tmp_path / "worker"
    shutil.copytree(ROOT / "examples/wrangler", app)
    shutil.copytree(ROOT / "examples/.celld-python/cache", app / ".celld-python/cache", dirs_exist_ok=True)
    lock(app)
    for snapshot in (False, True):
        project = build(app, tmp_path / f"bundle-{snapshot}", snapshot=snapshot)
        config = json.loads((project / 'wrangler.json').read_text())
        assert config['name'] == 'hello-wrangler'
        assert 'python_workers' not in config['compatibility_flags']
        assert config['main'] == 'host.js'
        publish_local(project, tmp_path / f"publish-{snapshot}.log")
        with node(project, tmp_path / f"node-{snapshot}.log") as port:
            status, body, _ = request(port, '/hello', {'name': 'Wrangler'})
            assert status == 200, body
            assert json.loads(body) == {'result': 'Hello, Wrangler!'}
            assert request(port, '/hello', {'name': []})[0] == 422


def test_snapshot_input_is_released_after_real_pyodide_restore(tmp_path):
    from celld.build import release_snapshot_input
    runtime = ROOT / "examples/.celld-python/cache/runtime/314.0.6"
    patched = tmp_path / "runtime"
    shutil.copytree(runtime, patched)
    loader = patched / "pyodide.mjs"
    loader.write_text(release_snapshot_input(loader.read_text()))
    script = ROOT / "tests/runtime/check-snapshot.mjs"
    result = subprocess.run(["node", str(script), str(runtime), str(patched)],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-6000:]
    result = json.loads(result.stdout.strip().splitlines()[-1])
    assert result["retained_input_bytes"] > 30_000_000
    assert result["released_input_bytes"] == 0
