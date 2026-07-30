"""Guard: every bundled ``db`` example must pass the SAME validation the client applies.

Rationale: ``describe()`` hands each endpoint's ``example`` to the LLM, which imitates
it directly. A guide-derived example that contradicts the (C++-truth) schema would
teach the LLM a wrong payload AND be rejected by the runtime hook that ships with it.

This guard reuses :func:`validate_assign` (the exact runtime path), so "example
passes this guard" is equivalent to "the client hook will not reject that example".

Run:  ``python -m midas_mcp.hooks.check_examples``  (exit 0 = all pass, 1 = failures).
Requires ``jsonschema`` to be installed (unlike the runtime hook, the guard must not
pass vacuously, so it imports it eagerly).
"""

from __future__ import annotations

import sys

import jsonschema  # noqa: F401  (eager import: the guard must fail loudly if absent)

from .. import catalog
from .validation import VALIDATED_GROUPS, validate_argument, validate_assign


def check_all() -> list[dict]:
    """Return one entry per validated endpoint whose bundled example fails its schema.

    Covers every group in ``VALIDATED_GROUPS`` via the exact runtime path per
    convention (Assign -> validate_assign, Argument -> validate_argument), so the
    guard tracks precisely what the runtime hook will validate.
    """
    problems: list[dict] = []
    for group, name, entry in catalog._iter_entries():
        if group not in VALIDATED_GROUPS:
            continue
        example = entry.get("example")
        if not isinstance(example, dict):
            continue
        # Resolve by full URI so nested design/rating entries hit their exact
        # code-standard schema (leaf names repeat across standards).
        uri = entry.get("uri") or f"{group}/{name}"
        if isinstance(example.get("Assign"), dict) and example["Assign"]:
            err = validate_assign(group, uri, example["Assign"])
        elif "Argument" in example:
            err = validate_argument(group, uri, example)
        else:
            continue
        if err is not None:
            problems.append({"name": uri, "error": err})
    return problems


def main() -> int:
    # Problem messages carry non-ASCII (e.g. em dashes); keep the guard usable on
    # legacy consoles (Windows cp949/cp1252) instead of dying with UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    problems = check_all()
    if not problems:
        print("OK: all bundled db examples pass their schema.")
        return 0
    print(f"{len(problems)} db endpoint(s) ship examples that FAIL their own schema:\n")
    for p in problems:
        details = p["error"].get("details") or []
        first = details[0] if details else {}
        loc = first.get("location", "")
        msg = first.get("problem") or p["error"].get("error", "")
        print(f"  {p['name']:8} {loc}  {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
