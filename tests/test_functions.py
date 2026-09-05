import asyncio
import json
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from celld import Context, Depends, Error, Invocation, Request, State, Worker


class Caller(BaseModel):
    actor: str


class Value(BaseModel):
    count: int = Field(default=0, ge=0)


async def test_function_context_and_dependency_lifetime_are_call_local():
    app, events = Worker(), []

    async def resource(ctx: Context[Caller]):
        events.append("open:" + ctx.data.actor)
        try:
            yield ctx
        finally:
            events.append("close:" + ctx.data.actor)

    @app.middleware
    async def trace(invocation: Invocation, call_next):
        invocation.context.local["trace"] = invocation.context.call_id
        return await call_next(invocation)

    @app.function
    async def hello(name: str, first=Depends(resource), second=Depends(resource)) -> dict:
        assert first is second
        await asyncio.sleep(0)
        return {"name": name, "actor": first.data.actor, "trace": first.local["trace"],
                "scope": first.scope, "client": first.client.name, "host": first.host}

    async def invoke(i):
        return await app.dispatch(Request("POST", "/hello", body=json.dumps({"name": str(i)}).encode(),
            header_items=[("x-celld-context", json.dumps({"actor": str(i)})), ("x-celld-call-id", str(i)),
                          ("x-celld-scope", "platform"), ("x-celld-host", '{"principal":"verified"}'),
                          ("x-celld-client", '{"name":"test"}')]))

    results = await asyncio.gather(*(invoke(i) for i in range(8)))
    for i, (response, _) in enumerate(results):
        assert json.loads(response.body)["result"] == {"name": str(i), "actor": str(i), "trace": str(i),
            "scope": "platform", "client": "test", "host": {"principal": "verified"}}
    assert len(events) == 16


async def test_function_failure_and_middleware_failure_roll_back_state():
    app = Worker()

    @app.function(key="key")
    def change(key: str, state: State[Value], fail: bool = False) -> int:
        state.value.count += 1
        if fail:
            raise Error("expected", code="denied")
        return state.value.count

    response, state = await app.dispatch(Request("POST", "/change", body=b'{"key":"a","fail":true}'), '{"count":5}')
    assert json.loads(response.body)["error"]["code"] == "denied" and state is None

    @app.middleware
    async def deny(invocation, call_next):
        await call_next(invocation)
        raise Error("late denial")

    response, state = await app.dispatch(Request("POST", "/change", body=b'{"key":"a"}'), '{"count":5}')
    assert response.status == 400 and state is None


async def test_invalid_context_and_unknown_arguments_fail_before_execution():
    app = Worker()

    @app.function
    def hello(ctx: Context[Caller]):
        pytest.fail("must not run")

    for data, headers in [(b'{}', []), (b'{}', [("x-celld-context", '[]')]),
                          (b'{"extra":1}', [("x-celld-context", '{"actor":"a"}')])]:
        response, state = await app.dispatch(Request("POST", "/hello", body=data, header_items=headers))
        assert response.status == 422 and state is None


def test_ambiguous_function_field_aliases_fail_at_build_time():
    app = Worker()

    @app.function
    def hello(name: Annotated[str, Field(alias="user")]):
        return name

    with pytest.raises(ValueError, match="Python names"):
        app.schema()


class AliasedState(BaseModel):
    count: int = Field(default=0, validation_alias="seed", serialization_alias="total")


async def test_state_round_trips_python_fields_independently_of_wire_aliases():
    app = Worker()

    @app.function(key="key")
    def increment(key: str, state: State[AliasedState]) -> AliasedState:
        state.value.count += 1
        return state.value

    state = None
    for value in (1, 2):
        response, state = await app.dispatch(Request("POST", "/increment", body=b'{"key":"a"}'), state)
        assert json.loads(state) == {"count": value}
        assert json.loads(response.body) == {"result": {"total": value}}


async def test_warm_validation_keeps_constraints_defaults_and_dependencies_isolated():
    app, opened, closed = Worker(), [], []

    async def resource(ctx: Context):
        opened.append(ctx.call_id)
        try:
            yield ctx.call_id
        finally:
            closed.append(ctx.call_id)

    @app.function
    async def check(name: Annotated[str, Field(min_length=2)],
                    trace=Depends(resource), values: list[int] = []) -> dict:
        values.append(len(name))
        await asyncio.sleep(0)
        return {"values": values, "trace": trace}

    async def invoke(i, body):
        return (await app.dispatch(Request("POST", "/check", body=body,
            header_items=[("x-celld-call-id", str(i))])))[0]

    # Warm compiled metadata, then interleave valid calls and invalid input.
    assert (await invoke("warm", b'{"name":"ok"}')).status == 200
    results = await asyncio.gather(*(invoke(i, b'{"name":"ok"}') for i in range(16)))
    for i, response in enumerate(results):
        assert json.loads(response.body)["result"] == {"values": [2], "trace": str(i)}
    for body in (b'{"name":"x"}', b'{"name":"ok","extra":1}', b'{"name":"ok","values":["bad"]}'):
        assert (await invoke("invalid", body)).status == 422
    assert sorted(opened) == sorted(closed)
    assert len([item for item in opened if item != "invalid"]) == 17


async def test_warm_output_validator_keeps_enforcing_constraints():
    from pydantic import ValidationError
    app = Worker()

    @app.function
    def absolute(value: int) -> Annotated[int, Field(ge=0)]:
        return value

    assert (await app.dispatch(Request("POST", "/absolute", body=b'{"value":1}')))[0].status == 200
    with pytest.raises(ValidationError):
        await app.dispatch(Request("POST", "/absolute", body=b'{"value":-1}'))
