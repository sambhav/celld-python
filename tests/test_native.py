import json
import sys

import pytest

from celld.cli import main
from celld.native import create, project


@pytest.mark.parametrize("runtime", ["pyodide", "monty"])
def test_native_projects_use_celld_without_sdk_bundling(tmp_path, monkeypatch, runtime):
    root = create(tmp_path / "hello", runtime)
    config = project(root)
    assert json.loads(config.read_text())["python_runtime"] == runtime
    invoked = []
    monkeypatch.setattr("celld.native.subprocess.run", lambda command, **kw: invoked.append(command))
    monkeypatch.setattr("celld.cli.prepare", lambda *a: pytest.fail("SDK prepare invoked"))
    monkeypatch.setattr("celld.cli.build", lambda *a, **kw: pytest.fail("SDK compiler invoked"))
    for command in ("dev", "build", "deploy"):
        monkeypatch.setattr(sys, "argv", ["pycelld", command, str(root)])
        main()
    assert invoked == [
        ["celld", "dev", str(config), "--port", "9876"],
        ["celld", "deploy", str(config), "--dry-run"],
        ["celld", "deploy", str(config)],
    ]
    with pytest.raises(ValueError):
        create(root, runtime)


def test_client_generation_from_native_contract(tmp_path, monkeypatch):
    from celld.client import Client
    schema = {"version":1,"runtime":"monty","functions":{"hello":{
        "arguments":{"type":"object","properties":{"name":{"type":"string"}},"required":[],"additionalProperties":False},
        "returns":{},"context":{},"stateful":False,"replay":False}}}
    monkeypatch.setattr(Client, "describe", lambda self: schema)
    out = tmp_path / "client.py"
    monkeypatch.setattr(sys,"argv",["pycelld","client","--endpoint","http://localhost:9876","--out",str(out)])
    main()
    assert "def hello(" in out.read_text()
    assert "async def hello(" in out.read_text()
