"""MIDAS NX Open API client.

Ported from the template's `src/utils_api.ts` (fetch-based `MidasAPI`).
Same conventions:
  - MAPI-Key header auth
  - DB group uses the `Assign` body key; DOC/POST use `Argument`
  - GET /db/{item} responses are unwrapped from the top-level item key
  - on failure, returns a dict shaped like {"error": "..."} instead of raising
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import requests

DEFAULT_SERVER = "https://moa-engineers.midasit.com:443"


def _decode_program_from_key(key: str) -> str | None:
    """MAPI-Key's first segment is base64(JSON) containing {"pg": "civil"|"gen", ...}."""
    try:
        head = key.split(".", 1)[0]
        head += "=" * (-len(head) % 4)  # pad base64
        payload = json.loads(base64.urlsafe_b64decode(head).decode("utf-8"))
        return payload.get("pg")
    except Exception:
        return None


class MidasClient:
    """Thin REST client for a running MIDAS NX (CIVIL/GEN) instance."""

    def __init__(
        self,
        mapi_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.mapi_key = mapi_key or os.environ.get("MIDAS_MAPI_KEY", "")
        self.timeout = timeout
        self.base_url = (base_url or os.environ.get("MIDAS_BASE_URL") or "").rstrip("/")

        if not self.base_url:
            # Derive {server}/{program} from the key (program is encoded in it).
            program = _decode_program_from_key(self.mapi_key) or "civil"
            self.base_url = f"{DEFAULT_SERVER}/{program}"

    # -- core ---------------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        return {"MAPI-Key": self.mapi_key, "Content-Type": "application/json"}

    def request(self, method: str, endpoint: str, body: Any | None = None) -> Any:
        """Generic request. `endpoint` is the path after the base URL, e.g. `/db/NODE`.

        Returns parsed JSON, or {"error": "..."} on any failure
        (mirrors utils_api.ts ERROR_DICT convention).
        """
        if not self.mapi_key:
            return {"error": "MIDAS_MAPI_KEY is not set."}
        url = f"{self.base_url}{endpoint}"
        try:
            resp = requests.request(
                method.upper(),
                url,
                headers=self._headers,
                data=json.dumps(body) if body is not None else None,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return {"error": f"request failed: {endpoint} ({exc})"}

        if not resp.ok:
            return {"error": f"request failed (status {resp.status_code}) {url}", "body": resp.text[:2000]}
        try:
            return resp.json()
        except ValueError:
            return {"error": f"non-JSON response {url}", "body": resp.text[:2000]}

    # -- DB CRUD (Assign convention) ---------------------------------------

    def db_read(self, item: str) -> Any:
        """GET /db/{item} -> unwrapped {id: value, ...}."""
        js = self.request("GET", f"/db/{item}")
        if isinstance(js, dict) and "error" in js:
            return js
        return (js or {}).get(item, {})

    def db_read_item(self, item: str, key: str | int) -> Any:
        js = self.request("GET", f"/db/{item}/{key}")
        if isinstance(js, dict) and "error" in js:
            return js
        return (js or {}).get(item, {}).get(str(key), {})

    def db_create(self, item: str, assign: dict) -> Any:
        return self.request("POST", f"/db/{item}", {"Assign": assign})

    def db_update(self, item: str, assign: dict) -> Any:
        return self.request("PUT", f"/db/{item}", {"Assign": assign})

    def db_delete(self, item: str, key: str | int) -> Any:
        return self.request("DELETE", f"/db/{item}/{key}")

    # -- DOC / POST (Argument convention) ----------------------------------

    def doc(self, name: str, argument: Any | None = None) -> Any:
        body = {} if argument is None else {"Argument": argument}
        return self.request("POST", f"/doc/{name}", body)

    def post_table(self, argument: dict) -> Any:
        return self.request("POST", "/post/TABLE", {"Argument": argument})
