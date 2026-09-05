from __future__ import annotations

import base64
import inspect
import json
import re
import uuid
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Generic, TypeVar, get_args, get_origin, get_type_hints
from urllib.parse import parse_qs, unquote

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, create_model
from pydantic.fields import FieldInfo

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


class Error(Exception):
    """An expected application failure returned to the caller."""
    def __init__(self, message: str, *, code: str = "application_error"):
        self.code = code
        super().__init__(message)


class ClientInfo(BaseModel):
    """Caller-declared information, never an authenticated identity."""
    name: str = Field(default="python", max_length=128)
    version: str = Field(default="", max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class Context(Generic[T]):
    """Per-call data plus trusted host attributes supplied by a platform adapter."""
    data: T
    scope: str
    function: str
    call_id: str
    request_id: str
    attempt: int = 1
    client: ClientInfo = field(default_factory=ClientInfo)
    host: dict[str, Any] = field(default_factory=dict)
    local: dict[str, Any] = field(default_factory=dict)


@dataclass
class Invocation:
    function: str
    arguments: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    context: Context | None = None


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


def _state_models(provider, seen=None, kind=State):
    seen = set() if seen is None else seen
    if provider in seen:
        raise ValueError("Dependency cycle")
    seen = seen | {provider}
    models = set()
    hints = get_type_hints(provider, include_extras=True)
    for name, param in inspect.signature(provider).parameters.items():
        if param.kind in (param.POSITIONAL_ONLY, param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError("Worker functions and dependencies require named parameters")
        annotation, extras = _annotation(hints.get(name, Any))
        deps = [x for x in extras if isinstance(x, Depends)]
        if isinstance(param.default, Depends):
            deps.append(param.default)
        if len(deps) > 1:
            raise ValueError(f"Multiple dependencies on {name}")
        if deps:
            models.update(_state_models(deps[0].provider, seen, kind))
        elif get_origin(annotation) is kind:
            model = get_args(annotation)[0]
            if not inspect.isclass(model) or not issubclass(model, BaseModel):
                raise TypeError(f"{kind.__name__} requires a Pydantic model")
            models.add(model)
    return models


def _inputs(provider):
    fields = {}
    hints = get_type_hints(provider, include_extras=True)
    for name, param in inspect.signature(provider).parameters.items():
        annotation, extras = _annotation(hints.get(name, Any))
        dep = next((x for x in extras if isinstance(x, Depends)), None)
        if isinstance(param.default, Depends):
            dep = param.default
        if dep:
            nested = _inputs(dep.provider)
            for field_name in fields.keys() & nested.keys():
                if fields[field_name] != nested[field_name]:
                    raise ValueError(f"Conflicting dependency argument: {field_name}")
            fields.update(nested)
        elif annotation not in (Request, Invocation, Context) and get_origin(annotation) not in (State, Context):
            if isinstance(param.default, FieldInfo):
                raise ValueError("Use Annotated[T, Field(...)] for constraints and ordinary Python parameter defaults")
            if any(isinstance(x, FieldInfo) and (x.alias is not None or x.validation_alias is not None) for x in extras):
                raise ValueError("Function arguments use their Python names; put aliased fields in a Pydantic model")
            schema = Annotated[annotation, *extras] if extras else annotation
            value = (schema, ... if param.default is inspect.Parameter.empty else param.default)
            if name in fields and fields[name] != value:
                raise ValueError(f"Conflicting dependency argument: {name}")
            fields[name] = value
    return fields


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
    operation: str | None = None
    context_model: type[BaseModel] | None = None


class Worker:
    def __init__(self):
        self.routes: list[Route] = []
        self.middlewares: list[Callable] = []

    def route(self, method: str, path: str, *, state_key=None, namespace=None, _operation=None):
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
            contexts = _state_models(handler, kind=Context)
            if len(contexts) > 1:
                raise ValueError("A function must use one context model")
            context_model = next(iter(contexts), None)
            if bool(model) != bool(state_key):
                raise ValueError("State[T] and state_key must be declared together")
            if state_key and not _operation and state_key not in names:
                raise ValueError("state_key must name a path parameter")
            if state_key and _operation:
                hints = get_type_hints(handler, include_extras=True)
                if _annotation(hints.get(state_key))[0] is not str:
                    raise ValueError("A function state key must name a required string argument")
                if inspect.signature(handler).parameters[state_key].default is not inspect.Parameter.empty:
                    raise ValueError("A function state key must be a required argument")
            if namespace and model is None:
                raise ValueError("namespace requires state")
            ns = namespace or (model.__name__ if model else None)
            for r in self.routes:
                if ns and r.namespace == ns and r.state_model is not model:
                    raise ValueError("Routes in one namespace must use the same state model")
            self.routes.append(Route(method.upper(), path, handler, re.compile(pattern), names,
                                     model, state_key, ns, _operation, context_model))
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
        """First registered middleware runs outermost."""
        self.middlewares.append(function)
        return function

    def function(self, handler=None, *, key=None, namespace=None):
        def register(function):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", function.__name__):
                raise ValueError("Exported function names must be ASCII identifiers without a leading underscore")
            return self.route("POST", "/" + function.__name__, state_key=key,
                              namespace=namespace, _operation=function.__name__)(function)
        return register(handler) if handler is not None else register

    def schema(self) -> str:
        functions = {}
        for route in self.routes:
            if not route.operation:
                continue
            arguments = create_model(route.operation + "Arguments", __config__=ConfigDict(extra="forbid"),
                                     **_inputs(route.handler))
            functions[route.operation] = dict(
                description=inspect.getdoc(route.handler) or "",
                arguments=arguments.model_json_schema(),
                returns=TypeAdapter(get_type_hints(route.handler, include_extras=True).get("return", Any)).json_schema(),
                context=route.context_model.model_json_schema() if route.context_model else None,
                stateful=route.state_model is not None,
            )
        return json.dumps(dict(version=1, functions=functions))

    def describe(self) -> str:
        return json.dumps([dict(method=r.method, path=r.path, pattern=r.regex.pattern,
                                names=r.names, state_key=r.state_key, namespace=r.namespace, operation=r.operation)
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
        try:
            matched = self.match(request)
        except HTTPError:
            matched = None
        invocation = None
        if matched and matched.operation:
            try:
                arguments = request.json() if request.body else {}
                if not isinstance(arguments, dict):
                    raise HTTPError(422, "Arguments must be an object")
                encoded = request.headers.get("x-celld-context", request.headers.get("x-celld-metadata", "{}"))
                if len(encoded) > 16384:
                    raise HTTPError(422, "Context exceeds 16 KiB")
                metadata = json.loads(encoded)
                if not isinstance(metadata, dict):
                    raise HTTPError(422, "Metadata must be an object")
                data = matched.context_model.model_validate(metadata) if matched.context_model else metadata
                call_id = request.headers.get("x-celld-call-id", str(uuid.uuid4()))
                ctx = Context(data, request.headers.get("x-celld-scope", "default"), matched.operation,
                              call_id, request.headers.get("x-celld-request-id", call_id),
                              int(request.headers.get("x-celld-attempt", "1")),
                              ClientInfo.model_validate_json(request.headers.get("x-celld-client", "{}")),
                              json.loads(request.headers.get("x-celld-host", "{}")))
                if not 1 <= ctx.attempt <= 100:
                    raise ValueError("Attempt must be between 1 and 100")
                invocation = Invocation(matched.operation, arguments, metadata, ctx)
                fields = _inputs(matched.handler)
                unknown = arguments.keys() - fields.keys()
                if unknown:
                    raise HTTPError(422, f"Unknown arguments: {', '.join(sorted(unknown))}")
            except (HTTPError, ValueError) as error:
                return Response.json({"error": {"code": "validation_error", "message": str(error)}}, 422), None
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
                    elif annotation is Invocation:
                        value = invocation
                    elif annotation is Context or get_origin(annotation) is Context:
                        value = invocation.context
                    elif get_origin(annotation) is State:
                        value = state
                    else:
                        if invocation is not None:
                            if name in invocation.arguments:
                                raw = invocation.arguments[name]
                            elif param.default is not inspect.Parameter.empty:
                                raw = param.default
                            else:
                                raise HTTPError(422, f"Missing argument: {name}")
                        elif name in request.path_params:
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
                            schema = Annotated[annotation, *extras] if extras else annotation
                            value = TypeAdapter(schema).validate_python(raw)
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
                    output = get_type_hints(route.handler, include_extras=True).get("return", Any)
                    value = TypeAdapter(output).validate_python(value)
                    return Response.json(value)
                except HTTPError as error:
                    return Response.json({"detail": error.detail}, error.status)

            call = endpoint
            if invocation is not None:
                async def function_endpoint(context):
                    nonlocal state
                    route = matched
                    if route.state_model:
                        value = (route.state_model.model_validate_json(state_json)
                                 if state_json is not None else route.state_model())
                        state = State(value)
                    return await resolve(route.handler)
                call = function_endpoint
            for middleware in reversed(self.middlewares):
                previous = call
                async def wrapped(req, middleware=middleware, previous=previous):
                    return await middleware(req, previous)
                call = wrapped
            try:
                response = await call(invocation if invocation is not None else request)
                if invocation is not None:
                    output = get_type_hints(matched.handler, include_extras=True).get("return", Any)
                    response = Response.json({"result": TypeAdapter(output).validate_python(response)})
            except HTTPError as error:
                if invocation is not None:
                    response = Response.json({"error": {"code": "validation_error", "message": error.detail}}, error.status)
                else:
                    response = Response.json({"detail": error.detail}, error.status)
            except Error as error:
                response = Response.json({"error": {"code": error.code, "message": str(error)}}, 400)
            if not isinstance(response, Response):
                raise TypeError("Middleware must return Response")
        # Cleanup runs before the state is offered for commit. A cleanup failure aborts it.
        if state is not None and response.status < 400:
            # Revalidate mutations, including nested values, before persisting.
            model = matched.state_model
            committed = model.model_validate_json(_JSON.dump_json(state.value)).model_dump_json()
        return response, committed

    async def handle_wire(self, payload: str, state_json: str | None = None) -> str:
        data = json.loads(payload)
        request = Request(data["method"], data["path"], data.get("query", ""),
                          data.get("headers", []), base64.b64decode(data.get("body", "")))
        response, state = await self.dispatch(request, state_json)
        return json.dumps(dict(status=response.status, headers=response.headers,
                               body=base64.b64encode(response.body).decode(), state=state))
