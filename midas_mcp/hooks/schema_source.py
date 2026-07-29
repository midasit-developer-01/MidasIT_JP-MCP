"""Hook role: locate the bundled JSON Schema for an Assign-convention endpoint.

The per-endpoint file stores ``schema`` as ``{NAME: {...draft-07...}}``. We return
the inner schema that validates a single Assign *value* object (e.g. ``{"ITEMS": [...]}``
for NSPR), or None when no usable schema is bundled.

Resolution is by FULL URI (``<group>/<path>``), not bare name, because the leaf
name repeats across code standards in design/rating (e.g. ``DCRM`` lives under
``design/RC/KDS-41-20-2022`` and other standards). The caller passes a
group-relative path (``RC/KDS-41-20-2022/DCRM``) or a bare item (``NODE``); either
is turned into a full URI and matched exactly.
"""

from __future__ import annotations


def load_item_schema(item: str, group: str | None = "db") -> dict | None:
    """Return the draft-07 schema for one endpoint, or None.

    ``item`` may be a bare name (``"NSPR"``), a group-relative path
    (``"RC/KDS-41-20-2022/DCRM"``), or a full URI (``"design/RC/.../DCRM"``).
    ``group`` (default ``"db"``) is prefixed when ``item`` is not already a full
    URI, so resolution lands on the exact code-standard entry rather than a
    same-named endpoint in another group or standard.
    """
    try:
        from .. import catalog
    except Exception:
        return None
    grp = (group or "db")
    it = str(item)
    # Build the full URI so describe() matches the exact endpoint (its uri-branch
    # is unambiguous); bare names would fall back to a first-match by leaf name.
    uri = it if it.lower().startswith(grp.lower() + "/") else f"{grp}/{it}"
    entry = catalog.describe(uri, group=grp)
    schema = entry.get("schema") if isinstance(entry, dict) else None
    if not isinstance(schema, dict) or not schema:
        return None
    leaf = it.split("/")[-1]
    for key, sub in schema.items():  # case-insensitive leaf-name match
        if key.lower() == leaf.lower() and isinstance(sub, dict):
            return sub
    if len(schema) == 1:  # single-schema file: use it regardless of key
        only = next(iter(schema.values()))
        return only if isinstance(only, dict) else None
    return None
