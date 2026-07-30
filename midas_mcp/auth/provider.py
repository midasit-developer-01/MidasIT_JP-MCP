"""OAuth 2.1 authorization server whose consent step collects a MAPI key.

The MCP SDK serves the protocol itself - metadata documents, dynamic client
registration, /authorize, /token, PKCE verification, bearer middleware. What is
implemented here is only the storage and what "logging in" means.
"""

from __future__ import annotations

import json
import secrets
import time
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .keys import fingerprint, looks_like_mapi_key, program_of
from .pages import LOGIN_PATH, REKEY_PATH
from .store import Store

AUTH_CODE_TTL = 300  # the client redeems it immediately
# Access tokens are deliberately short: a session's real length is the refresh
# (idle) window below. An active client rotates its refresh token about once a
# day when the access token lapses.
ACCESS_TOKEN_TTL = 60 * 60 * 24 * 1
# Refresh tokens carry an idle window that each rotation slides forward - this IS
# the session / re-login period. Kept larger than ACCESS_TOKEN_TTL so an active
# client (which only presents its refresh token when the access token lapses)
# never has it expire underneath them. After this much inactivity the user must
# re-authenticate and the stored MAPI key is reclaimed by _sweep().
REFRESH_TOKEN_TTL = 60 * 60 * 24 * 7
PENDING_TTL = 600  # how long the user has to finish the consent form
REKEY_TTL = 600  # how long a handed-out re-key link stays valid
SWEEP_INTERVAL = 60 * 60  # reclaim expired rows at most this often (amortized on writes)


def _now() -> int:
    # Current unix time as an int - the unit every TTL/expiry column uses.
    return int(time.time())


def _secret() -> str:
    """256 bits. The spec floor for authorization codes is 128."""
    return secrets.token_urlsafe(32)


""" MidasKeyProvider 과정
authorize          요청 보관 → 우리 /login 폼으로 보냄
   │
complete_login     폼에서 키 받음 → 검증 → 인가 코드 발급 → 클라이언트로
   │
exchange_authorization_code   코드 → 토큰 (코드 즉시 소멸)
   │
load_access_token / mapi_key_for_token   매 요청: 토큰 → 키
   │
start_rekey / complete_rekey   재연결 없이 저장된 키만 교체 (프로그램은 새 키를 따라감)
"""
class MidasKeyProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, public_url: str, db_path: str | None = None) -> None:
        # Remember our own external origin and open the token store.
        self.public_url = public_url.rstrip("/")
        self.store = Store(db_path)
        # Housekeeping: migrate any legacy immortal refresh tokens (minted with a
        # NULL expiry before idle-expiry existed) onto an idle window starting now,
        # then run one sweep at boot. With the weekday stop/start schedule this
        # boot sweep alone runs ~daily, but _maybe_sweep() also fires it on writes
        # so GC does not depend on the instance ever restarting.
        self._last_sweep = 0
        self.store.run(
            "UPDATE tokens SET expires_at=? WHERE kind='refresh' AND expires_at IS NULL",
            (_now() + REFRESH_TOKEN_TTL,),
        )
        self._sweep()

    # -- housekeeping (GC) ---------------------------------------------------

    def _sweep(self) -> None:
        """Reclaim rows whose lifetime is over.

        Purely housekeeping: an expired token is still *rejected* lazily on the hot
        path (load_access_token / load_refresh_token / mapi_key_for_token), so this
        only deletes rows that are never presented again - abandoned sessions and
        the plaintext MAPI keys they hold. Access tokens carry an absolute expiry;
        refresh tokens an idle window slid forward by rotation. So a stale refresh
        token (and its key) is not retained past REFRESH_TOKEN_TTL of inactivity.
        """
        now = _now()
        self._last_sweep = now
        self.store.run("DELETE FROM tokens  WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
        self.store.run("DELETE FROM codes   WHERE expires_at < ?", (now,))
        self.store.run("DELETE FROM pending WHERE created_at < ?", (now - PENDING_TTL,))
        self.store.run("DELETE FROM rekey   WHERE created_at < ?", (now - REKEY_TTL,))

    def _maybe_sweep(self) -> None:
        # Amortized trigger: piggyback the sweep on token issuance, but at most once
        # per SWEEP_INTERVAL so a burst of logins does not re-scan repeatedly. A
        # double-run under concurrency is harmless - the DELETEs are idempotent.
        if _now() - self._last_sweep >= SWEEP_INTERVAL:
            self._sweep()

    # -- clients (dynamic registration) --------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        # Look up a previously registered client by id (SDK calls this a lot).
        row = self.store.one("SELECT info FROM clients WHERE client_id=?", (client_id,))
        return OAuthClientInformationFull.model_validate_json(row["info"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Persist a self-registering client (Dynamic Client Registration).
        self.store.run(
            "INSERT OR REPLACE INTO clients (client_id, info) VALUES (?, ?)",
            (client_info.client_id, client_info.model_dump_json()),
        )

    # -- authorization -------------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Park the request and send the user to our own consent form.

        The form - not this method - mints the authorization code, because only
        then do we know which MAPI key is being authorized.
        """
        rid = _secret()
        self.store.run("DELETE FROM pending WHERE created_at < ?", (_now() - PENDING_TTL,))
        self.store.run(
            "INSERT INTO pending (rid, params, client_id, created_at) VALUES (?,?,?,?)",
            (rid, params.model_dump_json(), client.client_id, _now()),
        )
        return f"{self.public_url}{LOGIN_PATH}?{urlencode({'rid': rid})}"

    def complete_login(self, rid: str, mapi_key: str) -> str:
        """Called by the consent form. Returns the URL to send the browser to.

        Raises AuthorizeError so the HTTP layer can re-render the form with a
        readable message.
        """
        row = self.store.one("SELECT params, client_id, created_at FROM pending WHERE rid=?", (rid,))
        if row is None:
            raise AuthorizeError("invalid_request", "This login link is unknown - restart from your MCP client.")
        if row["created_at"] < _now() - PENDING_TTL:
            self.store.run("DELETE FROM pending WHERE rid=?", (rid,))
            raise AuthorizeError("invalid_request", "This login link expired - restart from your MCP client.")
        if not looks_like_mapi_key(mapi_key):
            raise AuthorizeError("access_denied", "That does not look like a MAPI key.")

        params = AuthorizationParams.model_validate_json(row["params"])
        code = _secret()
        payload = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=_now() + AUTH_CODE_TTL,
            client_id=row["client_id"],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=fingerprint(mapi_key),
        )
        self.store.run(
            "INSERT INTO codes (code, payload, mapi_key, expires_at) VALUES (?,?,?,?)",
            (code, payload.model_dump_json(), mapi_key, payload.expires_at),
        )
        self.store.run("DELETE FROM pending WHERE rid=?", (rid,))
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        # Fetch a still-valid code that belongs to this client (else None).
        row = self.store.one("SELECT payload, expires_at FROM codes WHERE code=?", (authorization_code,))
        if row is None or row["expires_at"] < _now():
            return None
        code = AuthorizationCode.model_validate_json(row["payload"])
        return code if code.client_id == client.client_id else None

    # -- tokens --------------------------------------------------------------

    def _issue(self, *, client_id: str, scopes: list[str], subject: str,
               resource: str | None, mapi_key: str) -> OAuthToken:
        # Mint an access+refresh token pair bound to the given MAPI key and store both.
        access, refresh = _secret(), _secret()
        now = _now()
        insert = (
            "INSERT INTO tokens (token, kind, client_id, scopes, subject, resource, mapi_key, expires_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
        )
        self.store.run(insert, (access, "access", client_id, json.dumps(scopes), subject,
                                resource, mapi_key, now + ACCESS_TOKEN_TTL))
        # Refresh tokens carry an idle window rather than living forever. Each use
        # rotates (re-issues) a fresh one, so an active client slides its expiry
        # forward while an abandoned one - and the MAPI key it holds - lapses and
        # is reclaimed by _sweep(). Revoking still takes out the pair immediately.
        self.store.run(insert, (refresh, "refresh", client_id, json.dumps(scopes), subject,
                                resource, mapi_key, now + REFRESH_TOKEN_TTL))
        self._maybe_sweep()
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Trade a one-time code for tokens, consuming the code so it can't be reused.
        row = self.store.one("SELECT mapi_key FROM codes WHERE code=?", (authorization_code.code,))
        if row is None:
            raise TokenError("invalid_grant", "authorization code already used or unknown")
        # Single use, whatever happens next.
        self.store.run("DELETE FROM codes WHERE code=?", (authorization_code.code,))
        return self._issue(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            subject=authorization_code.subject or "",
            resource=authorization_code.resource,
            mapi_key=row["mapi_key"],
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        # Look up a refresh token and confirm it belongs to this client (else None).
        row = self.store.one("SELECT * FROM tokens WHERE token=? AND kind='refresh'", (refresh_token,))
        if row is None or row["client_id"] != client.client_id:
            return None
        # Idle expiry: reject and drop a refresh token past its window (mirrors
        # load_access_token). An active client keeps it alive by rotating it.
        if row["expires_at"] is not None and row["expires_at"] < _now():
            self.store.run("DELETE FROM tokens WHERE token=?", (refresh_token,))
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes"]),
            expires_at=row["expires_at"],
            subject=row["subject"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        # Issue a fresh token pair and rotate: the presented refresh token is destroyed.
        row = self.store.one("SELECT mapi_key, resource FROM tokens WHERE token=?", (refresh_token.token,))
        if row is None:
            raise TokenError("invalid_grant", "unknown refresh token")
        # Rotate: the presented refresh token dies with this exchange.
        self.store.run("DELETE FROM tokens WHERE token=?", (refresh_token.token,))
        return self._issue(
            client_id=client.client_id,
            scopes=scopes or refresh_token.scopes,
            subject=refresh_token.subject or "",
            resource=row["resource"],
            mapi_key=row["mapi_key"],
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        # Resolve a bearer access token, dropping and rejecting it if expired.
        row = self.store.one("SELECT * FROM tokens WHERE token=? AND kind='access'", (token,))
        if row is None:
            return None
        if row["expires_at"] is not None and row["expires_at"] < _now():
            self.store.run("DELETE FROM tokens WHERE token=?", (token,))
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes"]),
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Revoke: delete the whole access+refresh pair for this user+client.
        row = self.store.one("SELECT subject, client_id FROM tokens WHERE token=?", (token.token,))
        if row is None:
            return
        # The spec says revoking either half should take out the pair.
        self.store.run(
            "DELETE FROM tokens WHERE subject=? AND client_id=?",
            (row["subject"], row["client_id"]),
        )

    # -- used by the MCP tools ----------------------------------------------

    def mapi_key_for_token(self, token: str) -> str | None:
        # Resolve a live access token back to the MAPI key it authorized (tools use this).
        row = self.store.one(
            "SELECT mapi_key, expires_at FROM tokens WHERE token=? AND kind='access'", (token,)
        )
        if row is None or (row["expires_at"] is not None and row["expires_at"] < _now()):
            return None
        return row["mapi_key"]

    # -- re-keying (swap the stored key without a reconnect) -----------------

    def start_rekey(self, token: str) -> str:
        """Mint a one-time re-key link for the holder of a live access token.

        The MCP request that calls this is already bearer-authenticated, so the
        token identifies the user. We stash (subject, client_id) behind a short
        ``rid`` and hand back a URL - possession of that rid is the capability
        to replace this user's key, so it lives only minutes. The key itself is
        never taken here; it is pasted later into the browser form.
        """
        row = self.store.one(
            "SELECT subject, client_id, expires_at FROM tokens WHERE token=? AND kind='access'",
            (token,),
        )
        if row is None or (row["expires_at"] is not None and row["expires_at"] < _now()):
            raise AuthorizeError("access_denied", "not authenticated")
        rid = _secret()
        self.store.run("DELETE FROM rekey WHERE created_at < ?", (_now() - REKEY_TTL,))
        self.store.run(
            "INSERT INTO rekey (rid, subject, client_id, created_at) VALUES (?,?,?,?)",
            (rid, row["subject"], row["client_id"], _now()),
        )
        return f"{self.public_url}{REKEY_PATH}?{urlencode({'rid': rid})}"

    def complete_rekey(self, rid: str, new_key: str) -> str:
        """Consume a re-key rid and swap the stored key for this user+client.

        Returns the program (civil|gen) the new key belongs to, so the caller
        can confirm the switch. The tokens the client already holds keep
        working - only the key behind them (and its fingerprint) changes, which
        is why no reconnect is needed. The subject moves to the new key's
        fingerprint so grouping (revoke) stays consistent.
        """
        row = self.store.one(
            "SELECT subject, client_id, created_at FROM rekey WHERE rid=?", (rid,)
        )
        if row is None:
            raise AuthorizeError("invalid_request", "This re-key link is unknown - start again from your MCP client.")
        if row["created_at"] < _now() - REKEY_TTL:
            self.store.run("DELETE FROM rekey WHERE rid=?", (rid,))
            raise AuthorizeError("invalid_request", "This re-key link expired - start again from your MCP client.")
        if not looks_like_mapi_key(new_key):
            raise AuthorizeError("access_denied", "That does not look like a MAPI key.")
        self.store.run(
            "UPDATE tokens SET mapi_key=?, subject=? WHERE subject=? AND client_id=?",
            (new_key, fingerprint(new_key), row["subject"], row["client_id"]),
        )
        self.store.run("DELETE FROM rekey WHERE rid=?", (rid,))
        return program_of(new_key) or "civil"
