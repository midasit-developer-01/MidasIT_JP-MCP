"""Endpoint catalog lookup over the bundled per-endpoint schema files.

Schemas live one-file-per-endpoint under ``data/schemas/<group>/<NAME>.json``
(e.g. ``data/schemas/db/NODE.json``). Each file carries ``_group``/``_name``
plus the endpoint's ``uri``/``methods``/``schema``/``example``/``tables``.

Lets the LLM discover the exact schema/example/tables for any endpoint
(including the undocumented `db/IEHP`) at call time instead of hardcoding 256
tools. Splitting the old monolithic json into per-endpoint files is a pure
data-layout change: the tool surface is unchanged.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "data" / "schemas"


@lru_cache(maxsize=1)
def _load() -> list[tuple[str, str, dict[str, Any]]]:
    """Load every per-endpoint file once into (group, name, entry) tuples.

    ``group`` and ``name`` come from the file's ``_group``/``_name`` fields
    (falling back to the top-level folder / file name), and are stripped from
    ``entry`` so the returned entry matches the original monolith's per-endpoint
    shape. The tree is scanned RECURSIVELY so deep code-standard paths
    (e.g. ``design/RC/KDS-41-20-2022/DCRM.json`` or ``requestinfo/POST/TABLE.json``)
    are included. Files directly under ``schemas/`` (e.g. ``_meta.json``) are
    ignored — only ``<group>/.../<NAME>.json`` is scanned.
    """
    out: list[tuple[str, str, dict[str, Any]]] = []
    for path in sorted(_SCHEMA_DIR.glob("**/*.json")):
        rel = path.relative_to(_SCHEMA_DIR)
        if len(rel.parts) < 2:  # skip files sitting directly under schemas/
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        group = data.pop("_group", rel.parts[0])
        name = data.pop("_name", path.stem)
        out.append((group, name, data))
    return out


def _iter_entries():
    """Yield (group, name, entry) for every endpoint in the catalog."""
    yield from _load()


def search(query: str, limit: int = 8, group: str | None = None) -> list[dict[str, Any]]:
    """Fuzzy-search endpoints by name / uri / note / field descriptions.

    Returns compact hits: {group, name, uri, methods, note?}.
    Pass `group` (e.g. "db", "design", "ope", "rating", "temp", "doc", "post",
    "view", "requestinfo", "config") to restrict results to that group — useful
    when a name repeats across groups/code-standards (e.g. MATD, MEMB, TABLE).
    Use `describe()` to pull the full schema + example for a chosen endpoint.
    """
    q = query.strip().lower()
    grp = group.strip().lower() if group else None
    tokens = [t for t in q.replace("/", " ").replace("-", " ").split() if t]
    hits: list[tuple[int, dict[str, Any]]] = []
    for group, name, entry in _iter_entries():
        if grp is not None and group.lower() != grp:
            continue
        uri = str(entry.get("uri", ""))
        note = str(entry.get("note", ""))
        blob = f"{group} {name} {uri} {note}".lower()
        # also scan schema field descriptions for keyword hits
        schema_blob = json.dumps(entry.get("schema", ""))[:4000].lower()
        # base 1 only when listing a group with no query, so `group=` + empty
        # query enumerates that group without polluting real keyword searches
        score = 1 if (grp is not None and not q) else 0
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


def _result(g: str, key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """describe() hit + its body convention, read from the example's top-level key
    ("Assign" vs "Argument" — a per-endpoint property, since temp mixes both)."""
    ex = entry.get("example")
    convention = (
        "assign" if isinstance(ex, dict) and "Assign" in ex
        else "argument" if isinstance(ex, dict) and "Argument" in ex
        else "unknown"
    )
    return {"group": g, "name": key, "convention": convention, **entry}


def describe(name: str, group: str | None = None) -> dict[str, Any]:
    """Return the full catalog entry (schema + example + note) for an endpoint.

    `name` may be a bare catalog key (e.g. "NODE", "ANAL", "TABLE") or a full
    `uri` (e.g. "design/RC/KDS-41-20-2022/MATD"), matched case-insensitively.

    Some names repeat: across groups (db/MEMB vs ope/MEMB) and — for design/
    rating — across code standards (MATD lives under design/PSC, design/RC,
    design/SRC and rating/PSC). Pass the full `uri` to pin exactly one, or pass
    `group` to narrow by group; without either, the first match is returned.
    """
    want = name.strip().lower()
    # uri-style input (has a "/") -> exact uri match, unambiguous
    if "/" in want:
        for g, key, entry in _iter_entries():
            if str(entry.get("uri", "")).lower() == want:
                return _result(g, key, entry)
    grp = group.strip().lower() if group else None
    for g, key, entry in _iter_entries():
        if key.lower() == want and (grp is None or g.lower() == grp):
            return _result(g, key, entry)
    return {"error": f"endpoint '{name}' not found in catalog"}
