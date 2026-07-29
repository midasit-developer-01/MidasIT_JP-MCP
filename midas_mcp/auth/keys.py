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


def looks_like_mapi_key(key: str) -> bool:
    """Cheap well-formedness check.

    The key's first segment is base64(JSON) carrying ``pg`` (civil|gen) - the
    same shape ``client._decode_program_from_key`` relies on to pick a base URL.
    """
    try:
        head = key.split(".", 1)[0]
        head += "=" * (-len(head) % 4)  # pad base64
        payload = json.loads(base64.urlsafe_b64decode(head).decode("utf-8"))
        return isinstance(payload, dict) and "pg" in payload
    except Exception:
        return False
