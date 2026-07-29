"""Exercise the whole OAuth flow against a running server.

Not a unit test - it drives the real HTTP endpoints exactly as an MCP client
does, which is the only way to catch the parts the SDK owns (PKCE verification,
metadata shape, bearer middleware) alongside the parts this package owns.

Run it against a server started with auth on::

    MIDAS_MCP_PUBLIC_URL=http://127.0.0.1:18081 \
    MIDAS_AUTH_DB=./_t/auth.db MIDAS_MCP_TRANSPORT=streamable-http \
    MIDAS_MCP_PORT=18081 MIDAS_MCP_HOST=127.0.0.1 python -m midas_mcp.server &

    python -m midas_mcp.auth.check_flow http://127.0.0.1:18081

Exits non-zero on the first failure, so it works as a deploy gate.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import urllib.parse

import requests

REDIRECT = "http://localhost:9999/cb"

# Syntactically valid MAPI key: first segment is base64(JSON carrying "pg").
_head = base64.urlsafe_b64encode(json.dumps({"pg": "civil"}).encode()).decode().rstrip("=")
SAMPLE_KEY = f"{_head}.signaturepart"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "check_flow", "version": "1"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


class Checker:
    def __init__(self) -> None:
        # Track how many assertions have failed so far.
        self.failed = 0

    def __call__(self, label: str, cond: bool, extra: str = "") -> bool:
        # Print PASS/FAIL for one assertion and tally failures.
        print(("PASS " if cond else "FAIL ") + label + (f"  {extra}" if extra else ""))
        if not cond:
            self.failed += 1
        return cond


def run(base: str) -> int:
    # Drive the full OAuth + MCP flow end to end; return the failure count.
    base = base.rstrip("/")
    s = requests.Session()
    check = Checker()

    # --- the server must refuse anonymous MCP traffic ---
    r = s.post(f"{base}/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    check("anonymous /mcp is 401", r.status_code == 401, str(r.status_code))
    check("401 carries WWW-Authenticate", "www-authenticate" in {k.lower() for k in r.headers})
    check("/healthz stays public", s.get(f"{base}/healthz").text == "ok")

    # --- discovery: what Claude reads first ---
    check("protected-resource metadata", s.get(f"{base}/.well-known/oauth-protected-resource").ok)
    r = s.get(f"{base}/.well-known/oauth-authorization-server")
    if not check("authorization-server metadata", r.ok):
        return check.failed
    meta = r.json()

    # --- clients register themselves ---
    r = s.post(meta["registration_endpoint"], json={
        "client_name": "check_flow", "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "client_secret_post",
    })
    if not check("dynamic client registration", r.status_code in (200, 201), r.text[:120]):
        return check.failed
    client = r.json()
    creds = {"client_id": client["client_id"], "client_secret": client.get("client_secret", "")}

    # --- /authorize hands off to our consent form ---
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    r = s.get(meta["authorization_endpoint"], params={
        "response_type": "code", "client_id": client["client_id"], "redirect_uri": REDIRECT,
        "state": "xyz", "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": f"{base}/mcp",
    }, allow_redirects=False)
    loc = r.headers.get("location", "")
    if not check("/authorize redirects to the login form", "/login?rid=" in loc, loc[:80]):
        return check.failed
    rid = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["rid"][0]

    r = s.get(f"{base}/login", params={"rid": rid})
    check("login form renders", r.ok and "MAPI key" in r.text)
    r = s.post(f"{base}/login", params={"rid": rid}, data={"mapi_key": "nope"}, allow_redirects=False)
    check("malformed key rejected", r.status_code == 400)

    # --- a real key produces an authorization code ---
    r = s.post(f"{base}/login", params={"rid": rid}, data={"mapi_key": SAMPLE_KEY}, allow_redirects=False)
    loc = r.headers.get("location", "")
    if not check("key accepted, redirected to client", loc.startswith(REDIRECT), loc[:80]):
        return check.failed
    q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    check("state round-trips", q.get("state", [""])[0] == "xyz")
    code = q["code"][0]

    # --- PKCE actually protects the code ---
    r = s.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "code_verifier": secrets.token_urlsafe(48), **creds,
    })
    check("wrong PKCE verifier rejected", r.status_code == 400, r.text[:100])

    r = s.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "code_verifier": verifier, **creds,
    })
    if not check("token exchange", r.ok, r.text[:120]):
        return check.failed
    tok = r.json()

    r = s.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "code_verifier": verifier, **creds,
    })
    check("authorization code is single use", r.status_code == 400)

    # --- the token actually works on the MCP endpoint ---
    auth_headers = {**MCP_HEADERS, "Authorization": f"Bearer {tok['access_token']}"}
    r = s.post(f"{base}/mcp", json=INITIALIZE, headers=auth_headers)
    check("authorized initialize", r.ok and "serverInfo" in r.text, str(r.status_code))
    r = s.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
               headers=auth_headers)
    check("authorized tools/list", r.ok and "midas_lookup" in r.text, str(r.status_code))

    # --- refresh rotates without losing the key binding ---
    r = s.post(meta["token_endpoint"], data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], **creds,
    })
    if check("refresh grant", r.ok, r.text[:120]):
        new = r.json()
        check("refresh token rotated", new.get("refresh_token") != tok["refresh_token"])
        r = s.post(f"{base}/mcp", json=INITIALIZE,
                   headers={**MCP_HEADERS, "Authorization": f"Bearer {new['access_token']}"})
        check("refreshed token still works", r.ok, str(r.status_code))

    return check.failed


def main() -> None:
    # CLI entry: run the flow against the given base URL and exit non-zero on any failure.
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18081"
    failed = run(base)
    print("\n" + ("ALL PASS" if failed == 0 else f">>> {failed} FAILED <<<"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
