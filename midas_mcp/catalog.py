"""Endpoint catalog lookup over the bundled per-endpoint schema files.

Schemas live one-file-per-endpoint under ``data/schemas/<group>/<NAME>.json``
(e.g. ``data/schemas/db/NODE.json``). Each file carries ``_group``/``_name``
plus the endpoint's ``uri``/``methods``/``schema``/``example``/``tables``.

Lets the LLM discover the exact schema/example/tables for any endpoint
(including the undocumented `db/IEHP`) at call time instead of hardcoding 256
tools. Splitting the old monolithic json into per-endpoint files is a pure
data-layout change: the tool surface is unchanged.

This module owns loading and ``describe``; ranking for ``search`` lives in
:mod:`midas_mcp.search_index`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import features, search_index

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


DESC_MAX = 140

# A sentence break is only worth cutting at if it lands late enough to have said
# something. Catalog descriptions open with a title sentence — "Section (SECT)." —
# that is under 40 characters 71% of the time, and stopping there would strip the
# very detail the caller needs to tell two endpoints apart.
_SENTENCE_MIN = 0.6


def _short_desc(summary: str, cap: int = DESC_MAX) -> str:
    """One line describing the endpoint, for a search hit.

    Cuts at a sentence boundary when one falls late in the budget, otherwise at a
    word boundary with an ellipsis, so a hit never ends mid-word.
    """
    text = " ".join(summary.split())
    if len(text) <= cap:
        return text

    window = text[:cap]
    stop = window.rfind(". ")
    if stop >= cap * _SENTENCE_MIN:
        return window[: stop + 1]

    space = window.rfind(" ")
    return (window[:space] if space > 0 else window[: cap - 1]).rstrip(" ,;:") + "…"


def search(query: str, limit: int = 10, group: str | None = None) -> list[dict[str, Any]]:
    """Fuzzy-search endpoints by name / uri / description (BM25, see search_index).

    Returns compact hits: {group, name, uri, methods, desc, note?}. ``desc`` is
    what makes the result usable — endpoint names are 4-letter abbreviations, so
    a list of bare uris gives the caller nothing to choose between. The ranking
    aims to keep the right endpoint ON the list rather than first; the caller is
    expected to read the descriptions and pick.

    Pass `group` (e.g. "db", "design", "ope", "rating", "temp", "doc", "post",
    "view", "requestinfo", "config") to restrict results to that group — useful
    when a name repeats across groups/code-standards (e.g. MATD, MEMB, TABLE).
    With a group set, an empty query simply lists that group.
    Use `describe()` to pull the full schema + example for a chosen endpoint.
    """
    hits: list[dict[str, Any]] = []
    for doc in search_index.rank(query, limit=limit, group=group):
        note = str(doc.entry.get("note", ""))
        hits.append({
            "group": doc.group,
            "name": doc.key,
            "uri": doc.uri,
            "methods": doc.entry.get("methods"),
            "desc": _short_desc(doc.summary),
            **({"note": note} if note else {}),
            # Only ever present, never False: absence is the signal, as with
            # `note`. Tells the model a midas_guide call will actually return
            # something, so it does not have to try one to find out.
            **({"guide": True} if features.has_guide(doc.uri) else {}),
        })
    return hits


def _result(g: str, key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """describe() hit + its body convention, read from the example's top-level key
    ("Assign" vs "Argument" — a per-endpoint property, since temp mixes both)."""
    ex = entry.get("example")
    convention = (
        "assign" if isinstance(ex, dict) and "Assign" in ex
        else "argument" if isinstance(ex, dict) and "Argument" in ex
        else "unknown"
    )
    # Ahead of **entry so a stray `guide` key in a schema file cannot shadow
    # the computed one.
    return {
        "group": g,
        "name": key,
        "convention": convention,
        **({"guide": True} if features.has_guide(str(entry.get("uri", ""))) else {}),
        **entry,
    }


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
