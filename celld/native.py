"""Use the native compiler/lifecycle for explicitly selected Python runtimes."""
import json
import subprocess
from pathlib import Path

from .wrangler import jsonc, resolve_target


def project(target: Path):
    config = resolve_target(target)
    if config.suffix not in {".json", ".jsonc"}:
        return None
    data = jsonc(config.read_text())
    if "python_runtime" not in data:
        return None
    if data["python_runtime"] not in {"pyodide", "monty"}:
        raise ValueError("python_runtime must be pyodide or monty")
    return config


def run(args, config, extra):
    if getattr(args, "host", None) or getattr(args, "out", None) or getattr(args, "no_snapshot", False):
        raise ValueError("Native Python projects use celld compiler hooks and runtime artifacts; SDK bundle flags do not apply")
    if args.command == "lock":
        raise ValueError("Native projects resolve declared dependencies during build; run pycelld build")
    command = ["celld", "dev" if args.command == "dev" else "deploy", str(config)]
    if args.command == "build":
        command.append("--dry-run")
    if args.command == "dev":
        if args.no_reload or args.idle_timeout is not None:
            raise ValueError("Native celld owns reload and idle eviction; SDK supervisor flags do not apply")
        command += ["--port", str(args.port)]
    subprocess.run(command + extra, check=True)


def create(directory: Path, runtime: str):
    from .scaffold import create as create_sdk
    # Reuse project-name validation and no-overwrite semantics.
    if any(directory.glob("wrangler.*")):
        raise ValueError("Wrangler project already exists")
    create_sdk(directory)
    source = directory / "src/app.py"
    source.write_text('''def hello(name: str = "world"):
    return f"Hello, {name}!"
''' if runtime == "monty" else '''from workers import Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("Hello, world!")
''')
    config = {"name": directory.resolve().name.lower().replace("_", "-"),
              "main": "src/app.py", "python_runtime": runtime,
              "compatibility_date": "2026-09-06"}
    if runtime == "pyodide":
        config["compatibility_flags"] = ["python_workers"]
    (directory / "wrangler.jsonc").write_text(json.dumps(config, indent=2) + "\n")
    return directory
