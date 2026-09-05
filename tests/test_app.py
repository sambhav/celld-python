import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel, Field, ValidationError

from celld import Depends, HTTPError, Request, Response, State, Worker


class Count(BaseModel):
    value: int = Field(default=0, ge=0)


class Add(BaseModel):
    amount: int = Field(gt=0)


def example():
    file = Path(__file__).parents[1] / "examples/hello/src/app.py"
    spec = importlib.util.spec_from_file_location("hello_example", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


async def test_hello_example_and_pydantic_validation():
    app = example()
    response, _ = await app.dispatch(Request("GET", "/"))
    assert json.loads(response.body) == {"message": "Hello from Python on celld!"}
    response, _ = await app.dispatch(Request("POST", "/greet", body=b'{"name":"Sam"}'))
    assert json.loads(response.body) == {"message": "Hello, Sam!"}
    response, _ = await app.dispatch(Request("POST", "/greet", body=b'{"name":""}'))
    assert response.status == 422
    assert (await app.dispatch(Request("POST", "/greet", body=b'invalid')))[0].status == 422
    assert (await app.dispatch(Request("DELETE", "/greet")))[0].status == 405
    assert (await app.dispatch(Request("GET", "/missing")))[0].status == 404


async def test_middleware_dependency_cache_and_cleanup():
    app, events = Worker(), []

    async def resource(request: Request):
        events.append("open:" + request.path_params["name"])
        try:
            yield request.path_params["name"]
        finally:
            events.append("close")

    async def nested(value=Depends(resource)):
        return value

    @app.middleware
    async def outer(request, call_next):
        events.append("outer-before")
        response = await call_next(request)
        events.append("outer-after")
        response.headers.append(("x-middleware", "yes"))
        return response

    @app.middleware
    async def inner(request, call_next):
        events.append("inner-before")
        response = await call_next(request)
        events.append("inner-after")
        return response

    @app.get("/{name}")
    async def endpoint(a=Depends(resource), b=Depends(nested)):
        return {"a": a, "b": b}

    response, _ = await app.dispatch(Request("GET", "/Sam%20K"))
    assert json.loads(response.body) == {"a": "Sam K", "b": "Sam K"}
    assert events == ["outer-before", "inner-before", "open:Sam K", "inner-after", "outer-after", "close"]
    assert ("x-middleware", "yes") in response.headers


async def test_state_commit_and_rollback():
    app = Worker()

    @app.post("/{name}", state_key="name", namespace="counters")
    def add(body: Add, count: State[Count]):
        count.value.value += body.amount
        return count.value

    response, state = await app.dispatch(Request("POST", "/a", body=b'{"amount":3}'), '{"value":2}')
    assert response.status == 200 and json.loads(state) == {"value": 5}
    response, state = await app.dispatch(Request("POST", "/a", body=b'{"amount":-1}'), '{"value":2}')
    assert response.status == 422 and state is None

    @app.middleware
    async def deny(request, call_next):
        await call_next(request)
        raise HTTPError(403, "Denied")

    response, state = await app.dispatch(Request("POST", "/a", body=b'{"amount":3}'), '{"value":2}')
    assert response.status == 403 and state is None


async def test_request_dependencies_are_separate_under_concurrency():
    app = Worker()

    async def value(request: Request):
        await asyncio.sleep(0)
        return request.path_params["name"]

    @app.get("/{name}")
    async def endpoint(v=Depends(value)):
        await asyncio.sleep(0)
        return v

    responses = await asyncio.gather(*(app.dispatch(Request("GET", f"/{i}")) for i in range(25)))
    assert [json.loads(r.body) for r, _ in responses] == list(map(str, range(25)))


async def test_cleanup_failure_prevents_commit_and_output_validation_is_server_error():
    app = Worker()

    async def bad_cleanup():
        yield 1
        raise RuntimeError("cleanup failed")

    @app.post("/{name}", state_key="name")
    def endpoint(count: State[Count], resource=Depends(bad_cleanup)):
        count.value.value += 1
        return count.value

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await app.dispatch(Request("POST", "/a"))

    other = Worker()

    @other.get("/")
    def invalid_output() -> int:
        return "bad"

    with pytest.raises(ValidationError):
        await other.dispatch(Request("GET", "/"))


def test_registration_rejects_ambiguous_state_and_routes():
    app = Worker()
    with pytest.raises(ValueError, match="declared together"):
        @app.get("/{name}")
        def no_key(state: State[Count]):
            pass
    with pytest.raises(ValueError, match="path parameter"):
        @app.get("/{name}", state_key="missing")
        def wrong_key(state: State[Count]):
            pass
    @app.get("/{name}")
    def first():
        pass
    with pytest.raises(ValueError, match="Duplicate route"):
        @app.get("/{other}")
        def duplicate():
            pass


async def test_binary_wire_and_duplicate_headers():
    app = Worker()

    @app.get("/")
    def endpoint():
        return Response(b'\x00\xff', headers=[("set-cookie", "a=1"), ("set-cookie", "b=2")])

    value = json.loads(await app.handle_wire('{"method":"GET","path":"/"}'))
    assert value["body"] == "AP8="
    assert value["headers"] == [["set-cookie", "a=1"], ["set-cookie", "b=2"]]
