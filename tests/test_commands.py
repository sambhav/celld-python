import importlib.util
import json
import sys

import pytest

from celld import Client
from celld.cli import main
from celld.commands import arguments, signatures
from celld.decorators import load_worker
from test_client import Reply, Transport


def test_named_cli_arguments_preserve_json_types_and_literal_strings():
    assert arguments(["name=Sam", "amount=2", "values=[1,2,6]", "ok=true", "empty=null", 'numeric="123"', "eq=a=b"]) == {
        "name":"Sam", "amount":2, "values":[1,2,6], "ok":True, "empty":None, "numeric":"123", "eq":"a=b"}
    assert arguments(["name=NaN"]) == {"name":"NaN"}
    for values in (["missing"], ["name=a", "name=b"], ["bad-name=x"]):
        with pytest.raises(ValueError):
            arguments(values)


def test_cli_calls_the_function_without_transport_syntax(monkeypatch, capsys):
    observed = []
    def call(self, name, /, **values):
        observed.append((self.endpoint, name, values, json.loads(self._context)))
        return {"message":"Hello, Sam"}
    monkeypatch.setattr(Client, "call", call)
    monkeypatch.setenv("CELLD_ENDPOINT", "http://localhost:9876/hello")
    monkeypatch.setattr(sys, "argv", ["pycelld", "call", "hello", "name=Sam", "--context", '{"actor":"Sam"}'])
    main()
    assert json.loads(capsys.readouterr().out) == {"message":"Hello, Sam"}
    assert observed == [("http://localhost:9876/hello", "hello", {"name":"Sam"}, {"actor":"Sam"})]


def test_bare_function_decorator_loads_without_an_app_instance(tmp_path):
    path = tmp_path / "single.py"
    path.write_text('from celld import function\n@function\ndef hello(name: str = "world") -> str:\n    return "Hello, " + name\n')
    spec = importlib.util.spec_from_file_location("single", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["single"] = module
    try:
        spec.loader.exec_module(module)
        assert module.hello() == "Hello, world"  # Still an ordinary Python function.
        schema = json.loads(load_worker("single").schema())
        assert list(signatures(schema)) == ["hello(name: str = 'world') -> str"]
    finally:
        sys.modules.pop("single", None)


def test_client_default_endpoint_and_schema_authentication(monkeypatch):
    monkeypatch.delenv("CELLD_ENDPOINT", raising=False)
    assert Client().endpoint == "http://127.0.0.1:9876"
    monkeypatch.setenv("CELLD_ENDPOINT", "https://example.test/worker")
    client = Client(token="test")
    schema = {"version":1,"functions":{}}
    transport = Transport(Reply(schema))
    client._opener = transport
    assert client.describe() == schema
    assert transport.requests[0].full_url == "https://example.test/worker/__celld/schema"
    assert transport.requests[0].get_header("Authorization") == "Bearer test"
    assert Client("http://localhost").endpoint == "http://localhost"


async def test_async_describe_and_schema_errors():
    from celld import AsyncClient, RemoteError
    client = AsyncClient()
    client._opener = Transport(Reply({"version":1,"functions":{}}))
    assert (await client.describe())["functions"] == {}
    for value, code in [({"version":2,"functions":{}}, "protocol_error"),
                        ({"error":{"code":"denied","message":"No"}}, "denied")]:
        client._opener = Transport(Reply(value))
        with pytest.raises(RemoteError, match=code):
            await client.describe()


def test_dev_prepares_only_a_missing_lock_and_forwards_idle_policy(tmp_path, monkeypatch):
    from celld import cli, dev
    from celld.scaffold import create
    project = create(tmp_path / "hello")
    locked, runs = [], []
    def prepare(target):
        locked.append(target)
        (target / "celld.lock.json").write_text("existing pin")
    monkeypatch.setattr(cli, "lock", prepare)
    monkeypatch.setattr(dev, "run", lambda target, **options: runs.append((target, options)))
    monkeypatch.setattr(sys, "argv", ["pycelld", "dev", str(project), "--idle-timeout", "60"])
    main()
    main()
    assert locked == [project]
    assert len(runs) == 2 and all(options["idle_timeout"] == 60 for _, options in runs)
    assert (project / "celld.lock.json").read_text() == "existing pin"


def test_dev_uses_celld_default_parallelism_without_inheriting_fleet_settings(monkeypatch):
    from celld.dev import environment
    monkeypatch.setenv("CELLD_MAX_STATELESS_ISOLATES", "1")
    monkeypatch.setenv("CELLD_BUCKET", "production")
    env = environment()
    assert "CELLD_MAX_STATELESS_ISOLATES" not in env
    assert "CELLD_BUCKET" not in env
