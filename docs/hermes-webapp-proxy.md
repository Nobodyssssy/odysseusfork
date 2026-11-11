# Official Hermes Webapp behind the Odysseus proxy

Branch `feature/webapp-proxy` (worktree `F:\Odysseus\integration-backups\webapp-proxy`, based on `feature/phase1-permissions`).
Code: `src/hermes_proxy.py`. Tests: `tests/application_modes/test_proxy_gateway.py` (29) beside the unchanged `test_proxy.py` (12).
Nothing here is installed, enabled or pushed. The proxy stays in the legacy mode unless `ODYSSEUS_HERMES_PROXY_MODE=gateway` is set.

## Design

`HermesHostProxy(app, upstream, mode='webui' | 'gateway', policy)`. The proxy only ever acts on requests whose Host is exactly
`<allowed name>:<listener port>`; everything else goes to Odysseus untouched (so a forged Host can never reach Hermes).

| | `webui` (default, legacy) | `gateway` (official Webapp) |
|---|---|---|
| Host / Origin / Cookie / bodies | forwarded unchanged | Host and Origin checked against the policy, then rewritten to the gateway's own `127.0.0.1:<port>` |
| Loopback peer, no `Forwarded` / `X-Forwarded-*` / `X-Real-*` | enforced | enforced |
| Exactly one Host and at most one Origin | not checked | enforced (400) |
| Origin must be an allowed exact origin; `null` never is | not checked | enforced (403) |
| `Sec-Fetch-Site` | not checked | WebSocket: must be `same-origin`; unsafe HTTP methods: `same-origin` or `none` |
| WebSocket | refused with 1008 (as before) | relayed frame for frame; Origin required |
| CORS response headers | passed through | dropped (same-origin only) |
| Host/Origin presented upstream | unchanged | `bound` (default): the gateway's own `127.0.0.1:PORT`; `public`: the browser's own, unchanged |
| Request logs | untouched | `token=`, `ticket=`, `access_token=`, `session_token=`, `code=`, `launch=` query values redacted in httpx/uvicorn lines |
| Cookie jar | none | none |

Why rewrite by default (`bound`): the gateway accepts only its own bound authority as Host and Origin
(`hermes_cli/web_server_boundary.py`), which is its DNS-rebinding defence, and it documents a Host-rewriting proxy as the supported
arrangement. The proxy performs the browser-facing check itself and the gateway's own check still runs on what it receives.
Nothing in the gateway was changed or relaxed, `webapp.public_url` was left empty, and the proxy never adds, stores or replays a
credential: the bearer token, one-use WebSocket ticket and cookies belong to the browser and the gateway.

WebSocket details: subprotocols are negotiated with the gateway and the gateway's choice is returned (`hermes-gateway-v1`; the
ticket-bearing protocol is never reflected, as the gateway intends); text and binary frames are relayed as-is; a close code and
reason from either side reaches the other (1005/1006/1015 are mapped to legal codes); an abrupt browser disconnect closes the gateway
connection; if the gateway disappears the browser's socket is closed; a gateway refusal (e.g. 401) is returned with its real status;
the largest frame is configurable (default 400 MB, because the gateway itself accepts one 384 MiB frame for an attachment).

`public` (pass-through) exists because the gateway's sign-in gate (`webapp.public_url` plus a dashboard provider) insists on the **real** browser origin for
cookie-authenticated writes: with Host/Origin rewritten, minting a WebSocket ticket returned 403 "Cookie-authenticated writes must come from the Webapp's
own origin" (live). With `public`, `webapp.public_url` must be `http://hermes.localhost:<Odysseus port>`; the gateway's own Host/Origin checks then judge
the browser's real values. A mismatched pairing (`public` without `public_url`, or `bound` with the gate on) fails closed (400/403).

Configuration (all optional): `ODYSSEUS_HERMES_PROXY_MODE`, `ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST` (`bound`|`public`), `ODYSSEUS_HERMES_PROXY_HOSTNAMES` (extra `*.localhost` names),
`ODYSSEUS_HERMES_PROXY_ORIGINS` (extra exact origins, e.g. a native shell), `ODYSSEUS_HERMES_PROXY_WS_NO_ORIGIN=1`,
`ODYSSEUS_HERMES_PROXY_WS_MAX_MB`. Invalid values make the proxy refuse to install (fail closed) rather than fall back.

Other files touched: `app.py` and `launcher.py` pass `ws_max_size` to uvicorn (unchanged unless gateway mode is on),
`requirements.txt` adds `websockets`. Odysseus has no WebSocket endpoints of its own, so Personal mode is unaffected.

## Evidence (labels: unit = automated test, lab = real browser + real gateway, disposable, nothing in production)

**unit**: `test_proxy_gateway.py` 29 passed (6 consecutive runs), `test_proxy.py` 12 passed; mutation check: removing the Host/Origin policy
fails 25 tests, not rewriting Host 4, allowing forwarded headers 9. Covers: legitimate HTTP and WebSocket (text, binary, 5 MB frame, subprotocol),
gateway-side 401 visible to the client, close codes both directions, abrupt disconnect, gateway vanishing, 10 foreign Origins, 10 forged Hosts,
duplicate Host/Origin, six forwarding headers, cross-site fetch metadata, non-loopback peer, gateway down (502), websockets package missing (501),
configuration validation, extra hostname, legacy mode unchanged.

**lab (official Webapp built from the pinned agent checkout, gateway on 19120, stand-in host running the real proxy class on 19700)**:
- Signed in with a one-use launch link rewritten to `hermes.localhost:19700`; the UI loaded, listed sessions, and showed Backend "ready".
- The browser's own WebSocket `/api/ws?native_dial=browser` was accepted through the relay.
- Chat: a message sent from the UI reached the agent and the reply streamed back.
- Tool call: the result of a scripted write outside the safe root (denied by upstream) appeared in the thread.
- Approval: a PowerShell command raised the approval card with the ask-policy text; **Reject** returned `BLOCKED: Action denied by user` to the agent and the file was not created.
- Authentication (valid bearer): own host 200; forged Host routed to the Odysseus stand-in; evil Origin, `Origin: null`, cross-site fetch metadata and `X-Forwarded-Host` refused (403); no bearer 401; wrong bearer 401.
- WebSocket with ONE valid one-use ticket: eight hostile upgrades (evil Origin, `null`, no Origin, other-port Origin, cross-site, `X-Forwarded-For`, forged Host, evil Host+Origin) were all refused by the proxy and the ticket was **still valid** afterwards (so the gateway never saw them); the legitimate upgrade then returned 101 with `hermes-gateway-v1`; replay of the ticket, a forged ticket and no ticket were refused.
- Production compared with the pre-lab baseline (listeners, config, WebUI settings and sessions, both `.env`, `auth.json`, `component.json`, checkouts, Odysseus HEAD, plugins): unchanged.

## Access grants (scoped logout)
Gateway mode additionally gates the Hermes origin on a server-side grant bound to the Odysseus browser session (on by default; `ODYSSEUS_HERMES_PROXY_GRANTS=0` disables it explicitly).
See `hermes-webapp-grants.md`. It is a second gate in front of the gateway's own authentication, never a substitute.

## Not verified / limitations

Retired since the first version of this note (see `hermes-webapp-preview.md`): the real Odysseus application was run in front of the gateway;
`webapp.public_url` and the sign-in gate were tested; the Webapp's chat, streaming, Stop, approvals and the plugin-loaded return control were
exercised in a real browser.

- The production Odysseus venv has no `websockets` package: in gateway mode every WebSocket would be refused with 501 until `requirements.txt` is installed there (fails closed, tested).
- uvicorn 0.54 logs `ASGI callable returned without completing handshake` once per refused WebSocket although the client receives the correct status; cosmetic. Its `--ws websockets` implementation is deprecated in favour of `websockets-sansio`; the relay was exercised with the default and with `websockets-sansio` in the unit suite (see the final report for the result).
- Same-user code can read the gateway's browser credential (`webapp\session-token`); the proxy does not and cannot close that.
- Odysseus logout does not end a Webapp session; `POST /auth/logout` (basic provider) clears only the browser's cookies; `webapp-sign-out` is the revocation.
- Large uploads through the Webapp (HTTP bodies, 384 MiB frames) were not exercised end to end; a 5 MB WebSocket frame and streamed HTTP bodies were.
- The terminal sockets (`/api/host-terminal`, `/api/pty`) and audio sockets were not exercised; the Electron app was not tested.
