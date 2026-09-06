"""The local build/deploy interface. Transport details stay outside app code."""
import argparse
import json
import subprocess
from pathlib import Path

from .build import build, configurations, lock
from .codegen import generate


def main():
    parser = argparse.ArgumentParser(prog="pycelld", description="Typed Python functions and durable state on celld.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a hello-world worker")
    init.add_argument("directory", type=Path)
    descriptions = {
        "lock": "Pin and fetch the runtime and declared Python packages",
        "build": "Bundle workers, packages, schemas, and typed clients",
        "dev": "Run locally and reload when Python sources change",
        "deploy": "Build and deploy using the native celld CLI",
    }
    for name, description in descriptions.items():
        command = commands.add_parser(name, help=description, description=description)
        command.add_argument("project", nargs="?", default=".", type=Path, help="Project directory, Wrangler JSONC, or fleet TOML (default: current directory)")
        if name != "lock":
            command.add_argument("--host", type=Path, help="ES module exporting resolveContext for your platform")
        if name == "build":
            command.add_argument("--out", type=Path)
            command.add_argument("--no-snapshot", action="store_true", help="Build without the interpreter snapshot for comparison")
        if name == "dev":
            command.add_argument("--port", type=int, default=9876)
            command.add_argument("--no-reload", action="store_true")
            command.add_argument("--idle-timeout", type=int, help="Release idle cells after this many seconds")
    for name in ("call", "functions"):
        command = commands.add_parser(name, help="Call a deployed function" if name == "call" else "List deployed function signatures")
        command.add_argument("--endpoint", help="Defaults to CELLD_ENDPOINT or local dev on port 9876")
        command.add_argument("--token", help="Optional platform bearer token")
        if name == "call":
            command.add_argument("function")
            command.add_argument("arguments", nargs="*", metavar="name=value")
            command.add_argument("--context", default="{}", help="JSON caller context")
            command.add_argument("--call-id", help="Recover one logical call using its original ID")
        else:
            command.add_argument("--json", action="store_true", help="Print the full client contract")
    client = commands.add_parser("client", help="Generate a client from a saved schema")
    client.add_argument("schema", type=Path)
    client.add_argument("--out", required=True, type=Path)
    import sys
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        index = argv.index("--")
        argv, extra = argv[:index], argv[index + 1:]
    args = parser.parse_args(argv)
    if extra and args.command != "deploy":
        parser.error("Only deploy accepts forwarded celld arguments after --")
    try:
        if args.command == "init":
            from .scaffold import create
            path = create(args.directory)
            print(f"Created {path}. Start it with: pycelld dev {path}")
        elif args.command == "lock":
            print(lock(args.project))
        elif args.command == "client":
            print(generate(json.loads(args.schema.read_text()), args.out))
        elif args.command == "dev":
            from .dev import run
            if args.idle_timeout is not None and args.idle_timeout < 1:
                raise ValueError("idle-timeout must be a positive number of seconds")
            root, _, _ = configurations(args.project)
            if not (root / "celld.lock.json").exists():
                print("Preparing the pinned runtime and declared packages...", flush=True)
                lock(args.project)
            run(args.project, port=args.port, host=args.host, reload=not args.no_reload, idle_timeout=args.idle_timeout)
        elif args.command in {"call", "functions"}:
            from .client import Client, RemoteError
            from .commands import arguments, signatures
            client = Client(args.endpoint, token=args.token)
            try:
                if args.command == "functions":
                    schema = client.describe()
                    print(json.dumps(schema, indent=2) if args.json else "\n".join(signatures(schema)))
                else:
                    client = client.with_context(json.loads(args.context))
                    if args.call_id:
                        client = client.with_call_id(args.call_id)
                    print(json.dumps(client.call(args.function, **arguments(args.arguments)), indent=2, ensure_ascii=False))
            except RemoteError as error:
                recovery = f" (call ID: {error.call_id})" if error.call_id else ""
                parser.exit(1, f"pycelld: {error}{recovery}\n")
        else:
            output = build(args.project, getattr(args, "out", None), host=args.host, snapshot=not getattr(args, "no_snapshot", False))
            if args.command == "build":
                print(output)
            else:
                subprocess.run(["celld", "deploy", str(output), *extra], check=True)
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"pycelld: {error}\n")


if __name__ == "__main__":
    main()
