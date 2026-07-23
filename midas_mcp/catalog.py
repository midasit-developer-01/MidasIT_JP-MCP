"""Endpoint catalog lookup over the bundled midas-api-examples.json.

Lets the LLM discover the exact schema/example for any endpoint (including the
undocumented `db/IEHP`) at call time instead of hardcoding 256 tools.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent.parent / "data" / "midas-api-examples.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    with _DATA.open(encoding="utf-8") as fh:
        return json.load(fh)


def _iter_entries():
    """Yield (group, name, entry) for every endpoint in the catalog."""
    data = _load()
    for group, items in data.items():
        if group.startswith("_") or not isinstance(items, dict):
            continue
        for name, entry in items.items():
            if isinstance(entry, dict):
                yield group, name, entry


def search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Fuzzy-search endpoints by name / uri / note / field descriptions.

    Returns compact hits: {group, name, uri, methods, note?}.
    Use `describe()` to pull the full schema + example for a chosen endpoint.
    """
    q = query.strip().lower()
    tokens = [t for t in q.replace("/", " ").replace("-", " ").split() if t]
    hits: list[tuple[int, dict[str, Any]]] = []
    for group, name, entry in _iter_entries():
        uri = str(entry.get("uri", ""))
        note = str(entry.get("note", ""))
        blob = f"{group} {name} {uri} {note}".lower()
        # also scan schema field descriptions for keyword hits
        schema_blob = json.dumps(entry.get("schema", ""))[:4000].lower()
        score = 0
        # whole-query exact/substring boosts
        if q == name.lower():
            score += 100
        if q and q in name.lower():
            score += 40
        if q and q in uri.lower():
            score += 30
        # per-token scoring (handles multi-word queries like "inelastic hinge")
        for tok in tokens:
            if tok in name.lower():
                score += 20
            if tok in note.lower():
                score += 8
            if tok in blob:
                score += 5
            if tok in schema_blob:
                score += 3
        if score:
            hits.append((score, {
                "group": group,
                "name": name,
                "uri": uri,
                "methods": entry.get("methods"),
                **({"note": note} if note else {}),
            }))
    hits.sort(key=lambda x: x[0], reverse=True)
    return [h for _, h in hits[:limit]]


def describe(name: str) -> dict[str, Any]:
    """Return the full catalog entry (schema + example + note) for an endpoint name.

    `name` matches the catalog key (e.g. "NODE", "IEHP", "ANAL", "TABLE"),
    case-insensitively, across all groups.
    """
    want = name.strip().lower()
    for group, key, entry in _iter_entries():
        if key.lower() == want:
            return {"group": group, "name": key, **entry}
    return {"error": f"endpoint '{name}' not found in catalog"}
