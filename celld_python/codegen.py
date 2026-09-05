"""Generate readable Python clients from the worker's JSON contract.

Only the supported JSON-schema subset is translated; structural constructs
that would silently weaken types are rejected. Server validators remain final.
"""
from __future__ import annotations

import json
import keyword
import re
from pathlib import Path

from pydantic import BaseModel

RESERVED = {"Client", "AsyncClient", "BaseModel", "ConfigDict", "Field", "TypeAdapter", "Any", "Literal",
            "Annotated", "RootModel", "Self", "ClientInfo", "RetryPolicy", "datetime", "date", "time", "UUID"}


def identifier(value):
    name = re.sub(r"\W", "_", value, flags=re.ASCII)
    if not name or not name[0].isalpha():
        name = "field_" + name
    if keyword.iskeyword(name) or name in dir(BaseModel) or name in {"self", "_client"}:
        name += "_"
    return name


class Generator:
    def __init__(self):
        self.models = []
        self.names = set(RESERVED)
        self.known = {}

    def model(self, schema, suggested, refs):
        schema = {k: v for k, v in schema.items() if k != "$defs"}
        reachable = {}

        def visit(value):
            if isinstance(value, dict):
                ref = value.get("$ref", "")
                if ref.startswith("#/$defs/") and ref[8:] not in reachable:
                    reachable[ref[8:]] = refs[ref[8:]]
                    visit(refs[ref[8:]])
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(schema)
        key = json.dumps([schema, reachable], sort_keys=True)
        if key in self.known:
            return self.known[key]
        name = identifier(schema.get("title", suggested))
        base = name
        suffix = 2
        while name in self.names:
            name = base + str(suffix)
            suffix += 1
        self.names.add(name)
        self.known[key] = name
        self.models.append((name, schema, refs))
        return name

    def annotation(self, schema, refs, suggested="Value"):
        if schema is True:
            return "Any"
        if schema is False:
            raise ValueError("A never-valid schema cannot be exposed in a client")
        if "$ref" in schema:
            ref = schema["$ref"]
            if not ref.startswith("#/$defs/") or ref[8:] not in refs:
                raise ValueError(f"Unsupported schema reference: {ref}")
            value = refs[ref[8:]]
            return self.model(value, ref[8:], refs)
        if any(key in schema for key in ("allOf", "not", "if", "then", "else", "patternProperties", "dependentSchemas")):
            raise ValueError(f"Unsupported schema construct in {suggested}")
        if "const" in schema:
            return f"Literal[{schema['const']!r}]"
        if "enum" in schema:
            return "Literal[" + ", ".join(repr(x) for x in schema["enum"]) + "]"
        choices = schema.get("anyOf", schema.get("oneOf"))
        if choices:
            types = list(dict.fromkeys(self.annotation(x, refs, suggested) for x in choices))
            result = " | ".join(types)
            if schema.get("discriminator"):
                result = f"Annotated[{result}, Field(discriminator={schema['discriminator']['propertyName']!r})]"
        else:
            kind = schema.get("type")
            if isinstance(kind, list):
                result = " | ".join(self.annotation({**schema, "type": t}, refs, suggested) for t in kind)
            elif kind == "object" or "properties" in schema:
                if "properties" in schema:
                    return self.model(schema, suggested, refs)
                additional = schema.get("additionalProperties", {})
                result = f"dict[str, {self.annotation(additional, refs, suggested + 'Value')}]" if isinstance(additional, dict) else "dict[str, Any]"
            elif kind == "array":
                if "prefixItems" in schema:
                    result = "tuple[" + ", ".join(self.annotation(x, refs, suggested) for x in schema["prefixItems"]) + "]"
                else:
                    result = f"list[{self.annotation(schema.get('items', {}), refs, suggested)}]"
            elif kind == "string":
                result = {"date-time": "datetime", "date": "date", "time": "time", "uuid": "UUID"}.get(schema.get("format"), "str")
            elif kind in {"integer", "number", "boolean", "null", None}:
                result = {"integer": "int", "number": "float", "boolean": "bool", "null": "None", None: "Any"}[kind]
            else:
                raise ValueError(f"Unsupported schema type: {kind}")
        constraints = []
        for source, target in {"minimum": "ge", "maximum": "le", "exclusiveMinimum": "gt", "exclusiveMaximum": "lt",
                               "multipleOf": "multiple_of", "minLength": "min_length", "maxLength": "max_length",
                               "minItems": "min_length", "maxItems": "max_length", "pattern": "pattern"}.items():
            if source in schema:
                constraints.append(f"{target}={schema[source]!r}")
        if constraints:
            result = f"Annotated[{result}, Field({', '.join(constraints)})]"
        return result

    def render_models(self):
        blocks = []
        index = 0
        while index < len(self.models):
            name, schema, refs = self.models[index]
            index += 1
            if "properties" not in schema:
                blocks.append(f"class {name}(RootModel[{self.annotation(schema, refs, name)!r}]):\n    pass\n")
                continue
            lines = [f"class {name}(BaseModel):", "    model_config = ConfigDict(populate_by_name=True, extra=" + repr("forbid" if schema.get("additionalProperties") is False else "ignore") + ")"]
            required = schema.get("required", [])
            used = set()
            for wire, value in schema["properties"].items():
                field = identifier(wire)
                while field in used:
                    field += "_"
                used.add(field)
                annotation = self.annotation(value, refs, name + field.title())
                args = [f"alias={wire!r}"] if field != wire else []
                if wire not in required:
                    if "default" in value:
                        args.insert(0, f"default={value['default']!r}")
                    elif value.get("type") in {"array", "object"}:
                        args.insert(0, "default_factory=" + ("list" if value["type"] == "array" else "dict"))
                    else:
                        # JSON Schema describes default_factory fields as optional
                        # without encoding the factory. Omit them on the wire.
                        annotation += " | None"
                        args.insert(0, "default=None")
                if "description" in value:
                    args.append(f"description={value['description']!r}")
                lines.append(f"    {field}: {annotation}" + (f" = Field({', '.join(args)})" if args else ""))
            blocks.append("\n".join(lines) + "\n")
        # Forward references support recursive Pydantic models and definition order.
        blocks.extend(f"{name}.model_rebuild()" for name, _, _ in self.models)
        return "\n\n".join(blocks)


def generate(schema: dict, output: Path):
    if schema.get("version") != 1:
        raise ValueError("Unsupported worker schema version")
    generator = Generator()
    methods = []
    contexts = []
    used_methods = {"call", "with_context", "with_call_id", "endpoint", "timeout", "retries"}
    for name, spec in schema["functions"].items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid exported function name: {name}")
        method = name
        while method in used_methods:
            method += "_"
        used_methods.add(method)
        arguments = spec["arguments"]
        refs = arguments.get("$defs", {})
        input_model = generator.model(arguments, name.title() + "Arguments", refs)
        returns = spec["returns"]
        output_type = generator.annotation(returns, returns.get("$defs", {}), name.title() + "Result")
        context = spec.get("context")
        if context:
            contexts.append(generator.annotation(context, context.get("$defs", {}), "Caller"))
        params, pairs = [], []
        used = set()
        for wire, value in arguments.get("properties", {}).items():
            param = identifier(wire)
            while param in used:
                param += "_"
            used.add(param)
            annotation = generator.annotation(value, refs, name.title() + param.title())
            default = ""
            if wire not in arguments.get("required", []):
                value_default = value.get("default", _MISSING)
                default = " = " + (repr(value_default) if value_default is not _MISSING and isinstance(value_default, (str, int, float, bool, type(None))) else "_UNSET")
            params.append(f"{param}: {annotation}{default}")
            pairs.append(f"{wire!r}: {param}")
        signature = "self" + (", *, " + ", ".join(params) if params else "")
        methods.append((method, name, signature, input_model, output_type, ", ".join(pairs), spec.get("description", "")))
    model_source = generator.render_models()
    context_type = " | ".join(dict.fromkeys(contexts)) or "dict[str, Any]"
    classes = []
    for asynchronous in (False, True):
        class_name = "AsyncClient" if asynchronous else "Client"
        base = "_AsyncClient" if asynchronous else "_Client"
        lines = [f"class {class_name}({base}):",
                 f"    def __init__(self, endpoint: str, *, context: {context_type} | None = None, token: str | None = None,",
                 "                 client_info: ClientInfo | None = None, timeout: float = 60, retries: RetryPolicy | None = None):",
                 "        super().__init__(endpoint, context=context, token=token, client_info=client_info, timeout=timeout, retries=retries)",
                 "", f"    def with_context(self, context: {context_type}) -> Self:",
                 "        return super().with_context(context)"]
        for method, remote, signature, inputs, result, pairs, description in methods:
            lines.extend(["", f"    {'async ' if asynchronous else ''}def {method}({signature}) -> {result}:",
                          f"        {description!r}",
                          f"        arguments = {inputs}.model_validate({{k: v for k, v in {{{pairs}}}.items() if v is not _UNSET}})",
                          f"        result = {'await ' if asynchronous else ''}self.call({remote!r}, **arguments.model_dump(mode='json', by_alias=True, exclude_unset=True))",
                          f"        return TypeAdapter({result}).validate_python(result)"])
        classes.append("\n".join(lines))
    source = '''# Generated by celld-py. Regenerate after changing the worker contract.
from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel, TypeAdapter
from celld_python import ClientInfo, RetryPolicy
from celld_python.client import Client as _Client, AsyncClient as _AsyncClient

_UNSET: Any = object()  # Omitted arguments keep their server defaults.

''' + model_source + "\n\n\n" + "\n\n\n".join(classes) + "\n"
    compile(source, str(output), "exec")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source)
    return output


_MISSING = object()
