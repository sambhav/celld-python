"""Build the Rust experiment without changing the shipped Python build path."""
from pathlib import Path
import shutil
import subprocess

from celld_python.build import build, replace_once

HERE = Path(__file__).resolve().parent


def compile_runtime():
    subprocess.run(["cargo", "build", "--manifest-path", str(HERE / "runtime/Cargo.toml"),
                    "--locked", "--release", "--target", "wasm32-unknown-unknown"], check=True)
    subprocess.run(["wasm-bindgen", str(HERE / "runtime/target/wasm32-unknown-unknown/release/celld_python_runtime.wasm"),
                    "--target", "web", "--out-dir", str(HERE / "build"), "--out-name", "rust_runtime"], check=True)


def build_rust(target, output, *, host=None):
    output = build(target, output, host=host)
    for name in ("rust_runtime.js", "rust_runtime_bg.wasm"):
        shutil.copyfile(HERE / "build" / name, output / name)
    path = output / "host.js"
    source = path.read_text()
    source = replace_once(source, "import { loadPyodide } from './pyodide.mjs';\nimport createPyodideModule from './pyodide.asm.mjs';",
        "import {initSync, PythonRuntime} from './rust_runtime.js';\n"
        "import rustModule from './rust_runtime_bg.wasm';\ninitSync({module:rustModule});")
    start, end = source.index("async function boot(app) {"), source.index("function response(result) {")
    source = replace_once(source, source[start:end], "function boot(app) { return PythonRuntime.boot(app, sdk); }\n")
    source = replace_once(source, "runtime.worker.handle_wire(payload,before===null?undefined:before)", "runtime.invoke(payload,before)")
    path.write_text(source)
    return output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--out", type=Path, default=HERE / "build/project")
    args = parser.parse_args()
    compile_runtime()
    print(build_rust(args.project, args.out))
