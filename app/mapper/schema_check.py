"""Validate fixture documents against schema/fixture.v1.json.

The checker implements the slice of JSON Schema that the fixture file uses,
so the server does not take a new dependency and the Swift package can mirror
the same rules.
"""

from __future__ import annotations

import json
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "fixture.v1.json"


def schema_path() -> Path:
    return _SCHEMA_PATH


def load_schema() -> dict:
    return json.loads(schema_path().read_text(encoding="utf-8"))


class SchemaError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate(instance: object, schema: dict | None = None) -> list[str]:
    document = schema if schema is not None else load_schema()
    defs = document.get("$defs") if isinstance(document.get("$defs"), dict) else {}
    return _check(instance, document, defs, "$")


def require_valid(instance: object, schema: dict | None = None) -> None:
    errors = validate(instance, schema)
    if errors:
        raise SchemaError(errors)


def _check(instance: object, schema: dict, defs: dict, path: str) -> list[str]:
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        target = defs.get(name)
        if not isinstance(target, dict):
            return [f"{path} references unknown {schema['$ref']}"]
        return _check(instance, target, defs, path)

    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path} must be {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']}")

    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(instance, name) for name in names):
            errors.append(f"{path} has the wrong type")
            return errors

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            errors.append(f"{path} is too short")
        if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
            errors.append(f"{path} is too long")

    if _is_number(instance):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path} is below {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path} is above {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path} must be greater than {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path} must be less than {schema['exclusiveMaximum']}")

    if isinstance(instance, dict) and ("properties" in schema or schema.get("type") == "object"):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        for key in required:
            if key not in instance:
                errors.append(f"{path}.{key} is required")
        allow_extra = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                errors.extend(_check(value, properties[key], defs, f"{path}.{key}"))
            elif allow_extra is False:
                errors.append(f"{path}.{key} is not allowed")

    if isinstance(instance, list) and ("items" in schema or schema.get("type") == "array"):
        if "minItems" in schema and len(instance) < int(schema["minItems"]):
            errors.append(f"{path} needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            errors.append(f"{path} has too many items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                errors.extend(_check(value, item_schema, defs, f"{path}[{index}]"))
    return errors


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_ok(value: object, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return _is_number(value)
    return False
