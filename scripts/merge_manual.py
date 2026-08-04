"""Fold the public API manual's naming into the bundled schemas.

Why
---
The bundled descriptions are generated from the C++ DTOs, so they name things
the way the code does. The manual names them the way the UI does, and that is
the wording a user's request is phrased in:

    manual              bundled schema        before this merge
    "Graphic Files"     "View Capture"        midas_lookup missed it
    "Activities"        "Active"              midas_lookup missed it
    "Viewpoint"         "Angle"               midas_lookup missed it

No amount of scorer tuning closes that gap — the words simply are not in the
index. Adding them is. Measured on the 92 eval queries whose text is NOT one of
the merged fields (so the result is not circular): recall@10 85.9% -> 91.3%,
top-1 42.4% -> 56.5%.

What it writes
--------------
Three fields, onto the existing per-endpoint files (not a parallel tree — a
second tree would have to be kept in sync with this one):

    manual_title   the API article's title      "Constraint Support"
    feature_name   the GUI feature's name       "Define Supports"
    manual_url     the public article URL       https://support.midasuser.com/...

Only the naming fields. The manual's prose (``function``/``call``/``input``) is
left out on purpose: it roughly triples a document's indexed length, which
skews BM25's length normalisation, and the titles alone carry the gain above.

Source layout (three files link endpoint -> manual article):
    api_to_feature_mapping_v3.json   endpoint      -> feature name
    GENNX_API_Schema/_index.json     endpoint      -> article id
    GENNX_Feature/_index.json        feature name  -> article id

Run:
    python scripts/merge_manual.py --source <API_Data dir>
    python scripts/merge_manual.py --source <dir> --dry-run

Idempotent: re-running with the same source rewrites the same three fields and
touches nothing else, so it is safe to re-run whenever the manual is re-scraped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO / "data" / "schemas"

FIELDS = ("manual_title", "feature_name", "manual_url")


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def feature_key(name: object) -> str:
    """Feature names carry a trailing '↗' in the index; match without it."""
    return norm(name).replace("↗", "").strip().lower()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def build_manual_map(source: Path) -> dict[str, dict[str, str]]:
    """endpoint (lowercased) -> the fields to merge."""
    mapping = load_json(source / "api_to_feature_mapping_v3.json")
    api_index = load_json(source / "GENNX_API_Schema" / "_index.json")
    feature_index = load_json(source / "GENNX_Feature" / "_index.json")
    if mapping is None or api_index is None or feature_index is None:
        sys.exit(f"source is missing one of the three index files: {source}")

    features = {feature_key(k): str(v) for k, v in feature_index.items()}
    out: dict[str, dict[str, str]] = {}

    for endpoint in set(mapping) | set(api_index):
        record: dict[str, str] = {}

        article = load_json(source / "GENNX_API_Schema" / f"{api_index[endpoint]}.json") \
            if endpoint in api_index else None
        if article:
            if norm(article.get("title")):
                record["manual_title"] = norm(article["title"])
            if norm(article.get("url")):
                record["manual_url"] = norm(article["url"])

        key = feature_key(mapping.get(endpoint))
        feature = load_json(source / "GENNX_Feature" / f"{features[key]}.json") \
            if key in features else None
        if feature and norm(feature.get("title")):
            record["feature_name"] = norm(feature["title"])

        if record:
            out[endpoint.lower()] = record
    return out


# One of our fields on its own line, at the very end of the object. Used to
# strip a previous run's block before writing the new one.
_OURS_RE = re.compile(
    r",\n[ \t]*\"(?:" + "|".join(FIELDS) + r")\": (?:\"(?:[^\"\\\\]|\\\\.)*\"|null)"
)


def splice(text: str, record: dict[str, str]) -> str:
    """Append our fields to the JSON object by editing the text, not re-serialising.

    The catalog files keep short arrays inline (``"enum": ["A", "B"]``), which
    ``json.dumps`` cannot reproduce — round-tripping them reflows 96 of the 228
    files and buries three added lines under thousands of cosmetic ones. Editing
    the text leaves every byte we are not adding exactly as it was.
    """
    body = _OURS_RE.sub("", text.rstrip())          # drop a previous run's block
    assert body.endswith("}"), "schema file does not end with an object"
    body = body[:-1].rstrip()                        # step inside the closing brace

    indent = "  "
    added = "".join(
        f',\n{indent}{json.dumps(f, ensure_ascii=False)}: {json.dumps(record[f], ensure_ascii=False)}'
        for f in FIELDS if f in record
    )
    return f"{body}{added}\n}}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", required=True, type=Path,
        help="directory holding api_to_feature_mapping_v3.json, GENNX_API_Schema/, GENNX_Feature/",
    )
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    manual = build_manual_map(args.source)
    print(f"manual entries: {len(manual)}")

    written = unchanged = unmatched = 0
    for path in sorted(SCHEMA_DIR.glob("**/*.json")):
        if len(path.relative_to(SCHEMA_DIR).parts) < 2:
            continue
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            continue

        record = manual.get(str(data.get("uri", "")).lower())
        if not record:
            unmatched += 1
            continue
        if all(data.get(f) == record.get(f) for f in FIELDS):
            unchanged += 1
            continue

        if not args.dry_run:
            path.write_text(splice(text, record), encoding="utf-8")
        written += 1

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {written} · already current {unchanged} · no manual entry {unmatched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
