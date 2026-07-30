"""Pre-request hooks for the MIDAS client.

Each module owns one role:
  - ``schema_source``  : locate the bundled JSON Schema for a DB endpoint
  - ``messages``       : turn raw schema/validation errors into human-readable text
  - ``validation``     : orchestrate schema validation of an outgoing DB body

The client calls :func:`validate_assign` before every Assign-body POST/PUT (db
and the other convention-carrying groups; see ``VALIDATED_GROUPS``), and
:func:`validate_argument` for Argument-body endpoints inside ``command()``.
"""

from __future__ import annotations

from .validation import (
    VALIDATED_GROUPS,
    validate_argument,
    validate_assign,
)

__all__ = [
    "VALIDATED_GROUPS",
    "validate_argument",
    "validate_assign",
]
