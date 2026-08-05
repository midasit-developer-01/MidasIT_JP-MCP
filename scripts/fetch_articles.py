"""Fetch the manual's feature articles that the local scrape is missing.

Why this exists
---------------
``GENNX_Feature/_index.json`` lists 627 article ids but only 527 of the files
were ever downloaded. 40 of the missing ids are the *only* thing standing
between 151 and 199 covered endpoints — the endpoint-to-feature match already
succeeds for them, the article file just is not there. Among them is
``doc/ANAL`` (Perform Analysis), step 4 of the README's own example flow.

The HTML-to-``sections`` parser that produced the existing 527 files does not
live in this repo, so it is reimplemented here. Nothing local could validate it
— ``etc/midas_articles_raw/`` holds the *API schema* articles and shares zero
ids with the feature set — so instead the script re-fetches articles whose
parsed output we already have and diffs against it (``--verify``). A parser
that reproduces those byte for byte is the one we trust on the missing 40.

The parse rules, recovered from that diff:

  - ``<h2>`` opens a section; its text lowercased is the section key.
  - Every block element (h1-h6, p, li, td, th) contributes one text block.
  - A section's value is those blocks each ``.strip()``ed — which also drops the
    ``<p>&nbsp;</p>`` spacers to "", since ``"\\xa0".strip() == ""`` — joined
    with a blank line, then stripped. That is where the runs of 3+ newlines in
    the existing files come from; they are load-bearing spacing, not noise.
  - ``full_text`` joins the same blocks *unstripped*, headings included, so it
    keeps the trailing NBSP that the section values lose.

This is the only code in the repo that touches the network, and it is not
packaged: ``scripts/`` ships in neither the wheel, the image, nor the .mcpb.

Run:
    python scripts/fetch_articles.py --source <API_Data dir> --verify
    python scripts/fetch_articles.py --source <API_Data dir> --dry-run
    python scripts/fetch_articles.py --source <API_Data dir>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

API = "https://support.midasuser.com/api/v2/help_center/en-us/articles/{id}.json"
PAGE = "https://support.midasuser.com/hc/en-us/articles/{id}"

# Elements that end a text run. Everything else (strong, em, a, span) is inline
# and its text is folded into the block it sits in.
BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "pre", "blockquote"})
# Containers that usually only wrap other blocks, but occasionally hold text
# directly — the "Details..." accordion toggles are a bare <strong> inside a
# <div>. Treated as blocks, then dropped when they turn out to be empty, so a
# wrapper does not contribute a phantom blank line.
CONTAINER_TAGS = frozenset({"div", "section", "figcaption"})
SKIP_TAGS = frozenset({"script", "style"})

# Articles whose parsed output is already on disk, used by --verify. Chosen to
# span the shapes: header-only, short, typical, multi-section, note-bearing.
VERIFY_IDS = (
    "29276656790041",  # Create Nodes        - typical, h3 field headings
    "29102537168025",  # New Project         - function + call only
    "29025968275097",  # Material Properties - long input
    "29487895285913",  # Main Control Data   - input + note
    "29285558012313",  # Element Beam Loads  - long, tables
    "29441929771033",  # (whatever resolves) - extra shape
)

REQUEST_PAUSE = 0.4  # be a polite client; 40 articles is ~16s


class _Blocks(HTMLParser):
    """Flatten article HTML into ``(tag, text)`` runs, one per block element.

    Only text *inside* a block element counts. The newlines between tags in the
    source are formatting, not content — treating them as blocks was the first
    bug this parser had, and it showed up as an extra blank line before every
    paragraph. An empty block element (``<p>&nbsp;</p>``) is still emitted,
    because that is exactly what produces the wide gaps in the stored text.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._buf: list[str] = []
        self._tag: str | None = None
        self._skip = 0

    def _close_block(self) -> None:
        if self._tag is not None:
            text = "".join(self._buf)
            # A container that held only other blocks contributes nothing; one
            # that held text of its own (the "Details..." toggles) is real.
            if self._tag not in CONTAINER_TAGS or text.strip():
                self.blocks.append((self._tag, text))
        self._buf = []
        self._tag = None

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in BLOCK_TAGS or tag in CONTAINER_TAGS:
            self._close_block()          # handles unclosed/nested blocks
            self._tag = tag
        elif tag == "br" and self._tag is not None:
            self._buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in BLOCK_TAGS or tag in CONTAINER_TAGS:
            self._close_block()

    def handle_data(self, data: str) -> None:
        if not self._skip and self._tag is not None:
            self._buf.append(data)

    def close(self) -> None:  # noqa: D102
        super().close()
        self._close_block()


def parse_body(html: str) -> tuple[dict[str, str], str]:
    """Return ``(sections, full_text)`` for one article body."""
    parser = _Blocks()
    parser.feed(html)
    parser.close()

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for tag, raw in parser.blocks:
        if tag == "h2":
            current = raw.strip().lower()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(raw.strip())

    joined = {k: "\n\n".join(v).strip() for k, v in sections.items()}
    full_text = "\n\n".join(raw for _, raw in parser.blocks).strip()
    return {k: v for k, v in joined.items() if v}, full_text


def fetch(article_id: str, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(API.format(id=article_id), timeout=timeout) as response:
        payload = json.load(response)
    return payload.get("article", payload)


def record(article: dict) -> dict:
    """Shape one article the way the existing GENNX_Feature files are shaped."""
    title = str(article.get("title") or "").strip()
    return {
        "article_id": str(article.get("id") or ""),
        "title": title,
        "feature_name": f"{title} ↗",
        "url": PAGE.format(id=article.get("id")),
        "labels": list(article.get("label_names") or []),
        "sections": {},   # filled by the caller so parse failures are visible
        "full_text": "",
    }


def _flat(text: str) -> str:
    """Whitespace-insensitive view of a block of prose, for drift-tolerant diffing."""
    return re.sub(r"\s+", " ", text).strip()


SIMILARITY_FLOOR = 0.95


def verify(source: Path) -> int:
    """Re-fetch articles we already hold and compare the parse against them.

    Byte equality is NOT the bar and cannot be: the articles have been edited
    upstream since the original scrape. ``Material Properties`` and ``Main
    Control Data`` both had their whole Note heading demoted into Input and
    grew to ~3x the text; several Call paragraphs lost a trailing space.

    The bar that actually matters is **no content loss**: every piece of text
    the old snapshot holds must still be findable in what we parse now. A
    dropped element type or a broken block boundary fails that immediately. An
    editor rewriting or reorganising the article does not, and shows up as
    "grew upstream" instead. Only genuinely missing text is a parser suspect.
    """
    feature_dir = source / "GENNX_Feature"
    checked = suspect = skipped = 0

    for article_id in VERIFY_IDS:
        local_path = feature_dir / f"{article_id}.json"
        if not local_path.exists():
            skipped += 1
            continue
        expected = json.loads(local_path.read_text(encoding="utf-8"))
        try:
            article = fetch(article_id)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  {article_id}  FETCH FAILED  {exc}")
            suspect += 1
            continue
        time.sleep(REQUEST_PAUSE)

        sections, _ = parse_body(article.get("body") or "")
        want_sections = expected.get("sections") or {}
        checked += 1

        # Whole-article haystack: a section that was demoted into another one
        # (Note -> Input) has moved, not vanished.
        haystack = " ".join(_flat(v) for v in sections.values())
        notes: list[str] = []
        lost: list[str] = []

        for key, old in sorted(want_sections.items()):
            needle = _flat(old)
            if not needle or needle in haystack:
                if key not in sections:
                    notes.append(f"[{key}] merged into another section upstream")
                elif _flat(sections[key]) != needle:
                    notes.append(f"[{key}] grew upstream ({len(needle)} -> {len(_flat(sections[key]))} chars)")
                continue
            ratio = SequenceMatcher(None, _flat(sections.get(key, "")), needle).ratio()
            if ratio >= SIMILARITY_FLOOR:
                # Reworded, not lost — a dropped element type never lands here.
                notes.append(f"[{key}] edited upstream ({ratio:.3f} similar)")
            else:
                lost.append(f"[{key}] text not found in the new parse ({ratio:.3f} similar)")

        if set(sections) - set(want_sections):
            notes.append(f"section(s) new upstream: {sorted(set(sections) - set(want_sections))}")

        suspect += bool(lost)
        status = "SUSPECT" if lost else ("ok" if not notes else "ok (drift)")
        print(f"  {article_id}  {status:11} {expected.get('title')}")
        for line in (lost + notes)[:4]:
            print(f"      {line}")

    print(
        f"\nchecked {checked} article(s) against the local snapshot: "
        f"{checked - suspect} with no content lost, {suspect} suspect, "
        f"{skipped} not held locally"
    )
    if suspect:
        print("SUSPECT means the parser probably dropped or mis-split content — investigate before fetching.")
    return 1 if suspect or not checked else 0


def missing_ids(source: Path) -> list[str]:
    """Article ids the index references, that no local file provides."""
    index = json.loads((source / "GENNX_Feature" / "_index.json").read_text(encoding="utf-8"))
    held = {p.stem for p in (source / "GENNX_Feature").glob("*.json") if p.stem != "_index"}

    # Narrow to ids an endpoint actually needs — the index carries 100 orphans,
    # only 40 of which block a mapped endpoint. Fetching the rest is wasted.
    mapping = json.loads((source / "api_to_feature_mapping_v3.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(REPO))
    from midas_mcp import catalog
    known = {str(e.get("uri", "")).lower() for _, _, e in catalog._iter_entries()}

    def key(name: object) -> str:
        return re.sub(r"\s+", " ", str(name or "")).replace("↗", "").strip().lower()

    by_title = {key(k): str(v) for k, v in index.items()}
    wanted: set[str] = set()
    for endpoint, feature in mapping.items():
        if not str(feature).strip() or endpoint.lower() not in known:
            continue
        article_id = by_title.get(key(feature), "")
        if article_id and article_id not in held:
            wanted.add(article_id)
    return sorted(wanted)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path,
                        help="directory holding GENNX_Feature/ and api_to_feature_mapping_v3.json")
    parser.add_argument("--verify", action="store_true",
                        help="re-fetch articles we already have and diff the parse; write nothing")
    parser.add_argument("--dry-run", action="store_true", help="report what would be fetched")
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not (args.source / "GENNX_Feature" / "_index.json").exists():
        sys.exit(f"source is missing GENNX_Feature/_index.json: {args.source}")

    if args.verify:
        return verify(args.source)

    wanted = missing_ids(args.source)
    print(f"{len(wanted)} article(s) referenced by a mapped endpoint but not held locally")
    if args.dry_run:
        for article_id in wanted:
            print(f"  {article_id}")
        return 0

    out_dir = args.source / "GENNX_Feature"
    written = failed = empty = 0
    for article_id in wanted:
        try:
            article = fetch(article_id)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  {article_id}  FAILED  {exc}")
            failed += 1
            continue
        time.sleep(REQUEST_PAUSE)

        entry = record(article)
        entry["sections"], entry["full_text"] = parse_body(article.get("body") or "")
        if not entry["sections"]:
            print(f"  {article_id}  EMPTY PARSE  ({entry['title']!r}) - not written")
            empty += 1
            continue
        (out_dir / f"{article_id}.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written += 1
        print(f"  {article_id}  {entry['title']}  ({', '.join(entry['sections'])})")

    print(f"\nwrote {written}, failed {failed}, empty {empty}")
    return 1 if failed or empty else 0


if __name__ == "__main__":
    sys.exit(main())
