"""Guard: ``midas_lookup`` must keep the right endpoint on the shortlist.

Rationale: with 572 endpoints reachable only through ``midas_lookup``, an
endpoint the search never surfaces may as well not exist. The metric that
matters is therefore RECALL@limit — is the answer anywhere in the returned
list — not whether it ranks first. The model reads each hit's ``desc`` and
picks, so a correct answer at rank 6 is as good as one at rank 1.

Top-1 is reported too, but not gated: it moves with how well the corpus
expresses relative importance, which is not something the scorer can fix.

Where the queries come from, and which ones actually test anything
------------------------------------------------------------------
``data/eval_queries.json`` is derived from MIDAS's **public API manual**
(support.midasuser.com), not written by hand:

  ``api_title``      the API article's title            "Constraint Support"
  ``feature_title``  the GUI feature's name             "Define Supports"
  ``function``       the manual's one-line statement    "Restrain the
                     of what the feature does           degrees-of-freedom of
                                                        selected nodes"

**Two of those three are now circular.** ``scripts/merge_manual.py`` copies the
manual's ``manual_title``/``feature_name`` into the bundled schemas, and
``search_index`` indexes them — so an ``api_title`` or ``feature_title`` query
is looking for text that was put into the index on purpose. Those score near
100% by construction and prove only that the wiring works.

``function`` is the honest set: the manual's prose is NOT indexed, so those 92
queries are scored against text the index has never seen. **The floor is
applied to it alone**; the circular sources get a much higher floor that acts
as a wiring check, and a total is reported for continuity but means little.

Run:  ``python -m midas_mcp.eval_search``  (exit 0 = pass, 1 = below threshold)

Still not a benchmark of real usage: the manual describes what a feature *is*,
while a user asks for what they *want to do*, and the corpus here is 224 of the
572 endpoints — design/rating/temp have no coverage at all. Queries from real
sessions are what would replace this; when they arrive, mark them ``session``
and move the gate onto them.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from . import catalog

EVAL_PATH = Path(__file__).resolve().parent.parent / "data" / "eval_queries.json"

# Query sources whose text is copied into the index by scripts/merge_manual.py.
# Scoring against them is circular; they are kept only as a wiring check.
CIRCULAR_SOURCES = frozenset({"api_title", "feature_title"})

# The real gate: sources the index has never seen. Set just under the measured
# level so ordinary corpus edits do not trip it; a genuine drop still does.
INDEPENDENT_FLOOR = 0.88

# Circular sources should be near-perfect. Falling below this means the merge or
# the index wiring broke, not that ranking got worse.
CIRCULAR_FLOOR = 0.97

LIMIT = 10

# The limit the tool tells the model to retry at. Reported alongside LIMIT so
# the instruction's payoff — how much of the tail a second search recovers — is
# a measured number rather than an assumption.
RETRY_LIMIT = 30

# How deep to look when reporting a miss — a target at rank 14 is a ranking
# problem, one absent entirely is an indexing problem, and the fix differs.
DIAGNOSTIC_DEPTH = 200


def load_cases(path: Path = EVAL_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def accepted(case: dict) -> set[str]:
    """The endpoint(s) that count as correct. ``alt`` holds sibling endpoints
    (the ``X`` / ``X-M1`` pairs describe the same thing with different fields)."""
    return {case["uri"], *case.get("alt", [])}


def rank_of(query: str, wanted: set[str], limit: int) -> int | None:
    """1-based rank of the first accepted endpoint, or None if none appear."""
    for position, hit in enumerate(catalog.search(query, limit=limit), 1):
        if hit["uri"] in wanted:
            return position
    return None


def _stats(ranks: list[int | None]) -> dict:
    total = len(ranks) or 1
    return {
        "n": len(ranks),
        "found": sum(r is not None for r in ranks),
        "recall": sum(r is not None for r in ranks) / total,
        "top1": sum(r == 1 for r in ranks) / total,
    }


def evaluate(cases: list[dict], limit: int = LIMIT) -> dict:
    by_source: dict[str, list[int | None]] = defaultdict(list)
    misses = []
    for case in cases:
        wanted = accepted(case)
        rank = rank_of(case["q"], wanted, limit)
        source = case.get("src", "?")
        by_source[source].append(rank)
        if rank is None:
            misses.append((source, case["q"], case["uri"],
                           rank_of(case["q"], wanted, DIAGNOSTIC_DEPTH)))

    independent = [r for s, g in by_source.items() if s not in CIRCULAR_SOURCES for r in g]
    circular = [r for s, g in by_source.items() if s in CIRCULAR_SOURCES for r in g]

    # What a retry at RETRY_LIMIT would recover, over the independent set only.
    retry_cases = [c for c in cases if c.get("src") not in CIRCULAR_SOURCES]
    retry_recovered = sum(
        1 for c in retry_cases
        if rank_of(c["q"], accepted(c), limit) is None
        and rank_of(c["q"], accepted(c), RETRY_LIMIT) is not None
    )
    return {
        "by_source": {s: _stats(g) for s, g in sorted(by_source.items())},
        "independent": _stats(independent),
        "circular": _stats(circular),
        "total": _stats([r for g in by_source.values() for r in g]),
        "retry": {
            "needed": sum(r is None for r in independent),
            "recovered": retry_recovered,
            "n": len(retry_cases),
        },
        "misses": misses,
    }


def main() -> int:
    result = evaluate(load_cases())
    ind, cir, tot = result["independent"], result["circular"], result["total"]

    print(f"limit={LIMIT}\n")
    print(f"{'source':16}{'n':>5}{'recall':>10}{'top-1':>9}")
    for source, stat in result["by_source"].items():
        tag = "  (circular)" if source in CIRCULAR_SOURCES else ""
        print(f"{source:16}{stat['n']:5}{stat['recall']:9.1%}{stat['top1']:9.1%}{tag}")

    print(f"\n{'INDEPENDENT':16}{ind['n']:5}{ind['recall']:9.1%}{ind['top1']:9.1%}   <- the gate")
    print(f"{'circular':16}{cir['n']:5}{cir['recall']:9.1%}{cir['top1']:9.1%}   <- wiring check")
    print(f"{'total':16}{tot['n']:5}{tot['recall']:9.1%}{tot['top1']:9.1%}   <- inflated, for continuity only")

    retry = result["retry"]
    if retry["n"]:
        print(f"\nretry at limit={RETRY_LIMIT}: {retry['needed']}/{retry['n']} independent queries "
              f"need one ({retry['needed'] / retry['n']:.0%}), and it recovers "
              f"{retry['recovered']} of those {retry['needed']}")

    if result["misses"]:
        print(f"\nmissed at limit={LIMIT} ({len(result['misses'])}):")
        for source, query, uri, deep in result["misses"]:
            where = f"rank {deep}" if deep else f"not in top {DIAGNOSTIC_DEPTH}"
            recovered = " -> retry finds it" if deep and deep <= RETRY_LIMIT else ""
            print(f"  [{source}] {uri:20} ({where}){recovered}  {query[:56]}")

    failed = False
    if ind["recall"] < INDEPENDENT_FLOOR:
        print(f"\nFAIL: independent recall {ind['recall']:.1%} is below the {INDEPENDENT_FLOOR:.0%} floor.")
        failed = True
    if cir["n"] and cir["recall"] < CIRCULAR_FLOOR:
        print(f"\nFAIL: circular recall {cir['recall']:.1%} is below {CIRCULAR_FLOOR:.0%} — "
              "the manual merge or the index wiring is broken, not the ranking.")
        failed = True
    if failed:
        return 1

    print(f"\nOK: independent recall meets the {INDEPENDENT_FLOOR:.0%} floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
