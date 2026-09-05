import argparse
import subprocess
from pathlib import Path

from .build import build, lock


def main():
    parser = argparse.ArgumentParser(prog="celld-py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("lock", "build", "dev", "deploy"):
        command = commands.add_parser(name)
        command.add_argument("project", nargs="?", default=".", type=Path)
        if name in ("dev", "deploy"):
            command.add_argument("celld_args", nargs=argparse.REMAINDER, help="Arguments forwarded to celld, after --")
    args = parser.parse_args()
    try:
        if args.command == "lock":
            print(lock(args.project))
            return
        output = build(args.project)
        if args.command == "build":
            print(output)
            return
        extra = args.celld_args
        if extra[:1] == ["--"]:
            extra = extra[1:]
        subprocess.run(["celld", args.command, str(output), *extra], check=True)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"celld-py: {error}\n")


if __name__ == "__main__":
    main()
