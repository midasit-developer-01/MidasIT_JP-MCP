"""The consent screen - the one page a user of this server ever sees.

Deliberately dependency-free: no CSS framework, no JS, no external fetches, so
it renders identically wherever the browser opens it.
"""

from __future__ import annotations

from urllib.parse import urlencode

LOGIN_PATH = "/login"

_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect MIDAS NX</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: ui-sans-serif, system-ui, sans-serif; max-width: 30rem;
         margin: 4rem auto; padding: 0 1.25rem; line-height: 1.55; }}
  h1 {{ font-size: 1.35rem; margin-bottom: .35rem; }}
  p  {{ color: #888; font-size: .92rem; margin-top: 0; }}
  input {{ width: 100%; padding: .6rem .7rem; font-family: ui-monospace, monospace;
           font-size: .9rem; border: 1px solid #8884; border-radius: .4rem;
           background: transparent; color: inherit; }}
  button {{ margin-top: .9rem; padding: .6rem 1.1rem; border: 0; border-radius: .4rem;
            background: #2563eb; color: #fff; font-size: .95rem; cursor: pointer; }}
  .err {{ background: #ef44441a; border: 1px solid #ef444455; border-radius: .4rem;
          padding: .6rem .7rem; font-size: .9rem; margin-bottom: 1rem; }}
  footer {{ margin-top: 2rem; font-size: .82rem; color: #888; }}
</style>
<h1>Connect MIDAS NX</h1>
<p>Paste the MAPI key from <b>[API Settings]</b> in MIDAS CIVIL/GEN NX.</p>
{error}
<form method="post" action="{action}">
  <input type="password" name="mapi_key" placeholder="MAPI key" autofocus required
         autocomplete="off" spellcheck="false">
  <button type="submit">Authorize</button>
</form>
<footer>The key is stored on this server and never sent to the MCP client,
which only receives a token you can revoke.</footer>
"""


def _escape(text: str) -> str:
    # Neutralise the three HTML-significant characters so text can't inject markup.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_login(rid: str, error: str | None = None) -> str:
    # Fill the page template with the request id and (escaped) optional error banner.
    # rid and error both reach the page from request input, so neither is
    # interpolated raw.
    block = f'<div class="err">{_escape(error)}</div>' if error else ""
    return _PAGE.format(action=f"{LOGIN_PATH}?{urlencode({'rid': rid})}", error=block)
