"""Lifecycle measurements using celld's real local store and operator state."""
import hashlib
import json
import re
import shutil
import subprocess
import time
import urllib.request
import urllib.error
from contextlib import contextmanager

from celld_python.dev import environment, free_port, stop


class RunningNode:
    def __init__(self, process, port, internal, startup_ms):
        self.process, self.port, self.internal, self.startup_ms = process, port, internal, startup_ms

    def state(self):
        with urllib.request.urlopen(f"http://{self.internal}/state", timeout=5) as response:
            return json.load(response)


@contextmanager
def measured_node(project, log_path, *, idle_seconds=None, node_suffix=None):
    port = free_port()
    env = environment()
    env.update(CELLD_INTERNAL_DEV_STORE=str(project / ".celld/dev/objects.sqlite3"),
               CELLD_WATCH=str(project / ".celld/dev/runtime"),
               CELLD_NODE="dev-" + hashlib.sha256(str(project).encode()).hexdigest()[:12], RUST_LOG="info")
    if node_suffix is not None:
        # Nodes share the object store, but never their local replication files.
        env["CELLD_NODE"] += "-" + node_suffix
        env["CELLD_WATCH"] = str(project / ".celld/dev" / ("runtime-" + node_suffix))
    if idle_seconds is not None:
        env["CELLD_IDLE_EVICT_S"] = str(idle_seconds)
    with log_path.open("w") as log:
        start = time.perf_counter_ns()
        process = subprocess.Popen([shutil.which("celld"), "--no-control-plane", "--bucket", "celld-dev",
            "--listen", f"127.0.0.1:{port}", "--internal-listen", "127.0.0.1:0"], env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                contents = log_path.read_text()
                internal = re.search(r"^celld internal listening on (127\.0\.0\.1:\d+)", contents, re.M)
                if "celld listening on" in contents and internal:
                    # Require both the operator census and public readiness.
                    running = RunningNode(process, port, internal[1], 0)
                    try:
                        running.state()
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/.well-known/celld/health", timeout=2) as response:
                            ready = response.status == 200
                    except (urllib.error.URLError, TimeoutError):
                        ready = False
                    if ready:
                        running.startup_ms = (time.perf_counter_ns() - start) / 1_000_000
                        yield running
                        return
                if process.poll() is not None:
                    raise RuntimeError(contents)
                time.sleep(.002)
            raise RuntimeError("Node startup timed out: " + log_path.read_text()[-5000:])
        finally:
            stop(process, log_path)


def wait_inactive(running, scopes, *, timeout=30):
    """Prove cells left residency without touching their public endpoints."""
    start = time.perf_counter_ns()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = running.state()
        if not set(scopes).intersection(state["residents"]) and state["evicting"] == 0 and state["releasing"] == 0:
            return {"wait_ms":(time.perf_counter_ns()-start)/1_000_000, "state":state}
        time.sleep(.05)
    raise RuntimeError("Idle cells did not leave residency: " + json.dumps(state))
