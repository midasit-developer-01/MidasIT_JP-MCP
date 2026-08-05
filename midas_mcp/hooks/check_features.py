"""Guard: every bundled GUI guide points at a real endpoint, and the index agrees with the tree.

Rationale: ``midas_lookup`` puts a ``guide`` breadcrumb on a hit based on
``data/features/_index.json``, and ``midas_guide`` then reads the tree. If those
two disagree, the model is told a guide exists and gets a miss record back —
the worst possible outcome, because it looks like a flaw in its own reasoning
rather than a data fault. This guard reuses :func:`features._index` and
``catalog._iter_entries()``, the exact runtime paths, so passing here means the
runtime cannot hit that contradiction.

Coverage is NOT gated. Most endpoints have no guide because the public manual
has no feature article for them (design, rating and temp have none at all), and
37 further articles are restricted upstream. That is the documented expected
state and is reported as a line of output, not a failure.

Run:  ``python -m midas_mcp.hooks.check_features``  (exit 0 = pass, 1 = broken).
"""

from __future__ import annotations

import json
import sys

from .. import catalog, features

# A guide with none of these says nothing useful and is a generation fault.
REQUIRED = ("feature_name", "menu_path", "url")


def check_all() -> tuple[list[str], dict[str, int]]:
    """Return ``(problems, stats)``. An empty problem list means the tree is sound."""
    problems: list[str] = []
    root = features._FEATURE_DIR

    if not root.exists():
        return [f"missing guide tree: {root}"], {}

    known = {str(e.get("uri", "")): True for _, _, e in catalog._iter_entries()}
    index = features._index()

    files: dict[str, dict] = {}
    for path in sorted(root.rglob("*.json")):
        rel = path.relative_to(root).as_posix()
        if rel == "_index.json":
            continue
        if "/" not in rel:
            problems.append(f"{rel}: sits at the tree root; a guide must live under its group directory")
            continue
        uri = rel[: -len(".json")]
        # Case-sensitive on purpose: a tree authored on Windows must not break
        # when the container mounts it on a case-sensitive filesystem.
        if uri not in known:
            problems.append(f"{rel}: '{uri}' is not an endpoint in data/schemas")
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{rel}: not valid JSON ({exc})")
            continue
        if not isinstance(record, dict):
            problems.append(f"{rel}: top level is {type(record).__name__}, expected an object")
            continue
        files[uri] = record

        if record.get("uri") != uri:
            problems.append(f"{rel}: 'uri' field is {record.get('uri')!r}, expected {uri!r}")
        for field in REQUIRED:
            if not str(record.get(field) or "").strip():
                problems.append(f"{rel}: '{field}' is missing or empty")

    # The index and the tree are two writers of the same fact; drift between
    # them is exactly what produces a lying breadcrumb.
    indexed = {entry["uri"] for entry in index.values()}
    for uri in sorted(indexed - set(files)):
        problems.append(f"_index.json lists '{uri}' but data/features/{uri}.json does not exist")
    for uri in sorted(set(files) - indexed):
        problems.append(f"data/features/{uri}.json exists but _index.json does not list it")
    for uri in sorted(indexed & set(files)):
        entry = index[uri.lower()]
        for field in ("feature_name", "menu_path"):
            if str(entry.get(field) or "") != str(files[uri].get(field) or ""):
                problems.append(f"_index.json '{uri}' has a stale {field}; regenerate with extract_features.py")

    stats = {
        "guides": len(files),
        "endpoints": len(known),
        "with_usage": sum("usage" in r for r in files.values()),
        "with_note": sum("note" in r for r in files.values()),
    }
    return problems, stats


def main() -> int:
    # Paths and manual titles carry non-ASCII; keep the guard usable on legacy
    # consoles (Windows cp949) instead of dying with UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    problems, stats = check_all()
    if stats:
        guides, endpoints = stats["guides"], stats["endpoints"]
        print(f"coverage: {guides}/{endpoints} endpoints ({guides * 100 // endpoints}%) have a GUI guide "
              f"- {stats['with_usage']} with usage, {stats['with_note']} with note")
        print("  the rest have no feature article in the public manual; that is expected, not a failure")

    if not problems:
        print("OK: every guide maps to a real endpoint and _index.json matches the tree.")
        return 0
    print(f"\n{len(problems)} problem(s) in the guide tree:\n")
    for line in problems:
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
