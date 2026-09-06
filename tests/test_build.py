import json
import zipfile

import pytest

from celld.build import digest, locked_packages, replace_once, wheel_metadata

INFO = {"python": "3.14.2", "abi_version": "2026_0"}


def wheel(path, name="demo", version="1.0", requirements=""):
    with zipfile.ZipFile(path, "w") as file:
        file.writestr(f"{name}-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{requirements}")
        file.writestr(name + "/__init__.py", "VALUE = 42\n")
    return path


def test_pure_and_wasm_wheels_are_accepted_but_native_wheels_rejected(tmp_path):
    for tag in ("py3-none-any", "cp314-cp314-pyemscripten_2026_0_wasm32"):
        assert wheel_metadata(wheel(tmp_path / f"demo-1.0-{tag}.whl"), INFO)["name"] == "demo"
    for tag in ("cp314-cp314-manylinux_2_17_x86_64", "cp313-cp313-pyemscripten_2025_0_wasm32"):
        with pytest.raises(ValueError, match="incompatible"):
            wheel_metadata(wheel(tmp_path / f"demo-1.0-{tag}.whl"), INFO)


def test_local_wheel_dependencies_resolve_for_wasm_and_cache_offline(tmp_path):
    pure = wheel(tmp_path / "demo-1.0-py3-none-any.whl", requirements='Requires-Dist: helper==1.0; sys_platform == "emscripten"\nRequires-Dist: missing; sys_platform == "linux"\n')
    helper = wheel(tmp_path / "helper-1.0-py3-none-any.whl", name="helper")
    pydantic = wheel(tmp_path / "pydantic-2.13.5-py3-none-any.whl", name="pydantic", version="2.13.5")
    app = {"directory": tmp_path, "wheels": [p.name for p in (pure, helper, pydantic)], "dependencies": ["demo==1.0"]}
    catalog = {"info": INFO, "packages": {}}
    locked = locked_packages(app, catalog, tmp_path / "cache")
    assert set(locked["packages"]) == {"demo", "helper", "pydantic"}
    assert locked["packages"]["demo"]["depends"] == ["helper"]
    assert locked == locked_packages(app, catalog, tmp_path / "cache")
    item = locked["packages"]["demo"]
    (tmp_path / "cache" / item["sha256"] / item["file_name"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        locked_packages(app, catalog, tmp_path / "cache")


def test_runtime_patch_is_version_guarded():
    assert replace_once("before needle after", "needle", "replacement") == "before replacement after"
    for source in ("changed", "needle needle"):
        with pytest.raises(ValueError, match="pinned runtime"):
            replace_once(source, "needle", "replacement")


def test_prepare_hydrates_a_lock_without_changing_pins(tmp_path, monkeypatch):
    import celld.build as builder
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="demo"\n[tool.celld]\nwheels=["demo-1.0-py3-none-any.whl"]\n')
    supplied = wheel(tmp_path / 'demo-1.0-py3-none-any.whl')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'core').write_bytes(b'pinned')
    monkeypatch.setattr(builder, 'core', lambda _: runtime)
    _, _, apps = builder.configurations(tmp_path)
    sha = digest(supplied.read_bytes())
    locked = dict(format=1, runtime=builder.PYODIDE, inputs=builder.inputs_hash(apps),
        runtime_files={'core':digest(b'pinned')},
        apps={'demo':{'packages':{'demo':dict(name='demo',file_name=supplied.name,sha256=sha)}}})
    lock = tmp_path / 'celld.lock.json'
    lock.write_text(json.dumps(locked))
    original = lock.read_bytes()
    assert builder.prepare(tmp_path) == lock
    assert lock.read_bytes() == original
    cached = tmp_path / '.celld-python/cache/packages' / sha / supplied.name
    assert cached.read_bytes() == supplied.read_bytes()
    cached.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='Checksum mismatch'):
        builder.prepare(tmp_path)
    assert lock.read_bytes() == original
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="different"\n')
    with pytest.raises(ValueError, match='Configuration changed'):
        builder.prepare(tmp_path)
