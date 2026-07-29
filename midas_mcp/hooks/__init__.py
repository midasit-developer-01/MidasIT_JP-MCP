"""Pre-request hooks for the MIDAS client.

Each module owns one role:
  - ``schema_source``  : locate the bundled JSON Schema for a DB endpoint
  - ``messages``       : turn raw schema/validation errors into human-readable text
  - ``validation``     : orchestrate schema validation of an outgoing DB body

The client calls :func:`validate_db_assign` before every POST/PUT to /db/{item},
and :func:`validate_assign` / :func:`validate_argument` for the other
convention-carrying groups (see ``VALIDATED_GROUPS``) inside ``command()``.
"""

from __future__ import annotations

from .validation import (
    VALIDATED_GROUPS,
    validate_argument,
    validate_assign,
    validate_db_assign,
)

__all__ = [
    "VALIDATED_GROUPS",
    "validate_argument",
    "validate_assign",
    "validate_db_assign",
]
