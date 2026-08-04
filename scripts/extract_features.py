"""Extract the manual's feature articles into ``data/features.json``.

Why keep this at all
--------------------
``merge_manual.py`` already folds the manual's *names* into the schemas, which
is what the search index needs. This file keeps the rest — what each feature
*does*, and where it lives in the UI — as data rather than throwing it away:

  - It is the vocabulary bridge. The remaining search misses are cases where the
    manual's wording and the DTO's wording do not overlap at all ("Constrain a
    specific node to subordinate to the movements of certain nodes" vs "Linear
    Constraints"). Any synonym work starts from this text.
  - It answers "what can I do here?", which search cannot: a browse view over
    a group wants the feature's own description, not the DTO's field notes.
  - It removes the dependency on a local scrape. Without it, every future use
    of this data means re-running the collector against Help Center.

Deliberately NOT indexed by ``search_index``. The eval set's ``function``
queries are drawn from this same text, and they are the only independent
measurement left — indexing it would make every query in the set circular and
leave nothing honest to gate on. If it is ever indexed, the eval set has to be
replaced with queries from real sessions first.

Output shape — keyed by endpoint, so "which endpoint is this a feature of" is
the lookup the file answers:

    {
      "db/CONS": {
        "feature_name": "Define Supports",
        "function": "Restrain the degrees-of-freedom of selected nodes...",
        "menu_path": "From the Main Menu select [Boundary] tab > ...",
        "url": "https://support.midasuser.com/hc/en-us/articles/..."
      }
    }

Run:
    python scripts/extract_features.py --source <API_Data dir>
    python scripts/extract_features.py --source <dir> --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_PATH = REPO / "data" / "features.json"

# The manual's prose runs long on a few endpoints (up to ~2.9K characters of
# option-by-option notes). Keep the part that states what the feature is for.
FUNCTION_CAP = 400
MENU_CAP = 200


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def feature_key(name: object) -> str:
    """Feature names carry a trailing '↗' in the index; match without it."""
    return norm(name).replace("↗", "").strip().lower()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def clip(text: str, cap: int) -> str:
    """Trim to ``cap``, preferring a sentence end so the text stays readable."""
    if len(text) <= cap:
        return text
    window = text[:cap]
    stop = window.rfind(". ")
    if stop >= cap * 0.5:
        return window[: stop + 1]
    space = window.rfind(" ")
    return (window[:space] if space > 0 else window).rstrip(" ,;:") + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", required=True, type=Path,
        help="directory holding api_to_feature_mapping_v3.json and GENNX_Feature/",
    )
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    mapping = load_json(args.source / "api_to_feature_mapping_v3.json")
    index = load_json(args.source / "GENNX_Feature" / "_index.json")
    if mapping is None or index is None:
        sys.exit(f"source is missing api_to_feature_mapping_v3.json or GENNX_Feature/_index.json: {args.source}")
    articles = {feature_key(k): str(v) for k, v in index.items()}

    # Only endpoints this server actually serves — the manual covers a few that
    # are not in the bundled catalog.
    sys.path.insert(0, str(REPO))
    from midas_mcp import catalog
    known = {str(e.get("uri", "")).lower(): str(e.get("uri", "")) for _, _, e in catalog._iter_entries()}

    out: dict[str, dict[str, str]] = {}
    no_article = not_served = 0

    for endpoint, feature in mapping.items():
        uri = known.get(endpoint.lower())
        if not uri:
            not_served += 1
            continue
        article = load_json(args.source / "GENNX_Feature" / f"{articles.get(feature_key(feature), '')}.json")
        if not article:
            no_article += 1
            continue

        sections = article.get("sections") or {}
        record = {"feature_name": norm(article.get("title"))}
        function = norm(sections.get("function"))
        if function:
            record["function"] = clip(function, FUNCTION_CAP)
        menu = norm(sections.get("call"))
        if menu:
            record["menu_path"] = clip(menu, MENU_CAP)
        if article.get("url"):
            record["url"] = norm(article["url"])
        out[uri] = record

    payload = json.dumps(dict(sorted(out.items())), ensure_ascii=False, indent=1) + "\n"
    if not args.dry_run:
        OUT_PATH.write_text(payload, encoding="utf-8")

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {len(out)} endpoints -> {OUT_PATH.relative_to(REPO)} ({len(payload) / 1024:.0f} KB)")
    print(f"  no feature article: {no_article} · endpoint not in catalog: {not_served}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
