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

from .hooks import VALIDATED_GROUPS, validate_argument, validate_assign, validate_db_assign

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
        validate: bool | None = None,
    ) -> None:
        self.mapi_key = mapi_key or os.environ.get("MIDAS_MAPI_KEY", "")
        self.timeout = timeout
        self.base_url = (base_url or os.environ.get("MIDAS_BASE_URL") or "").rstrip("/")

        # Client-side schema validation before POST/PUT. Defaults on; disable via
        # MidasClient(validate=False) or env MIDAS_VALIDATE=0/false/off.
        if validate is None:
            validate = os.environ.get("MIDAS_VALIDATE", "1").lower() not in {"0", "false", "off", "no"}
        self.validate = validate

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

    def _validate_assign(self, item: str, assign: Any) -> dict | None:
        """Run the pre-request validation hook, unless validation is disabled.

        Returns an error dict (same shape as request() failures) when the body
        is invalid, or None to proceed.
        """
        if not self.validate:
            return None
        return validate_db_assign(item, assign)

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
        err = self._validate_assign(item, assign)
        if err is not None:
            return err
        return self.request("POST", f"/db/{item}", {"Assign": assign})

    def db_update(self, item: str, assign: dict) -> Any:
        err = self._validate_assign(item, assign)
        if err is not None:
            return err
        return self.request("PUT", f"/db/{item}", {"Assign": assign})

    def db_delete(self, item: str, key: str | int) -> Any:
        return self.request("DELETE", f"/db/{item}/{key}")

    # -- Command groups (Argument convention: doc/ope/view/post) -----------

    def command(
        self,
        group: str,
        name: str,
        argument: Any | None = None,
        method: str = "POST",
        body: Any | None = None,
    ) -> Any:
        """Call an Argument-convention endpoint: {method} /{group}/{name}.

        POST/PUT wrap the payload as {"Argument": argument} (empty body when
        argument is None); GET takes no body. Used by the doc/ope/view/post
        tools, which all share this one request shape.

        `body` overrides the wrapping and is sent verbatim — for the handful of
        endpoints whose body is a named key rather than "Argument"
        (e.g. /ope/STOR -> {"STOR": {...}}).
        """
        method = method.upper()
        if method == "GET":
            return self.request("GET", f"/{group}/{name}")
        if body is None:
            body = {} if argument is None else {"Argument": argument}
        err = self._validate_command_body(group, name, method, body)
        if err is not None:
            return err
        return self.request(method, f"/{group}/{name}", body)

    def _validate_command_body(self, group: str, name: str, method: str, body: Any) -> dict | None:
        """Validate a command body before send, per convention (e.g. /temp/SVSL, /ope/AUTOMESH).

        POST/PUT only, and only for validated groups other than db (db is validated
        in db_create/db_update). Dispatches on the body shape:
          - {"Assign": {...}}  -> per-id value validation
          - {"Argument": ...} or {} -> whole-body validation (an omitted Argument
            is still checked, so an endpoint that requires one is caught locally)
        Verbatim named-key bodies (e.g. {"STOR": {...}}) and unverified groups pass
        through untouched.
        """
        if not self.validate or method not in ("POST", "PUT"):
            return None
        g = group.lower()
        if g == "db" or g not in VALIDATED_GROUPS:
            return None
        if not isinstance(body, dict):
            return None
        if isinstance(body.get("Assign"), dict):
            return validate_assign(g, name, body["Assign"])
        if "Argument" in body or not body:
            return validate_argument(g, name, body)
        return None
