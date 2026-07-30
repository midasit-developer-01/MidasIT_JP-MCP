"""Hook role: pre-request validation of a request body against its schema.

Fails open: if ``jsonschema`` is missing or no schema is bundled for the item,
returns None so the request is sent unchanged. Only bodies that a bundled schema
actively rejects are blocked.

Two body conventions, validated at different levels (that's how the bundled
schemas are shaped, per convention):
  - ``Assign`` (db/temp/design/rating db-style): ``schema[NAME]`` describes ONE
    per-id value object; :func:`validate_assign` checks each value in the map.
  - ``Argument`` (doc/ope/view/post/requestinfo/config, and design/rating
    analysis-style): ``schema[NAME]`` describes the WHOLE ``{"Argument": ...}``
    body; :func:`validate_argument` checks the full body.

``VALIDATED_GROUPS`` is the single source of truth for which groups are checked —
a group is listed only once its bundled schemas (generated from the C++ DTOs) pass
the example guard (check_examples), so validation never rejects a valid body over
a bad schema.
"""

from __future__ import annotations

from typing import Any

from .messages import (
    collect_field_descriptions,
    describe_problem,
    endpoint_label,
    error_field,
    format_path,
)
from .schema_source import load_item_schema

# Every group that carries an Assign/Argument schema and passes the example guard.
# db is validated in the client's db_create/db_update; the other nine are validated
# in client.command(). (config/requestinfo are read-mostly, so they rarely fire.)
VALIDATED_GROUPS = frozenset({
    "db", "temp", "design", "rating",
    "doc", "ope", "view", "post", "requestinfo", "config",
})


def _validate(group: str, item: str, cases_from) -> dict | None:
    """Shared core: load the schema, then validate a set of (prefix, value) cases.

    ``cases_from(schema)`` returns ``(cases, early_error)`` — ``cases`` is a list of
    ``(location_prefix, value_to_validate)``; ``early_error`` short-circuits with a
    caller-shaped error (or None). Returns an error dict, or None to proceed
    (also None when validation cannot run: jsonschema absent / no schema bundled).
    """
    try:
        import jsonschema
        from jsonschema.exceptions import best_match
    except ImportError:
        return None
    schema = load_item_schema(item, group=group)
    if schema is None:
        return None
    cases, early = cases_from(schema)
    if early is not None:
        return early

    label = endpoint_label(item, schema)
    field_desc = collect_field_descriptions(schema)
    validator = jsonschema.Draft7Validator(schema)
    problems: list[dict] = []
    for prefix, value in cases:
        # best_match descends into if/then/anyOf to surface the most relevant error
        err = best_match(validator.iter_errors(value))
        if err is None:
            continue
        field = error_field(err)
        desc = field_desc.get(field) if field else None
        problems.append({
            "location": f"{prefix}{format_path(err.absolute_path)}",
            "field": desc or field or "(value)",  # human meaning, not the raw code
            "field_code": field,
            "problem": describe_problem(err, field),  # natural sentence per validator
        })
    if problems:
        return {
            "error": f"'{label}' request body is invalid and was not sent. "
                     f"Fix the items below and retry (pass validate=False to bypass).",
            "endpoint": label,
            "details": problems[:20],
        }
    return None


def validate_assign(group: str, item: str, assign: Any) -> dict | None:
    """Validate an ``Assign`` map for /{group}/{item}: each value vs the item schema."""
    def cases_from(schema):
        if not isinstance(assign, dict):
            return [], {"error": f"Invalid body for /{group}/{item}: 'Assign' must be an object keyed by id."}
        return [(f"id '{k}' -> ", v) for k, v in assign.items()], None
    return _validate(group, item, cases_from)


def validate_argument(group: str, item: str, body: Any) -> dict | None:
    """Validate an ``Argument`` body for /{group}/{item}.

    ``body`` is the full request body ({"Argument": ...}, or {} when omitted); the
    Argument-group schema describes that whole body, so it is validated as-is.
    """
    def cases_from(schema):
        full = body if isinstance(body, dict) else {"Argument": body}
        return [("", full)], None
    return _validate(group, item, cases_from)
