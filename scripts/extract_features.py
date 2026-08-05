"""Extract the manual's feature articles into ``data/features/``.

What this produces
------------------
One file per endpoint, ``data/features/<uri>.json``, mirroring the layout of
``data/schemas`` — the endpoint's uri IS its path, so nothing has to invent or
invert a slug, and ``midas_guide`` finds a guide with a single ``Path`` join.
Plus ``data/features/_index.json``, a ``{uri: {feature_name, menu_path}}`` map
that lets ``catalog`` put a ``guide`` breadcrumb on a search hit and lets
``search_index`` fold the menu path into the index, both without opening 155
files.

    data/features/db/NODE.json
    {
      "uri": "db/NODE",
      "feature_name": "Create Nodes",
      "function": "Create a new node or ...",
      "menu_path": "From the Main Menu select [Node/Element] tab > ...",
      "usage": "Click the [...] button ...",
      "note": "...",
      "url": "https://support.midasuser.com/hc/en-us/articles/..."
    }

Three things here are easy to get wrong
---------------------------------------
1. ``usage`` must NOT go through ``norm``. ``norm`` collapses newlines, and the
   dialog text separates a field's heading from its explanation with a blank
   line — flatten that and "Start Node Number" fuses into the sentence after
   it, which is the one thing that makes the text readable field by field.
   ``block`` is the normaliser for anything whose paragraph structure matters.

2. ``usage`` is not just ``sections.input``. The scraper splits a dialog on
   bold field headings, so a heading literally named "Description" starts a new
   section that goes on documenting the *same* dialog (16 records, 77 KB).
   ``output`` and ``example`` are one-off instances of the same artefact. All
   four are concatenated in the article's own key order.

3. 128 distinct articles serve 155 endpoints, so ~113 KB of text is duplicated
   across sibling endpoints (``db/MVLD`` and its six variants share one
   article). That is deliberate. Pointing siblings at a shared record would
   save disk that never reaches a response, and would buy an indirection plus a
   referential-integrity check in the guard.

Coverage is a source limit, not a bug: the public manual has feature articles
for db/ope/doc/view/post only. design, rating and temp have none, and 37 more
articles are restricted upstream (HTTP 401 even for the page) — see
``scripts/fetch_articles.py``.

Deliberately NOT indexed: ``function`` and ``usage``. The eval set's
``function`` queries are drawn from that same text, so indexing it would make
``python -m midas_mcp.eval_search`` measure itself. ``menu_path`` IS indexed —
it is not the source of any eval query, and 0 of the 92 ``function`` queries
are fully covered by menu-path terms.

Run:
    python scripts/extract_features.py --source <API_Data dir>
    python scripts/extract_features.py --source <dir> --dry-run
    python scripts/extract_features.py --source <dir> --prune
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "data" / "features"
INDEX_PATH = OUT_DIR / "_index.json"

# Sections that document the dialog itself, in the order they are concatenated.
USAGE_SECTIONS = ("input", "description", "output", "example")
# Sections that land in their own field. Anything outside these two tuples is
# unrecognised and gets reported rather than silently dropped.
STRUCTURAL_SECTIONS = ("function", "call", "note")


def norm(value: object) -> str:
    """Single-line normalisation. Do not use on text whose line breaks matter."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def block(value: object) -> str:
    """Normalise prose while preserving its paragraph structure.

    Trailing spaces go and runs of 3+ newlines collapse to one blank line; the
    blank line between a field heading and its explanation stays. Saves about
    1% of bytes — the point is readability, not size.
    """
    lines = [line.rstrip() for line in str(value or "").replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def feature_key(name: object) -> str:
    """Feature names carry a trailing '↗' in the index; match without it.

    Also unescapes HTML entities — two mapping values arrive as
    ``(Design&gt; Steel) ...`` and match nothing until they are decoded.
    """
    return norm(html.unescape(str(name or ""))).replace("↗", "").strip().lower()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def build_record(uri: str, article: dict) -> tuple[dict[str, str], list[str]]:
    """One guide record, plus any section names this script does not recognise."""
    sections = article.get("sections") or {}
    unknown = [k for k in sections if k not in USAGE_SECTIONS and k not in STRUCTURAL_SECTIONS]

    record: dict[str, str] = {"uri": uri, "feature_name": norm(article.get("title"))}
    for field, source in (("function", "function"), ("menu_path", "call")):
        text = norm(sections.get(source))
        if text:
            record[field] = text

    # Article key order, not USAGE_SECTIONS order — the scraper emits `input`
    # before the heading-split remainder, and that is the reading order.
    usage = [block(sections[k]) for k in sections if k in USAGE_SECTIONS and block(sections[k])]
    if usage:
        record["usage"] = "\n\n".join(usage)

    note = block(sections.get("note"))
    if note:
        record["note"] = note
    if article.get("url"):
        record["url"] = norm(article["url"])
    return record, unknown


def write_if_changed(path: Path, payload: str, dry_run: bool) -> bool:
    """Write only on a real change, so regeneration does not churn 155 mtimes."""
    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return False
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", required=True, type=Path,
        help="directory holding api_to_feature_mapping_v3.json and GENNX_Feature/",
    )
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--prune", action="store_true",
                        help="delete guide files this run did not produce (default: report only)")
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

    records: dict[str, dict[str, str]] = {}
    unknown_sections: dict[str, int] = {}
    no_article = not_served = blank = 0

    for endpoint, feature in mapping.items():
        if not str(feature).strip():
            blank += 1
            continue
        uri = known.get(endpoint.lower())
        if not uri:
            not_served += 1
            continue
        article = load_json(args.source / "GENNX_Feature" / f"{articles.get(feature_key(feature), '')}.json")
        if not article:
            no_article += 1
            continue

        record, unknown = build_record(uri, article)
        for name in unknown:
            unknown_sections[name] = unknown_sections.get(name, 0) + 1
        records[uri] = record

    # ---- write the tree ------------------------------------------------
    written = 0
    for uri, record in sorted(records.items()):
        payload = json.dumps(record, ensure_ascii=False, indent=1) + "\n"
        written += write_if_changed(OUT_DIR / f"{uri}.json", payload, args.dry_run)

    index_payload = json.dumps(
        {
            uri: {k: v for k, v in (("feature_name", r.get("feature_name")),
                                    ("menu_path", r.get("menu_path"))) if v}
            for uri, r in sorted(records.items())
        },
        ensure_ascii=False, indent=1,
    ) + "\n"
    index_changed = write_if_changed(INDEX_PATH, index_payload, args.dry_run)

    # ---- orphans -------------------------------------------------------
    produced = {f"{uri}.json" for uri in records}
    orphans = sorted(
        p for p in OUT_DIR.rglob("*.json")
        if p != INDEX_PATH and str(p.relative_to(OUT_DIR)).replace("\\", "/") not in produced
    ) if OUT_DIR.exists() else []
    for path in orphans:
        if args.prune and not args.dry_run:
            path.unlink()

    # ---- report --------------------------------------------------------
    verb = "would write" if args.dry_run else "wrote"
    total = len(known)
    print(f"{verb} {len(records)} guide(s) of {total} endpoints ({len(records) * 100 // total}%) -> "
          f"{OUT_DIR.relative_to(REPO)}")
    print(f"  changed: {written} file(s){' + _index.json' if index_changed else ''}")
    print(f"  skipped: {blank} unmapped in the source, {no_article} article file missing, "
          f"{not_served} endpoint not in catalog")

    with_usage = sum("usage" in r for r in records.values())
    with_note = sum("note" in r for r in records.values())
    print(f"  content: {with_usage} with usage, {with_note} with note")

    if unknown_sections:
        print(f"  WARNING unrecognised section(s), not captured: {unknown_sections}")
    if orphans:
        action = "deleted" if (args.prune and not args.dry_run) else "left in place (use --prune)"
        print(f"  orphan guide file(s) {action}: {len(orphans)}")
        for path in orphans[:5]:
            print(f"    {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
