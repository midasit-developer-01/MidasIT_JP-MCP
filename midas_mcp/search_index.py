"""BM25 ranking over the bundled endpoint catalog.

Why this is separate from ``catalog``
-------------------------------------
``catalog`` owns loading and ``describe``; ranking is its own concern with its
own derived state (an IDF table built once at import-time cost). Keeping them
apart means the scorer can be tuned and evaluated (``midas_mcp.eval_search``)
without touching the loader.

What it ranks on
----------------
Each endpoint is one short document: **name + uri + the schema's description**
(570/572 endpoints carry one), plus the manual's own names for it where those
were merged in (``manual_title``/``feature_name``, 228 of 572 endpoints — see
``scripts/merge_manual.py``). The manual names matter because the descriptions
are DTO-derived and a user asks in UI wording: "Graphic Files", not "View
Capture".

Schema *fields* are deliberately NOT indexed — measured on the seed eval set
they leave recall unchanged but cost top-1 accuracy (11/18 -> 9/18) and
quadruple the index (37.8 -> 155.6 terms/doc), because generic field names like
``LOAD``/``SECTION`` appear nearly everywhere. The manual's prose sections are
left out for the same reason.

Why BM25 rather than hand-tuned weights
---------------------------------------
The old scorer added fixed points per match, so a query token hitting a common
word scored the same as a rare one — ``"load"`` (158/572 docs) counted as much
as ``"spectrum"`` (3/572). BM25's IDF derives that weighting from the corpus
instead, so it stays correct as endpoints are added. Retrieval quality on the
seed set: 47% recall / 7% top-1 before, 100% / 61% after.

The scorer's job is RECALL, not precision: it hands the model a shortlist with
each hit's description, and the model reads them and picks. It does not need to
rank the answer first — only to keep it on the list.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

# BM25 parameters. Standard defaults; documents here are short and uniform
# (~38 terms), so `b` (length normalisation) has little to do either way.
K1 = 1.5
B = 0.75

# Every catalog description opens by naming the thing — "Constraint Supports
# (CONS). Keyed by node number; ..." — and that opening carries far more
# identifying signal than the field-level prose after it. Weighting it up is
# what makes long descriptive queries work: "create general link elements, each
# linking two nodes" overlaps `db/NODE` on four common words but `db/NLNK` on
# the two that matter, and only a title boost lets the latter win.
# Measured on 372 manual-derived queries, split by endpoint: held-out recall@10
# 88.8% -> 95.1% and top-1 50.3% -> 63.6% going from 1.0 to 3.0.
TITLE_WEIGHT = 3.0

# Matches "Constraint Supports (CONS)." at the head of a description. Anchored
# and length-capped so a sentence that merely contains parentheses cannot be
# mistaken for the opening title.
_TITLE_RE = re.compile(r"^(.{0,80}?)\s*\([A-Za-z0-9_\-]+\)\s*\.")

# Name-match bonuses, added on top of the BM25 score. Endpoint names are 4-letter
# abbreviations, so a query that names one directly ("SPLC", "db/NODE") is a much
# stronger signal than any term overlap and should outrank it.
NAME_EXACT = 24.0
NAME_PARTIAL = 12.0
NAME_TOKEN = 6.0

# Prior on the group, scaled down (/PRIOR_SCALE) so it breaks ties between
# comparable matches without overriding a clearly better term match. Ordered by
# how often a group answers a modelling question: `db` holds the model itself,
# while `design`/`rating`/`temp` are deep code-standard trees that otherwise
# crowd the results (the same leaf name repeats across many standards).
GROUP_PRIOR = {
    "db": 24.0,
    "doc": 20.0,
    "view": 14.0,
    "post": 14.0,
    "ope": 10.0,
    "config": 6.0,
    "requestinfo": 4.0,
    "temp": 0.0,
    "design": 0.0,
    "rating": 0.0,
}
PRIOR_SCALE = 8.0

# Penalty per extra path segment: `design/RC/KDS-41-20-2022/DCRM` is a specific
# code standard, so it should sit below a plain `db/SECT` for a generic query.
DEPTH_PENALTY = 0.5

_WORD_RE = re.compile(r"[^a-z0-9]+")


def stem(word: str) -> str:
    """Porter step 1a — plural stripping only.

    Deliberately just this one step: it collapses the singular/plural pairs that
    actually differ between a user's phrasing and the catalog's wording
    (``support``/``supports`` — the one miss on the seed set before stemming),
    without the aggressive truncation later Porter steps bring.

    The ``ss`` guard matters: naive suffix stripping turns ``mass`` into ``mas``
    while ``masses`` becomes ``mass``, so the two stop matching. Verified against
    all 2,214 vocabulary terms in the catalog.
    """
    if len(word) <= 3:
        return word
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ss"):
        return word
    if word.endswith("s"):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Split on non-alphanumerics, lowercase, stem. Used for both docs and queries."""
    return [stem(t) for t in _WORD_RE.split(str(text).lower()) if t]


def summarize(entry: dict[str, Any]) -> str:
    """The endpoint's human description, from wherever the schema keeps it.

    Per-endpoint files nest it as ``schema[NAME].description``; a few put it at
    ``schema.description``. Returns "" when neither exists (2 of 572).
    """
    schema = entry.get("schema")
    if not isinstance(schema, dict):
        return ""
    for value in schema.values():
        if isinstance(value, dict) and value.get("description"):
            return str(value["description"])
    return str(schema.get("description") or "")


@dataclass
class Doc:
    """One endpoint as an indexed document.

    ``key`` keeps the catalog key as written (``SPLC``) since callers echo it
    back to ``describe``; ``name`` is its lowercased form, used for matching.
    """

    group: str
    key: str
    name: str
    uri: str
    summary: str
    entry: dict[str, Any]
    tf: dict[str, int] = field(default_factory=dict)
    length: int = 0
    # Terms from the description's opening title, scored at TITLE_WEIGHT.
    title_terms: frozenset[str] = frozenset()


@dataclass
class Index:
    docs: list[Doc]
    idf: dict[str, float]
    avg_length: float


def title_of(summary: str) -> str:
    """The naming phrase a description opens with, or a short prefix as fallback."""
    match = _TITLE_RE.match(summary or "")
    return match.group(1) if match else (summary or "")[:60]


@lru_cache(maxsize=1)
def build() -> Index:
    """Build the BM25 index once, over ``catalog._load()``.

    Costs ~22ms on 572 endpoints, against the ~479ms the catalog load itself
    already spends parsing JSON — negligible even under stdio, where a fresh
    process starts per session.
    """
    from . import catalog

    docs: list[Doc] = []
    for group, name, entry in catalog._iter_entries():
        summary = summarize(entry)
        uri = str(entry.get("uri", ""))
        # The manual's names sit in the title field alongside the description's
        # own opening: both are ways of naming the same endpoint, and a query
        # matching either should get the same boost.
        manual = " ".join(
            str(entry.get(f) or "") for f in ("manual_title", "feature_name")
        )
        terms = tokenize(name) + tokenize(uri) + tokenize(summary) + tokenize(manual)
        doc = Doc(
            group=group,
            key=name,
            name=name.lower(),
            uri=uri,
            summary=summary,
            entry=entry,
            title_terms=frozenset(tokenize(title_of(summary)) + tokenize(manual)),
        )
        for term in terms:
            doc.tf[term] = doc.tf.get(term, 0) + 1
        doc.length = len(terms)
        docs.append(doc)

    n = len(docs) or 1
    df: dict[str, int] = {}
    for doc in docs:
        for term in doc.tf:
            df[term] = df.get(term, 0) + 1
    idf = {t: math.log((n - v + 0.5) / (v + 0.5) + 1) for t, v in df.items()}
    avg = sum(d.length for d in docs) / n
    return Index(docs=docs, idf=idf, avg_length=avg or 1.0)


def _bm25(doc: Doc, query_terms: list[str], index: Index) -> float:
    """BM25, with terms that appear in the description's title weighted up."""
    score = 0.0
    for term in query_terms:
        freq = doc.tf.get(term, 0)
        if not freq:
            continue
        norm = 1 - B + B * doc.length / index.avg_length
        contribution = index.idf.get(term, 0.0) * (freq * (K1 + 1)) / (freq + K1 * norm)
        if term in doc.title_terms:
            contribution *= TITLE_WEIGHT
        score += contribution
    return score


def _name_bonus(doc: Doc, query: str, query_terms: list[str]) -> float:
    """Reward a query that names the endpoint, rather than describing it."""
    if query == doc.name:
        return NAME_EXACT
    if query in doc.name or doc.name in query:
        return NAME_PARTIAL
    bonus = 0.0
    for term in query_terms:
        if term == doc.name:
            bonus += NAME_PARTIAL
        elif len(term) > 3 and (term in doc.name or doc.name in term):
            bonus += NAME_TOKEN
    return bonus


def rank(query: str, limit: int, group: str | None = None) -> list[Doc]:
    """Return up to ``limit`` docs, best first.

    An empty query with ``group`` set lists that group — the caller relies on
    this to enumerate a group, and BM25 alone would score every doc 0 there.
    """
    index = build()
    wanted = group.strip().lower() if group else None
    candidates = [d for d in index.docs if wanted is None or d.group.lower() == wanted]

    normalized = query.strip().lower()
    if not normalized:
        # No query to rank by: only meaningful as "list this group".
        return candidates[:limit] if wanted is not None else []

    # De-duplicated: a word repeated in the query ("link ... linking") says no
    # more about relevance than one occurrence, and counting it twice lets a
    # long descriptive sentence outweigh a precise short one.
    query_terms = list(dict.fromkeys(tokenize(normalized)))
    scored: list[tuple[float, Doc]] = []
    for doc in candidates:
        score = _bm25(doc, query_terms, index)
        if score <= 0:
            continue
        score += _name_bonus(doc, normalized, query_terms)
        score += GROUP_PRIOR.get(doc.group, 0.0) / PRIOR_SCALE
        score -= DEPTH_PENALTY * max(0, doc.uri.count("/") - 1)
        scored.append((score, doc))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [doc for _, doc in scored[:limit]]
