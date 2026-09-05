"""Compile the exact patched parser and existing packing core in a small harness."""
import argparse
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Patched upstream celld checkout")
    args = parser.parse_args()
    source = args.source.resolve()
    target = HERE / "build/checks"
    (target / "src").mkdir(parents=True, exist_ok=True)
    (target / "Cargo.toml").write_text('[package]\nname="packing-checks"\nversion="0.1.0"\nedition="2021"\n[dependencies]\nanyhow="=1.0.104"\n')
    imports = '\n'.join(f'#[path = {json.dumps(str(source / path))}]\nmod {name};' for name, path in [
        ("env_vars", "crates/celld/env_vars.rs"), ("isolate", "crates/logic/isolate.rs")])
    (target / "src/lib.rs").write_text('#![allow(dead_code)]\n' + imports + '\n' + (HERE / "checks.rs").read_text())
    subprocess.run(["cargo", "test", "--manifest-path", str(target / "Cargo.toml")], check=True)


if __name__ == "__main__":
    main()
