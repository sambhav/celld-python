"""Local supervisor for the pinned celld runtime.

v0.4.0's filesystem watcher rebuilds on reads. Publish through its dev command,
then run the same child topology while watching original Python inputs here.
The local SQLite object store is celld's S3 substitute for development only.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from .build import build, configurations


def environment():
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith("CELLD_") or key in {"AWS_DEFAULT_REGION", "AWS_REGION"}:
            env.pop(key, None)
    env.update(CELLD_V8_HEAP_LIMIT_MB="256", CELLD_MAX_STATELESS_ISOLATES="1")
    return env


def stop(process, log_path=None):
    requested = False
    if log_path and log_path.exists():
        match = re.search(r"^celld internal listening on (127\.0\.0\.1:\d+)", log_path.read_text(), re.M)
        if match:
            try:
                with urllib.request.urlopen(urllib.request.Request(
                    f"http://{match[1]}/shutdown?handoff=preserve", data=b"", method="POST"), timeout=2) as response:
                    requested = response.status == 200
            except OSError:
                pass
    if not requested:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def publish(project, binary, log_path):
    with log_path.open("w") as log:
        process = subprocess.Popen([binary, "dev", str(project), "--port", str(free_port())],
                                   env=environment(), stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if "ready " in log_path.read_text():
                    return
                if process.poll() is not None:
                    raise RuntimeError(log_path.read_text()[-5000:])
                time.sleep(.1)
            raise RuntimeError("celld local publication timed out: " + log_path.read_text()[-5000:])
        finally:
            stop(process)


def source_digest(target, host):
    root, _, apps = configurations(target)
    paths = {target if target.is_file() else target / "pyproject.toml", root / "celld.lock.json"}
    if host:
        paths.add(host)
    for app in apps:
        paths.add(app["directory"] / "pyproject.toml")
        paths.update((app["directory"] / app["source"]).rglob("*.py"))
    content = hashlib.sha256()
    for path in sorted(paths):
        content.update(str(path).encode())
        content.update(path.read_bytes())
    return content.hexdigest()


def run(target: Path, *, port=9876, host=None, reload=True):
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    binary = shutil.which("celld")
    if not binary:
        raise ValueError("Install celld 0.4.0 and put it on PATH")
    target = target.resolve()
    project = build(target, host=host)
    state = project / ".celld" / "dev"
    env = environment()
    node_name = "dev-" + hashlib.sha256(str(project).encode()).hexdigest()[:12]
    env.update(CELLD_INTERNAL_DEV_STORE=str(state / "objects.sqlite3"),
               CELLD_WATCH=str(state / "runtime"), CELLD_NODE=node_name)
    running = True

    def shutdown(signum, frame):
        nonlocal running
        running = False

    previous = {s: signal.signal(s, shutdown) for s in (signal.SIGTERM, signal.SIGINT)}
    process = None
    try:
        fingerprint = source_digest(target, host)
        while running:
            publish(project, binary, project / "publish.log")
            with (project / "node.log").open("w") as log:
                process = subprocess.Popen([binary, "--no-control-plane", "--bucket", "celld-dev",
                    "--listen", f"127.0.0.1:{port}", "--internal-listen", "127.0.0.1:0"],
                    env=env, stdout=log, stderr=log)
                deadline = time.monotonic() + 20
                while "celld listening on" not in (project / "node.log").read_text():
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError((project / "node.log").read_text()[-5000:])
                    time.sleep(.1)
                print(f"ready http://127.0.0.1:{port}  |  clients: {project}/*_client.py", flush=True)
                while running and process.poll() is None:
                    time.sleep(.3)
                    if reload:
                        try:
                            current = source_digest(target, host)
                            if current != fingerprint:
                                # Validate first; an invalid edit keeps the old worker serving.
                                build(target, host=host)
                                fingerprint = current
                                print("rebuilt; restarting worker", flush=True)
                                break
                        except (ValueError, OSError, subprocess.SubprocessError) as error:
                            print(f"reload failed: {error}", flush=True)
                            # Wait for a further edit instead of retrying an invalid import forever.
                            try:
                                fingerprint = source_digest(target, host)
                            except (ValueError, OSError):
                                pass
                if process.poll() is not None and running:
                    raise RuntimeError((project / "node.log").read_text()[-5000:])
                stop(process, project / "node.log")
                process = None
    finally:
        if process is not None:
            stop(process, project / "node.log")
        for s, handler in previous.items():
            signal.signal(s, handler)
