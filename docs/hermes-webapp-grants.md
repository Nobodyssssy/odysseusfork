# Scoped logout: Odysseus-side access grants for the Hermes Projects origin

Branch `feature/webapp-proxy`, local only (not pushed, not deployed). Code: `src/hermes_grants.py`, `src/hermes_proxy.py` (gate), `src/application_modes.py`
(`enter_url`), `core/auth.py` (revocation listener + digest lookup), `static/js/application-modes.js`. Tests: `tests/application_modes/test_proxy_grants.py` (66).

## What it guarantees, and what it does not

**Logging out of Odysseus ends access to Hermes (Projects) through this proxy, for that browser session only.** The next request is refused, every WebSocket the proxy is
relaying for that session is closed (code 4401), and streamed responses are cut. Other Odysseus sessions (same user or not) keep their access. Switching Personal/Projects
keeps both sessions. The official gateway's own authentication is untouched and still decides every request that passes the gate.

**It does not revoke anything inside Hermes, and it does not protect the gateway's own port.** Measured live after logout: the same Hermes bearer token still returned 200
when sent straight to `127.0.0.1:<gateway port>`. A copied Hermes cookie or token, a native Desktop app, the CLI, or any process on this computer that can reach the gateway port
keeps its access. The loopback bind and the gateway's own Host/Origin and token checks are the only barriers there, and the same-user-code limit documented earlier still applies.
This is a web-vector control, not a sandbox. No UI or document may describe it as complete logout.

## Design

| Piece | Behaviour |
|---|---|
| Ticket | one-use, 30 s, memory only. Created only by the authenticated, same-origin, local-administrator launch route, bound to the caller's Odysseus session token (re-read from the cookie and checked against the identity the middleware established), user and a server-held redirect target. The browser receives an opaque URL `http://hermes.localhost:<port>/__odysseus/enter?ticket=...`. The gateway's own one-use launch link (a Hermes credential) stays inside the ticket and appears only in the proxy's redirect, so it never passes through Odysseus JavaScript. |
| Exchange | `GET /__odysseus/enter`, handled by the proxy on the Hermes origin only. Requires the normal loopback/Host/Origin/forwarding policy, a top-level navigation (`Sec-Fetch-Mode: navigate`, `Dest: document`), a `Referer` from the Odysseus origin, exactly one `ticket` parameter and nothing else. Every refusal is the same generic 403. The ticket is consumed whatever the outcome. 303 to the stored target with `no-store`, `no-referrer`. |
| Grant | created by redeeming a ticket. Cookie `odysseus_hermes_grant`: 256-bit random, `HttpOnly`, `SameSite=Lax`, host-only, `Max-Age` = min(8 h, the Odysseus session's remaining life). Only its SHA-256 and the session digest are stored (SQLite, `<data dir>/hermes-grants.db`); the secret, the Odysseus token and the redirect target are never written. Persisted, so it survives an Odysseus restart exactly as long as the Odysseus session does. |
| Use | every HTTP request and WebSocket upgrade on the Hermes origin needs a live grant. Zero cookies, conflicting duplicates, a malformed value, an expired or revoked grant and any internal failure all deny. The Odysseus session is re-validated on every use, so a missed callback cannot keep a grant alive. A navigation without a grant is redirected to Odysseus; anything else gets 401 with `X-Odysseus-Grant: required`. |
| Forwarding | the grant cookie is removed from what the gateway sees (other cookies pass verbatim), and a gateway response cannot set or clear it. Hermes tokens never reach Odysseus. |
| Revocation | `AuthManager` now notifies a listener on `revoke_token`, `revoke_user_sessions`, `delete_user` and when a lookup finds an expired or orphaned session. The store drops that session's grants and pending tickets and signals every watcher: WebSocket relays close at once, streams and pending upstream calls are cut, including a request body mid-upload. Open sockets and streams additionally re-check every 2 s for endings nobody announced. |
| Fixation | a presented grant is reused only if it belongs to the same Odysseus session and user; otherwise a fresh grant is issued and the planted one is untouched. The browser cannot choose the redirect target or supply an identity. |
| Limits | 16 grants per session (oldest evicted), 8 outstanding tickets per session, 256 total. |
| Fail closed | no session manager, a bad TTL setting, an unreadable or wrong-version store: the Hermes proxy is not installed. `ODYSSEUS_HERMES_PROXY_GRANTS=0` turns the gate off explicitly and logs a warning; there is no silent downgrade. |

Settings: `ODYSSEUS_HERMES_GRANT_TTL_HOURS` (1 to 168, default 8), `ODYSSEUS_HERMES_ENTER_REFERER=0` (not recommended).
The legacy WebUI proxy mode and production behaviour are unchanged: grants exist only in `gateway` mode.

## Evidence (labels: unit = automated test, live = disposable preview with the real Odysseus app, the real gateway and a real browser)

- **unit, 66 tests** (store, real `AuthManager`, launch route, proxy over real sockets and real WebSockets). Mutation check: 12 deliberate breaks (skip use-time validation, accept another
  session's grant, tickets not one-use, grant outliving its session, no watcher signal, conflicting cookies, grant cookie forwarded, gateway sets the grant cookie, no Referer check,
  WebSocket and HTTP skip the gate, no periodic re-check) were each caught. Two weaknesses were found and fixed on the way (my Set-Cookie assertion only saw the last header;
  a watcher leaked on an already-revoked grant).
- **live, browser** (`docs/evidence/grants/`): the switch put a host-only HttpOnly SameSite=Lax cookie on the Hermes origin, invisible to page JavaScript and storage, and showed the
  Webapp. A second launch from another tab reused the single grant. Logout from a third tab returned 200 in 0.21 s; the open Hermes tabs fell to "Backend offline / Sign in to Hermes"
  and a request carrying a still-valid Hermes token got 401 from the gate. Refresh, Back and a brand-new tab to the Hermes origin all landed on the Odysseus login. Two full
  Personal-Projects round trips kept Odysseus authenticated and held exactly one grant.
- **live, sockets** (`grants-live-probe.json`, 22 pass): revoked grants refused on HTTP, POST, ticket minting and three WebSocket reconnects, while a junk ticket on a live grant is refused
  by the gateway rather than the gate (so the two layers are distinguishable). A second browser session of the same user was unaffected until it logged out itself; its two real gateway
  sockets then closed with 4401 within 0.02 s, and its reconnects were refused. The gateway still demanded its own token on a protected route.
- **live, restarts** (`grants-restart-probe.json`, 17 pass): after restarting Odysseus a live session's grant still worked, revoked grants stayed revoked, a session removed while Odysseus was down
  was denied with no callback ever delivered, and a new launch worked. After restarting the gateway the gate still admitted, and the gateway alone decided about its own token.
- Regression: `tests/application_modes` plus all auth/session/API-token tests ran with every dependency installed: 563 passed, 1 failed, 2 skipped. The failure
  (`test_auth_config_lock_concurrency::test_file_always_valid_json_during_concurrent_ops`, a Windows `os.replace` race) already fails on the pre-change commit and alone 3 of 3.

## Not verified, and limits

- Session expiry was proven in unit tests with an injected clock and live only through a session removed while Odysseus was down; a natural 7-day expiry was not waited out.
- Other users' sessions were tested in unit tests (alice/bob, real `AuthManager`); live isolation was between two sessions of the same user.
- Independently authenticated Hermes CLI/Desktop sessions are unaffected by construction (they never pass this proxy) and the live direct-port check confirmed it; that same fact is the limitation above.
- A revoked stream stops being delivered to the browser; whether the agent run behind it keeps going in the gateway was not tested and should be assumed to continue.
- Page content already rendered stays on screen until the next request fails (the Webapp then shows its offline state); the gate cannot clear a page.
- The grant cookie is per host, not per port: two Odysseus instances on different ports in one browser share `hermes.localhost` cookies and overwrite each other's grant. Not an issue for a single production instance; do not run preview and production side by side in one browser profile.
- The launch navigation needs the Odysseus origin as `Referer`. Odysseus sends `Referrer-Policy: no-referrer` everywhere; only the launch link opts in (`referrerPolicy=origin`). A browser or extension that strips it makes launching fail closed.
- `webapp.public_url` (sign-in gate) combined with grants was exercised only before grants were added; the proxy path is identical (`ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST=public` is covered in unit tests with grants), but the gated browser flow was not rerun with grants.
- The grant database is not in the backup set yet.

## Fit with the planned architecture

- **Browser on port 7000:** this is the design target; nothing else is exposed.
- **Native Windows/Linux app:** a client that dials the gateway directly authenticates with the gateway alone and never passes this gate. A client that embeds the browser surface can use the same launch route and cookie. The store and tickets are transport-neutral.
- **Phone/LAN later:** not enabled. It would need TLS (`Secure` cookies), explicit Host/Origin allowlists beyond `*.localhost`, relaxing the loopback requirement deliberately, and a per-device story for the Hermes side; none of that is implemented or implied.
