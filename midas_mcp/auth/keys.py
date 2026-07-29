"""What counts as an identity here: the MAPI key itself.

MIDAS has no user directory, so there is nothing to authenticate against beyond
"this key is well-formed". A key that parses but is expired fails later at the
MIDAS API, which is a better place to surface it than a login form.
"""

from __future__ import annotations

import base64
import hashlib
import json


def fingerprint(mapi_key: str) -> str:
    """Stable, non-reversible id for a key - used as the OAuth ``sub``.

    Lets token records and logs identify a user without storing the key twice.
    """
    return hashlib.sha256(mapi_key.encode()).hexdigest()[:16]


def _head_payload(key: str) -> dict | None:
    # Decode the key's first segment (base64 JSON) or None if it doesn't parse.
    try:
        head = key.split(".", 1)[0]
        head += "=" * (-len(head) % 4)  # pad base64
        payload = json.loads(base64.urlsafe_b64decode(head).decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def looks_like_mapi_key(key: str) -> bool:
    """Cheap well-formedness check.

    The key's first segment is base64(JSON) carrying ``pg`` (civil|gen) - the
    same shape ``client._decode_program_from_key`` relies on to pick a base URL.
    """
    payload = _head_payload(key)
    return payload is not None and "pg" in payload


def program_of(key: str) -> str | None:
    """The product a key belongs to - ``"civil"`` or ``"gen"`` - read from its head.

    The base URL is derived from this (see ``client._decode_program_from_key``),
    so it is also what a user really switches when they swap in a different key:
    a CIVIL key and a GEN key are different keys, and re-keying to the other one
    moves the whole session to that program automatically.
    """
    payload = _head_payload(key)
    pg = payload.get("pg") if payload else None
    return pg if isinstance(pg, str) else None
