from __future__ import annotations

import base64
import inspect
import json
import re
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Generic, TypeVar, get_args, get_origin, get_type_hints
from urllib.parse import parse_qs, unquote

from pydantic import BaseModel, TypeAdapter, ValidationError

T = TypeVar("T", bound=BaseModel)
_JSON = TypeAdapter(Any)


@dataclass(frozen=True)
class Depends:
    provider: Callable


@dataclass
class State(Generic[T]):
    """A fresh, validated state value for this request; committed on success."""
    value: T


class HTTPError(Exception):
    def __init__(self, status: int, detail: Any):
        self.status, self.detail = status, detail
        super().__init__(str(detail))


@dataclass
class Request:
    method: str
    path: str
    query_string: str = ""
    header_items: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    context: dict[str, Any] = field(default_factory=dict)
    path_params: dict[str, str] = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        return {k.lower(): v for k, v in self.header_items}

    @property
    def query(self) -> dict[str, list[str]]:
        return parse_qs(self.query_string, keep_blank_values=True)

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError):
            raise HTTPError(422, "Expected a JSON request body") from None


@dataclass
class Response:
    body: bytes = b""
    status: int = 200
    headers: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def json(cls, value: Any, status: int = 200) -> Response:
        return cls(_JSON.dump_json(value), status, [("content-type", "application/json")])


def _annotation(annotation):
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0], get_args(annotation)[1:]
    return annotation, ()


def _state_models(provider, seen=None):
    seen = set() if seen is None else seen
    if provider in seen:
        raise ValueError("Dependency cycle")
    seen = seen | {provider}
    models = set()
    hints = get_type_hints(provider, include_extras=True)
    for name, param in inspect.signature(provider).parameters.items():
        annotation, extras = _annotation(hints.get(name, Any))
        deps = [x for x in extras if isinstance(x, Depends)]
        if isinstance(param.default, Depends):
            deps.append(param.default)
        if len(deps) > 1:
            raise ValueError(f"Multiple dependencies on {name}")
        if deps:
            models.update(_state_models(deps[0].provider, seen))
        elif get_origin(annotation) is State:
            model = get_args(annotation)[0]
            if not inspect.isclass(model) or not issubclass(model, BaseModel):
                raise TypeError("State requires a Pydantic model")
            models.add(model)
    return models


@dataclass
class Route:
    method: str
    path: str
    handler: Callable
    regex: re.Pattern
    names: list[str]
    state_model: type[BaseModel] | None
    state_key: str | None
    namespace: str | None


class Worker:
    def __init__(self):
        self.routes: list[Route] = []
        self.middlewares: list[Callable] = []

    def route(self, method: str, path: str, *, state_key=None, namespace=None):
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError("Routes must be absolute paths without query or fragment")
        names = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", path)
        if len(set(names)) != len(names):
            raise ValueError("Duplicate path parameter")
        parts = re.split(r"\{[A-Za-z_][A-Za-z0-9_]*\}", path)
        if any("{" in p or "}" in p for p in parts):
            raise ValueError("Invalid path template")
        pattern = "^" + "([^/]+)".join(re.escape(p) for p in parts) + "$"

        def register(handler):
            if any(r.method == method.upper() and r.regex.pattern == pattern for r in self.routes):
                raise ValueError(f"Duplicate route: {method} {path}")
            models = _state_models(handler)
            if len(models) > 1:
                raise ValueError("A route can use one state model")
            model = next(iter(models), None)
            if bool(model) != bool(state_key):
                raise ValueError("State[T] and state_key must be declared together")
            if state_key and state_key not in names:
                raise ValueError("state_key must name a path parameter")
            if namespace and model is None:
                raise ValueError("namespace requires state")
            ns = namespace or (model.__name__ if model else None)
            for r in self.routes:
                if ns and r.namespace == ns and r.state_model is not model:
                    raise ValueError("Routes in one namespace must use the same state model")
            self.routes.append(Route(method.upper(), path, handler, re.compile(pattern), names,
                                     model, state_key, ns))
            return handler
        return register

    def get(self, path, **options):
        return self.route("GET", path, **options)

    def post(self, path, **options):
        return self.route("POST", path, **options)

    def put(self, path, **options):
        return self.route("PUT", path, **options)

    def patch(self, path, **options):
        return self.route("PATCH", path, **options)

    def delete(self, path, **options):
        return self.route("DELETE", path, **options)

    def middleware(self, function):
        """First registered middleware runs outermost, including on 404/422."""
        self.middlewares.append(function)
        return function

    def describe(self) -> str:
        return json.dumps([dict(method=r.method, path=r.path, pattern=r.regex.pattern,
                                names=r.names, state_key=r.state_key, namespace=r.namespace)
                           for r in self.routes])

    def match(self, request: Request) -> Route:
        allowed = []
        for route in self.routes:
            match = route.regex.fullmatch(request.path)
            if match:
                allowed.append(route.method)
                if request.method == route.method:
                    request.path_params = dict(zip(route.names, map(unquote, match.groups())))
                    return route
        if allowed:
            raise HTTPError(405, "Method not allowed")
        raise HTTPError(404, "Not found")

    async def dispatch(self, request: Request, state_json: str | None = None) -> tuple[Response, str | None]:
        state = None
        committed = None
        async with AsyncExitStack() as stack:
            cache = {}

            async def resolve(provider):
                if provider in cache:
                    return cache[provider]
                hints = get_type_hints(provider, include_extras=True)
                values = {}
                for name, param in inspect.signature(provider).parameters.items():
                    annotation, extras = _annotation(hints.get(name, Any))
                    dep = next((x for x in extras if isinstance(x, Depends)), None)
                    if isinstance(param.default, Depends):
                        dep = param.default
                    if dep:
                        value = await resolve(dep.provider)
                    elif annotation is Request:
                        value = request
                    elif get_origin(annotation) is State:
                        value = state
                    else:
                        if name in request.path_params:
                            raw = request.path_params[name]
                        elif inspect.isclass(annotation) and issubclass(annotation, BaseModel):
                            raw = request.json()
                        elif name in request.query:
                            raw = request.query[name] if get_origin(annotation) is list else request.query[name][-1]
                        elif param.default is not inspect.Parameter.empty:
                            raw = param.default
                        else:
                            raise HTTPError(422, f"Missing parameter: {name}")
                        try:
                            value = TypeAdapter(annotation).validate_python(raw)
                        except ValidationError as error:
                            raise HTTPError(422, json.loads(error.json(include_url=False, include_input=False))) from None
                    values[name] = value
                if inspect.isasyncgenfunction(provider):
                    value = await stack.enter_async_context(asynccontextmanager(provider)(**values))
                elif inspect.isgeneratorfunction(provider):
                    value = stack.enter_context(contextmanager(provider)(**values))
                else:
                    value = provider(**values)
                    if inspect.isawaitable(value):
                        value = await value
                cache[provider] = value
                return value

            async def endpoint(req):
                nonlocal state
                try:
                    route = self.match(req)
                    if route.state_model:
                        # Stored state validation failures are server errors, not bad requests.
                        value = (route.state_model.model_validate_json(state_json)
                                 if state_json is not None else route.state_model())
                        state = State(value)
                    value = await resolve(route.handler)
                    if isinstance(value, Response):
                        return value
                    output = get_type_hints(route.handler).get("return", Any)
                    value = TypeAdapter(output).validate_python(value)
                    return Response.json(value)
                except HTTPError as error:
                    return Response.json({"detail": error.detail}, error.status)

            call = endpoint
            for middleware in reversed(self.middlewares):
                previous = call
                async def wrapped(req, middleware=middleware, previous=previous):
                    return await middleware(req, previous)
                call = wrapped
            try:
                response = await call(request)
            except HTTPError as error:
                response = Response.json({"detail": error.detail}, error.status)
            if not isinstance(response, Response):
                raise TypeError("Middleware must return Response")
        # Cleanup runs before the state is offered for commit. A cleanup failure aborts it.
        if state is not None and response.status < 400:
            # Revalidate mutations, including nested values, before persisting.
            model = type(state.value)
            committed = model.model_validate_json(state.value.model_dump_json()).model_dump_json()
        return response, committed

    async def handle_wire(self, payload: str, state_json: str | None = None) -> str:
        data = json.loads(payload)
        request = Request(data["method"], data["path"], data.get("query", ""),
                          data.get("headers", []), base64.b64decode(data.get("body", "")))
        response, state = await self.dispatch(request, state_json)
        return json.dumps(dict(status=response.status, headers=response.headers,
                               body=base64.b64encode(response.body).decode(), state=state))
