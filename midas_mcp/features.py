"""GUI operation guides for the endpoints the public manual documents.

One file per endpoint under ``data/features/<uri>.json`` — the uri IS the path,
the same layout ``data/schemas`` uses, so there is no slug to invent and none to
invert. ``_index.json`` at the tree root maps uri -> ``{feature_name, menu_path}``
and is what ``catalog`` reads to put a ``guide`` breadcrumb on a search hit, and
what ``search_index`` folds into the index, without opening 155 files to do it.

Coverage is 155 of 572 endpoints and that is a source limit, not a bug. The
public manual has feature articles for db/ope/doc/view/post only; design, rating
and temp have none, and 37 further articles are restricted upstream (see
``scripts/fetch_articles.py``). So a miss is a normal outcome, not an error, and
:func:`guide` answers one with the manual link plus related guided features
rather than an empty response the model will paper over with a guessed menu path.

Read lazily, one file per request: most sessions never ask a "how do I" question
and should not pay for ~1 MB of dialog text they will not use.

This module deliberately does NOT import ``catalog`` — ``catalog`` imports THIS
one, for the breadcrumb. Callers resolve a name to a catalog entry first (that is
``server.py``'s job) and hand the entry in, which also guarantees the guide and
the schema can never disagree about which endpoint a bare name meant.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import search_index

_FEATURE_DIR = Path(__file__).resolve().parent.parent / "data" / "features"
_INDEX_PATH = _FEATURE_DIR / "_index.json"

# One tool result should not be 12K tokens. The full text always stays on disk;
# past this the response says it was cut and the caller can re-ask with
# full=True or follow `url`. Fires on a handful of the longest dialogs.
USAGE_CAP = 12_000

# How many "you might have meant" entries a miss carries, and how deep to look
# for them before filtering to the ones that actually have a guide.
RELATED_LIMIT = 5
_RELATED_POOL = 15


@lru_cache(maxsize=1)
def _index() -> dict[str, dict[str, str]]:
    """``{uri_lower: {"uri", "feature_name", "menu_path"}}`` for every bundled guide."""
    if not _INDEX_PATH.exists():  # pragma: no cover - only if the tree is missing
        return {}
    raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    return {uri.lower(): {"uri": uri, **entry} for uri, entry in raw.items()}


def has_guide(uri: str) -> bool:
    """True when a bundled guide exists for this exact endpoint."""
    return str(uri or "").lower() in _index()


def menu_path(uri: str) -> str:
    """The endpoint's ribbon route, or "". Used by ``search_index`` at build time."""
    return _index().get(str(uri or "").lower(), {}).get("menu_path", "")


@lru_cache(maxsize=256)
def _read(uri: str) -> dict[str, Any] | None:
    """Parse one guide file.

    Only ever reached for a uri already in :func:`_index`, so the cache key space
    is bounded by the bundled tree and a caller cannot grow it — which matters in
    http mode, where one process serves every request.
    """
    known = _index().get(str(uri or "").lower())
    if not known:
        return None
    path = _FEATURE_DIR / f"{known['uri']}.json"
    if not path.exists():  # index and tree disagree; check_features gates this
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _clip(text: str, cap: int) -> str:
    """Trim to ``cap``, preferring a paragraph or sentence end so it stays readable."""
    if len(text) <= cap:
        return text
    window = text[:cap]
    for marker in ("\n\n", ". "):
        stop = window.rfind(marker)
        if stop >= cap * 0.5:
            return window[: stop + len(marker)].rstrip()
    space = window.rfind(" ")
    return (window[:space] if space > 0 else window).rstrip(" ,;:") + "…"


def guide(entry: dict[str, Any], full: bool = False) -> dict[str, Any]:
    """The GUI guide for an already-resolved catalog entry, or a useful miss record.

    ``entry`` is what ``catalog.describe`` returns — including its error dict for
    an unknown name, which is passed straight back. "No such endpoint" and "no
    guide for this endpoint" need different fixes from the caller, so they must
    not collapse into one another.
    """
    uri = str(entry.get("uri") or "")
    if not uri:
        return entry
    record = _read(uri)
    if record is None:
        return _miss(entry)

    out: dict[str, Any] = {
        "guide": True,
        "group": entry.get("group"),
        "name": entry.get("name"),
        "uri": record.get("uri", uri),
    }
    for field in ("feature_name", "menu_path", "function", "usage", "note", "url"):
        value = record.get(field)
        if value:
            out[field] = value

    usage = out.get("usage", "")
    if not full and len(usage) > USAGE_CAP:
        out["usage"] = _clip(usage, USAGE_CAP)
        out["usage_truncated"] = True
        out["usage_full_chars"] = len(usage)
    return out


def _miss(entry: dict[str, Any]) -> dict[str, Any]:
    """What to say when the manual has no article for this endpoint.

    Never a bare "not found": the failure mode being defended against is the
    model filling the silence with an invented ribbon path. The record states
    plainly that no guide exists, hands over whatever the schema already knows
    (``merge_manual`` left a manual link on 77 of the uncovered endpoints), and
    points at real guided features nearby.
    """
    uri = str(entry.get("uri") or "")
    out: dict[str, Any] = {
        "guide": False,
        "group": entry.get("group"),
        "name": entry.get("name"),
        "uri": uri,
        "reason": (
            "No GUI guide is bundled for this endpoint. Guides come from the public "
            f"manual's feature articles and cover {len(_index())} of the 572 endpoints "
            "(db, ope, doc, view, post); design, rating and temp have none. This is a "
            "source limit, not a missing file."
        ),
    }
    for field in ("manual_title", "manual_url"):
        if entry.get(field):
            out[field] = entry[field]

    related = _related(entry)
    if related:
        out["related"] = related
    out["next"] = (
        f'midas_describe("{uri}") has the schema and a working example. Tell the user '
        "no bundled UI guide exists for this one rather than inventing a menu path."
    )
    return out


def _related(entry: dict[str, Any]) -> list[dict[str, str]]:
    """Guided endpoints plausibly describing the same dialog as ``entry``.

    Two sources, siblings first. A leaf name that repeats under ``db``
    (``design/RC/.../MEMB`` -> ``db/MEMB``) is usually literally the same dialog,
    so it outranks anything the ranker finds. The rest come from the existing
    BM25 index, filtered to guides and to hits that actually share a term with
    the endpoint's own name — without that filter the ranker will confidently
    offer an unrelated endpoint for a query it has nothing for, which is worse
    than offering nothing.
    """
    uri = str(entry.get("uri") or "")
    name = str(entry.get("name") or "")
    out: list[dict[str, str]] = []
    seen = {uri.lower()}

    def add(candidate_uri: str, why: str) -> None:
        known = _index().get(candidate_uri.lower())
        if not known or candidate_uri.lower() in seen:
            return
        seen.add(candidate_uri.lower())
        item = {"uri": known["uri"], "why": why}
        for field in ("feature_name", "menu_path"):
            if known.get(field):
                item[field] = known[field]
        out.append(item)

    # 1. Same leaf name in another group.
    if name:
        for other in _index().values():
            if other["uri"].rsplit("/", 1)[-1].lower() == name.lower():
                add(other["uri"], f"same endpoint name under {other['uri'].split('/', 1)[0]}")

    # 2. Ranker hits that share vocabulary with this endpoint's name/title.
    query = " ".join(x for x in (name, search_index.title_of(search_index.summarize(entry))) if x)
    if query.strip():
        wanted = set(search_index.tokenize(query))
        for doc in search_index.rank(query, limit=_RELATED_POOL):
            if len(out) >= RELATED_LIMIT:
                break
            if not wanted & set(search_index.tokenize(doc.uri + " " + doc.summary)):
                continue
            add(doc.uri, "similar feature")

    return out[:RELATED_LIMIT]
