"""The pages a user of this server sees: the login (consent) screen and the
re-key screen. Two forms of the same shape - paste a MAPI key - plus a small
confirmation page after a successful re-key.

Deliberately dependency-free: no CSS framework, no JS, no external fetches, so
they render identically wherever the browser opens them.
"""

from __future__ import annotations

from urllib.parse import urlencode

LOGIN_PATH = "/login"
REKEY_PATH = "/rekey"

_STYLE = """<style>
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
  .ok  {{ background: #22c55e1a; border: 1px solid #22c55e55; border-radius: .4rem;
          padding: .6rem .7rem; font-size: .9rem; margin-bottom: 1rem; }}
  footer {{ margin-top: 2rem; font-size: .82rem; color: #888; }}
</style>"""

_FORM = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{style}
<h1>{heading}</h1>
<p>{blurb}</p>
{error}
<form method="post" action="{action}">
  <input type="password" name="mapi_key" placeholder="MAPI key" autofocus required
         autocomplete="off" spellcheck="false">
  <button type="submit">{button}</button>
</form>
<footer>{footer}</footer>
"""

_DONE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MAPI key updated</title>
{style}
<h1>MAPI key updated</h1>
<div class="ok">{message}</div>
<footer>You can close this tab and return to your MCP client - the connection
already uses the new key, no reconnect needed.</footer>
"""

_KEY_FOOTER = ("The key is stored on this server and never sent to the MCP client, "
               "which only receives a token you can revoke.")


def _escape(text: str) -> str:
    # Neutralise the three HTML-significant characters so text can't inject markup.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _form(*, path: str, rid: str, error: str | None, title: str,
          heading: str, blurb: str, button: str, footer: str) -> str:
    # Render one MAPI-key form. rid and error both come from request input, so
    # neither is interpolated raw.
    block = f'<div class="err">{_escape(error)}</div>' if error else ""
    return _FORM.format(
        style=_STYLE, title=title, heading=heading, blurb=blurb, button=button,
        footer=footer, error=block,
        action=f"{path}?{urlencode({'rid': rid})}",
    )


def render_login(rid: str, error: str | None = None) -> str:
    # The first-time consent screen: authorize the connection with a MAPI key.
    return _form(
        path=LOGIN_PATH, rid=rid, error=error,
        title="Connect MIDAS NX", heading="Connect MIDAS NX",
        blurb="Paste the MAPI key from <b>[API Settings]</b> in MIDAS CIVIL/GEN NX.",
        button="Authorize", footer=_KEY_FOOTER,
    )


def render_rekey(rid: str, error: str | None = None) -> str:
    # Swap the stored key without reconnecting - after a renewal, or to move
    # between CIVIL and GEN (the program follows whichever key you paste).
    return _form(
        path=REKEY_PATH, rid=rid, error=error,
        title="Update MIDAS NX key", heading="Update your MAPI key",
        blurb=("Paste a new MAPI key to replace the current one. To switch between "
               "MIDAS CIVIL and GEN, paste that program's key - the connection "
               "follows the new key automatically."),
        button="Replace key", footer=_KEY_FOOTER,
    )


def render_rekey_done(program: str) -> str:
    # Confirmation page: show which program (civil|gen) the new key activated.
    message = f"The connection now uses your new key. Active program: <b>{_escape(program.upper())}</b>."
    return _DONE.format(style=_STYLE, message=message)
