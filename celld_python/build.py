"""Lock and bundle a fleet. No application code is executed by the builder."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tomllib
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

PYODIDE = "314.0.6"
CORE_FILES = ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def configurations(target: Path):
    target = target.resolve()
    if target.is_dir():
        target /= "pyproject.toml"
    data = tomllib.loads(target.read_text())
    root = target.parent
    if target.name == "pyproject.toml":
        name = data["project"]["name"]
        entries = [dict(name=name, path=".", mount="/")]
    else:
        name, entries = data["name"], data["apps"]
    apps = []
    for entry in entries:
        directory = (root / entry["path"]).resolve()
        project = tomllib.loads((directory / "pyproject.toml").read_text())
        settings = project.get("tool", {}).get("celld-python", {})
        app = dict(name=entry["name"], mount=entry.get("mount", "/"),
                   entrypoint=settings.get("entrypoint", "app:app"),
                   source=settings.get("source", "src"),
                   dependencies=project["project"].get("dependencies", []),
                   wheels=settings.get("wheels", []))
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", app["name"]):
            raise ValueError("App names must be lowercase URL-safe identifiers")
        if not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*", app["entrypoint"]):
            raise ValueError("entrypoint must be module:attribute")
        if not app["mount"].startswith("/") or any(c in app["mount"] for c in "?#%{}"):
            raise ValueError("mount must be a literal absolute URL path")
        app["mount"] = app["mount"].rstrip("/") or "/"
        if any(x["name"] == app["name"] or x["mount"] == app["mount"] for x in apps):
            raise ValueError("App names and mounts must be unique within a fleet")
        app["directory"] = directory
        apps.append(app)
    if not apps:
        raise ValueError("A fleet requires at least one app")
    return root, name, apps


def inputs_hash(apps):
    return digest(json.dumps([{k: v for k, v in a.items() if k != "directory"} for a in apps], sort_keys=True).encode())


def core(cache: Path):
    directory = cache / "runtime" / PYODIDE
    if all((directory / file).is_file() for file in CORE_FILES):
        return directory
    directory.mkdir(parents=True, exist_ok=True)
    # Fixed, official runtime package. npm validates registry integrity.
    result = subprocess.run(["npm", "pack", f"pyodide@{PYODIDE}", "--json", "--pack-destination", str(directory)],
                            text=True, capture_output=True, check=True)
    archive = directory / json.loads(result.stdout)[0]["filename"]
    with tarfile.open(archive) as tar:
        for name in CORE_FILES:
            stream = tar.extractfile("package/" + name)
            if stream is None:
                raise ValueError(f"Runtime missing {name}")
            (directory / name).write_bytes(stream.read())
    archive.unlink()
    return directory


def target_environment(info):
    env = default_environment()
    env.update(python_version=".".join(info["python"].split(".")[:2]),
               python_full_version=info["python"], sys_platform="emscripten",
               platform_system="Emscripten", platform_machine="wasm32", os_name="posix",
               implementation_name="cpython", implementation_version=info["python"], extra="")
    return env


def wheel_metadata(path: Path, info):
    name, version, _, tags = parse_wheel_filename(path.name)
    py = "cp" + "".join(info["python"].split(".")[:2])
    platform = f"pyemscripten_{info['abi_version']}_wasm32"
    if not any((t.platform == "any" and t.abi == "none" and t.interpreter in ("py3", "py2.py3", py)) or
               (t.platform == platform and t.interpreter == py and t.abi == py) for t in tags):
        raise ValueError(f"{path.name} is incompatible with Python {info['python']} / {platform}; native Linux/macOS wheels cannot run in WASM")
    with zipfile.ZipFile(path) as wheel:
        metadata = [n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError(f"Invalid wheel metadata: {path.name}")
        for member in wheel.namelist():
            if member.startswith("/") or ".." in Path(member).parts:
                raise ValueError(f"Unsafe wheel member in {path.name}")
        meta = BytesParser().parsebytes(wheel.read(metadata[0]))
    if canonicalize_name(meta["Name"]) != name or Version(meta["Version"]) != version:
        raise ValueError(f"Wheel filename and metadata disagree: {path.name}")
    if meta["Requires-Python"]:
        from packaging.specifiers import SpecifierSet
        if Version(info["python"]) not in SpecifierSet(meta["Requires-Python"]):
            raise ValueError(f"{name} does not support Python {info['python']}")
    return dict(name=name, version=str(version), requirements=meta.get_all("Requires-Dist", []))


def locked_packages(app, catalog, cache):
    info = catalog["info"]
    available = {canonicalize_name(k): dict(v) for k, v in catalog["packages"].items()}
    local = {}
    for supplied in app["wheels"]:
        path = (app["directory"] / supplied).resolve()
        meta = wheel_metadata(path, info)
        if meta["name"] in local:
            raise ValueError(f"Duplicate supplied wheel: {meta['name']}")
        local[meta["name"]] = (path, meta)
        available[meta["name"]] = dict(name=meta["name"], version=meta["version"], file_name=path.name,
            sha256=digest(path.read_bytes()), depends=[], imports=[], install_dir="site", package_type="package")
    selected = {}
    requested_extras = {}
    requirements = ["pydantic>=2.12,<3", *app["dependencies"]]
    env = target_environment(info)
    while requirements:
        text = requirements.pop(0)
        req = Requirement(text)
        if req.marker and not req.marker.evaluate(env):
            continue
        if req.url:
            raise ValueError("Declare URL dependencies as local wheels under tool.celld-python.wheels")
        name = canonicalize_name(req.name)
        if name not in available:
            raise ValueError(f"{name} is not in Pyodide {PYODIDE}; supply a pure Python or matching WASM wheel and its dependencies")
        package = available[name]
        if Version(package["version"]) not in req.specifier:
            raise ValueError(f"{req} conflicts with available {name}=={package['version']}")
        extras = requested_extras.get(name, set()) | req.extras
        if name in selected and extras == requested_extras[name]:
            continue
        requested_extras[name] = extras
        if name not in selected:
            file = cache / package["sha256"] / package["file_name"]
            file.parent.mkdir(parents=True, exist_ok=True)
            if not file.exists():
                if name in local:
                    file.write_bytes(local[name][0].read_bytes())
                else:
                    url = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE}/full/{package['file_name']}"
                    with urllib.request.urlopen(url, timeout=60) as result:
                        data = result.read()
                    if digest(data) != package["sha256"]:
                        raise ValueError(f"Checksum mismatch for {name}")
                    file.write_bytes(data)
            if digest(file.read_bytes()) != package["sha256"]:
                raise ValueError(f"Checksum mismatch for cached {name}")
            selected[name] = dict(package)
        file = cache / package["sha256"] / package["file_name"]
        deps = list(package["depends"])
        if file.suffix == ".whl":
            meta = wheel_metadata(file, info)
            for spec in meta["requirements"]:
                dependency = Requirement(spec)
                if not dependency.marker or any(dependency.marker.evaluate(dict(env, extra=x)) for x in ("", *extras)):
                    dependency.marker = None
                    deps.append(str(dependency))
        selected[name]["depends"] = sorted({canonicalize_name(Requirement(d).name) for d in deps})
        requirements.extend(deps)
    return dict(info=info, packages=dict(sorted(selected.items())))


def lock(target: Path):
    root, name, apps = configurations(target)
    cache = root / ".celld-python" / "cache"
    runtime = core(cache)
    catalog = json.loads((runtime / "pyodide-lock.json").read_text())
    result = dict(format=1, runtime=PYODIDE, inputs=inputs_hash(apps),
                  runtime_files={n: digest((runtime / n).read_bytes()) for n in CORE_FILES}, apps={})
    for app in apps:
        result["apps"][app["name"]] = locked_packages(app, catalog, cache / "packages")
    destination = root / "celld.lock.json"
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return destination


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"Pyodide port no longer matches the pinned runtime: {old[:70]}")
    return source.replace(old, new)


def port_runtime(runtime: Path, output: Path):
    # Small, version-pinned adapter. Preserve original upstream files in cache.
    source = (runtime / "pyodide.mjs").read_text()
    source = replace_once(source, 'import("ws")', 'Promise.reject(new Error("Node ws unavailable in celld"))')
    source = replace_once(source, '_(e+"pyodide.asm.wasm")', '{response:true}')
    source = replace_once(source, 'WebAssembly.instantiateStreaming(n,s)',
                          'Promise.resolve({instance:new WebAssembly.Instance(_pythonWasm,s),module:_pythonWasm})')
    source = ('import _pythonWasm from "./pyodide.asm.wasm";\n'
              'import {assetFetch as fetch} from "./assets.js";\n'
              'const process=undefined; const location="https://celld-python.invalid/runtime/";\n' + source)
    (output / "pyodide.mjs").write_text(source)
    source = (runtime / "pyodide.asm.mjs").read_text()
    source = replace_once(source, 'var ENVIRONMENT_IS_NODE=globalThis.process?.versions?.node&&globalThis.process?.type!="renderer";',
                          'var ENVIRONMENT_IS_NODE=false;')
    source = replace_once(source, 'typeof globalThis.MessageChannel=="function"', 'false')
    (output / "pyodide.asm.mjs").write_text('import {assetFetch as fetch} from "./assets.js";\n' + source)
    shutil.copyfile(runtime / "pyodide.asm.wasm", output / "pyodide.asm.wasm")


def build(target: Path, output: Path | None = None):
    root, name, apps = configurations(target)
    lock_path = root / "celld.lock.json"
    if not lock_path.is_file():
        raise ValueError("Missing celld.lock.json; run celld-py lock first")
    locked = json.loads(lock_path.read_text())
    if locked.get("format") != 1 or locked["runtime"] != PYODIDE or locked["inputs"] != inputs_hash(apps):
        raise ValueError("Configuration changed; run celld-py lock again")
    cache = root / ".celld-python" / "cache"
    runtime = cache / "runtime" / PYODIDE
    for file, sha in locked["runtime_files"].items():
        if digest((runtime / file).read_bytes()) != sha:
            raise ValueError(f"Runtime checksum mismatch: {file}")
    output = (output or root / ".celld-python" / "build").resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = Path(__file__).parent
    port_runtime(runtime, output)
    for file in ("host.js", "assets.js"):
        shutil.copyfile(package / "runtime" / file, output / file)
    assets = {"/runtime/python_stdlib.zip": base64.b64encode((runtime / "python_stdlib.zip").read_bytes()).decode()}
    manifest = []
    for app in sorted(apps, key=lambda a: len(a["mount"]), reverse=True):
        spec = locked["apps"][app["name"]]
        for pkg in spec["packages"].values():
            data = (cache / "packages" / pkg["sha256"] / pkg["file_name"]).read_bytes()
            if digest(data) != pkg["sha256"]:
                raise ValueError(f"Package checksum mismatch: {pkg['name']}")
            assets[f"/apps/{app['name']}/{pkg['file_name']}"] = base64.b64encode(data).decode()
        source = (app["directory"] / app["source"]).resolve()
        if not source.is_dir():
            raise ValueError(f"Missing application source directory: {source}")
        sources = {}
        for path in sorted(source.rglob("*.py")):
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError(f"Source symlink not permitted: {path}")
            relative = path.relative_to(source).as_posix()
            if relative.startswith("celld_python/"):
                raise ValueError("App sources cannot shadow celld_python")
            sources[relative] = path.read_text()
        manifest.append(dict(name=app["name"], mount=app["mount"], entrypoint=app["entrypoint"],
                             lock=spec, packages=list(spec["packages"]), sources=sources))
    sdk = {"celld_python/" + file: (package / file).read_text() for file in ("__init__.py", "app.py")}
    (output / "manifest.js").write_text("export const apps=" + json.dumps(manifest) + ";\nexport const sdk=" + json.dumps(sdk) + ";\n")
    (output / "asset-data.js").write_text("export const assets=" + json.dumps(assets) + ";\n")
    (output / "wrangler.json").write_text(json.dumps(dict(name=name, main="host.js", compatibility_date="2026-09-05",
        durable_objects=dict(bindings=[dict(name="PYTHON_CELLS", class_name="PythonCell")]),
        migrations=[dict(tag="v1", new_sqlite_classes=["PythonCell"])]), indent=2) + "\n")
    return output
