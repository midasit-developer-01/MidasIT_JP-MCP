"""Hook role: turn raw schema/validation errors into human-readable text.

These helpers translate API codes into the descriptions carried by the schema,
so an error names *what a field means* ("Spring Stiffness ...") rather than only
its code ("SDR").
"""

from __future__ import annotations

import re
from typing import Any


def endpoint_label(item: str, schema: dict) -> str:
    """Human name for an endpoint, e.g. 'Point Spring (NSPR)'.

    Uses the first sentence of the schema's top-level ``description`` and keeps
    the raw code in parentheses so both the meaning and the API code are clear.
    """
    desc = schema.get("description") if isinstance(schema, dict) else None
    if isinstance(desc, str) and desc.strip():
        first = desc.split(". ", 1)[0].strip().rstrip(".")
        return first if item.upper() in first.upper() else f"{first} ({item})"
    return item


def collect_field_descriptions(node: Any, out: dict[str, str] | None = None) -> dict[str, str]:
    """Flatten every ``properties.<field>.description`` in a schema into {field: desc}."""
    if out is None:
        out = {}
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for name, sub in props.items():
                if isinstance(sub, dict) and isinstance(sub.get("description"), str) and name not in out:
                    out[name] = sub["description"]
        for value in node.values():
            collect_field_descriptions(value, out)
    elif isinstance(node, list):
        for value in node:
            collect_field_descriptions(value, out)
    return out


def error_field(err: Any) -> str | None:
    """The field a validation error is about (missing-required name, or last named path step)."""
    if getattr(err, "validator", None) == "required":
        m = re.search(r"'([^']+)' is a required property", err.message)
        if m:
            return m.group(1)
    for part in reversed(list(err.absolute_path)):
        if isinstance(part, str):
            return part
    return None


def format_path(path: Any) -> str:
    """Render a JSON path like ['ITEMS', 0, 'SDR'] as 'ITEMS[0] / SDR'."""
    parts: list[str] = []
    for p in path:
        if isinstance(p, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{p}]"
            else:
                parts.append(f"[{p}]")
        else:
            parts.append(str(p))
    return " / ".join(parts) if parts else "(item root)"


def _json_type(x: Any) -> str:
    """The JSON type name of a Python value (bool before int)."""
    if isinstance(x, bool):
        return "boolean"
    if isinstance(x, int):
        return "integer"
    if isinstance(x, float):
        return "number"
    if isinstance(x, str):
        return "string"
    if isinstance(x, list):
        return "array"
    if isinstance(x, dict):
        return "object"
    if x is None:
        return "null"
    return type(x).__name__


def _quote(x: Any) -> str:
    """Render a scalar for messages: strings quoted, everything else plain."""
    return f"'{x}'" if isinstance(x, str) else str(x)


def _article(word: str) -> str:
    """'a' or 'an' for the given word (by leading letter)."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def describe_problem(err: Any, field_code: str | None = None) -> str:
    """Turn a jsonschema ValidationError into a natural English sentence per validator.

    Falls back to the library's own ``err.message`` for any validator not handled
    explicitly, so nothing ever loses information.
    """
    kind = getattr(err, "validator", None)
    name = field_code or "value"
    constraint = getattr(err, "validator_value", None)
    got = getattr(err, "instance", None)
    sub = getattr(err, "schema", None) or {}

    if kind == "required":
        return f"'{name}' is required here but was not provided."
    if kind == "enum":
        allowed = ", ".join(_quote(v) for v in constraint) if isinstance(constraint, list) else _quote(constraint)
        return f"'{name}' must be one of: {allowed} — got {_quote(got)}."
    if kind == "const":
        return f"'{name}' must be {_quote(constraint)} — got {_quote(got)}."
    if kind == "type":
        want = " or ".join(constraint) if isinstance(constraint, list) else str(constraint)
        got_type = _json_type(got)
        return f"'{name}' must be {_article(want)} {want}, but got {_article(got_type)} {got_type}."
    if kind == "minItems":
        count = len(got) if isinstance(got, list) else "fewer"
        if sub.get("maxItems") == constraint:
            return f"'{name}' must have exactly {constraint} items — got {count}."
        return f"'{name}' must have at least {constraint} items — got {count}."
    if kind == "maxItems":
        count = len(got) if isinstance(got, list) else "more"
        if sub.get("minItems") == constraint:
            return f"'{name}' must have exactly {constraint} items — got {count}."
        return f"'{name}' must have at most {constraint} items — got {count}."
    if kind == "minimum":
        return f"'{name}' must be greater than or equal to {constraint} — got {_quote(got)}."
    if kind == "maximum":
        return f"'{name}' must be less than or equal to {constraint} — got {_quote(got)}."
    if kind == "minLength":
        return f"'{name}' must be at least {constraint} characters long."
    if kind == "maxLength":
        return f"'{name}' must be at most {constraint} characters long."
    if kind == "additionalProperties":
        return f"'{name}' contains a property that is not allowed by the schema."
    return err.message
