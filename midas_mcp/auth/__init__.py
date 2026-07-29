"""OAuth for remote (streamable-http) deployments.

Why this exists
---------------
Remote mode needs each caller's own MAPI key, but MCP clients such as Claude's
custom connectors cannot attach an arbitrary header - the only credential they
carry is an OAuth bearer token. So the server becomes its own authorization
server: "logging in" is a one-field form where the user pastes their MAPI key,
and the token issued afterwards is what the client sends from then on. The key
never leaves this server; the client only ever holds a revocable token.

Each module owns one role:
  - ``store``    : SQLite persistence (clients, codes, tokens)
  - ``keys``     : what an identity is here - the MAPI key and its fingerprint
  - ``pages``    : the consent screen
  - ``provider`` : the OAuth 2.1 authorization server itself
  - this module  : env-driven setup, wiring into FastMCP

Everything is opt-in. With ``MIDAS_MCP_PUBLIC_URL`` unset the server is built
exactly as before, so stdio (.mcpb) and header-only http deployments are
untouched. Setting that one variable is what turns OAuth on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

__all__ = ["AuthSetup", "from_env"]


@dataclass
class AuthSetup:
    """Everything the server needs to turn auth on.

    ``fastmcp_kwargs`` has to be passed to FastMCP's constructor - that is where
    the SDK wires the metadata/registration/token routes and the bearer
    middleware, so the decision cannot be deferred.
    """

    provider: Any
    fastmcp_kwargs: dict[str, Any]

    def install_routes(self, app: Any) -> None:
        """Add the consent screen and a public health check.

        Both go through ``custom_route``, which is exempt from the bearer
        middleware - the login page has to be, since it is where the credential
        is obtained in the first place.
        """
        from mcp.server.auth.provider import AuthorizeError
        from starlette.requests import Request
        from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse

        from .pages import LOGIN_PATH, REKEY_PATH, render_login, render_rekey, render_rekey_done

        provider = self.provider

        @app.custom_route(LOGIN_PATH, methods=["GET"])
        async def login_form(request: Request) -> HTMLResponse:
            # Show the empty consent form, carrying the request id (rid) through.
            return HTMLResponse(render_login(request.query_params.get("rid", "")))

        @app.custom_route(LOGIN_PATH, methods=["POST"])
        async def login_submit(request: Request) -> HTMLResponse | RedirectResponse:
            # Take the pasted key; on success redirect back to the client, else re-render with the error.
            form = await request.form()
            rid = request.query_params.get("rid", "")
            try:
                target = provider.complete_login(rid, str(form.get("mapi_key", "")).strip())
            except AuthorizeError as exc:
                return HTMLResponse(render_login(rid, exc.error_description or exc.error), status_code=400)
            # 302 so the browser switches to GET when it lands back on the client.
            return RedirectResponse(target, status_code=302)

        @app.custom_route(REKEY_PATH, methods=["GET"])
        async def rekey_form(request: Request) -> HTMLResponse:
            # Show the re-key form (same shape as login), carrying the rid through.
            return HTMLResponse(render_rekey(request.query_params.get("rid", "")))

        @app.custom_route(REKEY_PATH, methods=["POST"])
        async def rekey_submit(request: Request) -> HTMLResponse:
            # Swap the stored key; on success confirm which program is now active.
            form = await request.form()
            rid = request.query_params.get("rid", "")
            try:
                program = provider.complete_rekey(rid, str(form.get("mapi_key", "")).strip())
            except AuthorizeError as exc:
                return HTMLResponse(render_rekey(rid, exc.error_description or exc.error), status_code=400)
            return HTMLResponse(render_rekey_done(program))

        @app.custom_route("/healthz", methods=["GET"])
        async def healthz(request: Request) -> PlainTextResponse:  # noqa: ARG001
            # Unauthenticated liveness probe - the one way to check the server without a token.
            return PlainTextResponse("ok")

    def mapi_key_for_token(self, token: str) -> str | None:
        # Delegate to the provider: resolve a bearer token back to its MAPI key.
        return self.provider.mapi_key_for_token(token)

    def start_rekey(self, token: str) -> str:
        # Delegate: mint a one-time re-key link for this bearer token's owner.
        return self.provider.start_rekey(token)


def from_env() -> AuthSetup | None:
    """Build the auth setup from the environment, or None when auth is off.

    OAuth turns on precisely when ``MIDAS_MCP_PUBLIC_URL`` is set - the
    externally reachable https origin. That one value is both the switch and the
    address: OAuth needs it for the issuer and redirect URLs, and a server
    behind a reverse proxy cannot infer it. With it unset the server is built
    exactly as before, so stdio (.mcpb) and header-only http are untouched.
    """
    public_url = os.environ.get("MIDAS_MCP_PUBLIC_URL", "").rstrip("/")
    if not public_url:
        return None

    from urllib.parse import urlparse

    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
    from mcp.server.transport_security import TransportSecuritySettings

    from .provider import MidasKeyProvider

    provider = MidasKeyProvider(public_url)
    host = urlparse(public_url).netloc
    return AuthSetup(
        provider=provider,
        fastmcp_kwargs={
            "auth_server_provider": provider,
            "auth": AuthSettings(
                issuer_url=public_url,  # this server is its own authorization server
                resource_server_url=public_url,
                # Clients register themselves on first connect; without this
                # every client would need credentials issued by hand.
                client_registration_options=ClientRegistrationOptions(enabled=True),
                revocation_options=RevocationOptions(enabled=True),
            ),
            # Behind a proxy the Host is our public domain; allow it so the SDK's
            # localhost-only rebinding check doesn't 421 every request.
            "transport_security": TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[host, f"{host}:*"],
                allowed_origins=[public_url, f"{public_url}:*"],
            ),
        },
    )
