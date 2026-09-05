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

from celld_python.build import build

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


def test_shared_fleet_state_concurrency_restart_and_numpy(tmp_path):
    project = build(ROOT / "examples" / "fleet.toml", tmp_path / "project")
    publish_local(project, tmp_path / "publish.log")
    context = {"x-celld-context": '{"actor":"Sam"}'}
    call = {**context, "x-celld-call-id": "persisted-call-000001"}
    with node(project, tmp_path / "node.log") as port:
        assert json.loads(request(port, "/hello/hello", {})[1]) == {"result": "Hello, world"}
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
