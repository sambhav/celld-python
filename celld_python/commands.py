"""Human-facing function calls and discovery; no transport details in arguments."""
import json
import re


def _non_json_constant(text):
    raise ValueError(text)


def arguments(items):
    values = {}
    for item in items:
        name, separator, value = item.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Expected name=value, got {item!r}")
        if name in values:
            raise ValueError(f"Argument supplied twice: {name}")
        try:
            values[name] = json.loads(value, parse_constant=_non_json_constant)
        except ValueError:
            values[name] = value
    return values


def type_name(schema):
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in schema:
        return " | ".join(type_name(item) for item in schema["anyOf"])
    if schema.get("type") == "array":
        return "list[" + type_name(schema.get("items", {})) + "]"
    return {"string":"str", "integer":"int", "number":"float", "boolean":"bool", "object":"dict", "null":"None"}.get(schema.get("type"), "Any")


def signatures(schema):
    for name, function in schema["functions"].items():
        parameters = []
        arguments = function["arguments"]
        for key, value in arguments.get("properties", {}).items():
            parameter = f"{key}: {type_name(value)}"
            if "default" in value:
                parameter += " = " + repr(value["default"])
            elif key not in arguments.get("required", []):
                parameter += " = ..."
            parameters.append(parameter)
        suffix = "  [state]" if function.get("stateful") else ""
        yield f"{name}({', '.join(parameters)}) -> {type_name(function['returns'])}{suffix}"
