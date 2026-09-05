import importlib.util
import json
import sys
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field, ValidationError

from celld import Context, Depends, State, Worker
from celld.codegen import generate


class Caller(BaseModel):
    actor: str


class Item(BaseModel):
    name: str
    count: Annotated[int, Field(ge=0)] = 0
    tags: list[str] = Field(default_factory=list)
    kind: Literal["small", "large"] = "small"


class Stored(BaseModel):
    value: int = 0


def actor(ctx: Context[Caller]):
    return ctx.data.actor


def contract():
    app = Worker()

    @app.function(key="key")
    def save(key: str, item: Item, state: State[Stored], user=Depends(actor), amount: Annotated[int, Field(gt=0)] = 1) -> Item:
        return item

    return json.loads(app.schema())


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_sync_client_validates_arguments_and_returns_models(tmp_path):
    schema = contract()
    assert set(schema["functions"]["save"]["arguments"]["properties"]) == {"key", "item", "amount"}
    module = load(generate(schema, tmp_path / "generated.py"))
    client = module.Client("http://localhost", context=module.Caller(actor="Sam"))
    observed = []

    def call(name, **arguments):
        observed.append((name, arguments))
        return {"name": "saved", "count": 2, "tags": [], "kind": "large"}

    client.call = call
    value = client.save(key="a", item=module.Item(name="test"))
    assert isinstance(value, BaseModel) and value.name == "saved"
    assert observed == [("save", {"key": "a", "item": {"name": "test"}, "amount": 1})]
    with pytest.raises(ValidationError):
        client.save(key="a", item=module.Item(name="test"), amount=-1)
    assert len(observed) == 1
    assert isinstance(client.with_context(module.Caller(actor="other")), module.Client)


async def test_generated_async_client(tmp_path):
    module = load(generate(contract(), tmp_path / "generated_async.py"))
    client = module.AsyncClient("http://localhost")

    async def call(name, **arguments):
        return arguments["item"]

    client.call = call
    assert (await client.save(key="a", item=module.Item(name="test"))).name == "test"


def test_unsupported_structures_fail_clearly(tmp_path):
    schema = contract()
    schema["functions"]["save"]["returns"] = {"allOf": [{"type": "object"}]}
    with pytest.raises(ValueError, match="Unsupported schema construct"):
        generate(schema, tmp_path / "no.py")


def test_generated_names_cannot_overwrite_transport_or_another_function(tmp_path):
    spec = {"arguments": {"type": "object", "properties": {}, "additionalProperties": False},
            "returns": {"type": "string"}, "context": None}
    module = load(generate({"version": 1, "functions": {"call": spec, "call_": spec, "describe": spec}}, tmp_path / "names.py"))
    client = module.Client()
    client.call = lambda function, **arguments: function
    assert client.call_() == "call"
    assert client.call__() == "call_"
    assert client.describe_() == "describe"


class AliasedPerson(BaseModel):
    full_name: str = Field(validation_alias="inputName", serialization_alias="outputName")


async def test_generated_result_uses_the_serialized_model_contract(tmp_path):
    from celld import Request
    app = Worker()

    @app.function
    def person() -> AliasedPerson:
        return AliasedPerson(inputName="Sam")

    schema = json.loads(app.schema())
    assert "outputName" in schema["functions"]["person"]["returns"]["properties"]
    module = load(generate(schema, tmp_path / "alias_client.py"))
    response, _ = await app.dispatch(Request("POST", "/person", body=b'{}'))
    client = module.Client("http://localhost")
    client.call = lambda function, **arguments: json.loads(response.body)["result"]
    assert client.person().outputName == "Sam"
