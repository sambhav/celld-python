"""The local build/deploy interface. Transport details stay outside app code."""
import argparse
import json
import subprocess
from pathlib import Path

from .build import build, lock
from .codegen import generate


def main():
    parser = argparse.ArgumentParser(prog="celld-py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("lock", "build", "dev", "deploy"):
        command = commands.add_parser(name)
        command.add_argument("project", nargs="?", default=".", type=Path)
        if name != "lock":
            command.add_argument("--host", type=Path, help="ES module exporting resolveContext for your platform")
        if name == "build":
            command.add_argument("--out", type=Path)
        if name == "dev":
            command.add_argument("--port", type=int, default=9876)
            command.add_argument("--no-reload", action="store_true")
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
        if args.command == "lock":
            print(lock(args.project))
        elif args.command == "client":
            print(generate(json.loads(args.schema.read_text()), args.out))
        elif args.command == "dev":
            from .dev import run
            run(args.project, port=args.port, host=args.host, reload=not args.no_reload)
        else:
            output = build(args.project, getattr(args, "out", None), host=args.host)
            if args.command == "build":
                print(output)
            else:
                subprocess.run(["celld", "deploy", str(output), *extra], check=True)
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"celld-py: {error}\n")


if __name__ == "__main__":
    main()
