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
| Cookie jar | none | none |

Why rewrite instead of passing Host through: the gateway accepts only its own bound authority as Host and Origin
(`hermes_cli/web_server_boundary.py`), which is its DNS-rebinding defence, and it documents a Host-rewriting proxy as the supported
arrangement. The proxy performs the browser-facing check itself and the gateway's own check still runs on what it receives.
Nothing in the gateway was changed or relaxed, `webapp.public_url` was left empty, and the proxy never adds, stores or replays a
credential: the bearer token, one-use WebSocket ticket and cookies belong to the browser and the gateway.

WebSocket details: subprotocols are negotiated with the gateway and the gateway's choice is returned (`hermes-gateway-v1`; the
ticket-bearing protocol is never reflected, as the gateway intends); text and binary frames are relayed as-is; a close code and
reason from either side reaches the other (1005/1006/1015 are mapped to legal codes); an abrupt browser disconnect closes the gateway
connection; if the gateway disappears the browser's socket is closed; a gateway refusal (e.g. 401) is returned with its real status;
the largest frame is configurable (default 400 MB, because the gateway itself accepts one 384 MiB frame for an attachment).

Configuration (all optional): `ODYSSEUS_HERMES_PROXY_MODE`, `ODYSSEUS_HERMES_PROXY_HOSTNAMES` (extra `*.localhost` names),
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

## Not verified / limitations

- The real Odysseus application was not run in front of the lab gateway; a stand-in ASGI app wrapped by the real proxy class was. The wiring (`install_hermes_proxy` in `app.py`, outermost middleware) is unchanged and unit-tested, but not exercised end to end.
- The production Odysseus venv has no `websockets` package: in gateway mode every WebSocket would be refused with 501 until `requirements.txt` is installed there (fails closed, tested).
- uvicorn 0.54 logs `ASGI callable returned without completing handshake` once per refused WebSocket although the client receives the correct status; cosmetic. The plain-close alternative would lose the real status code.
- `webapp.public_url` was not set. The gateway therefore runs without the dashboard login gate, and the session token in the browser's `sessionStorage` is the only credential: anything that can read that token (or `webapp\session-token` in the Hermes home) is the user. This is the same-user-code limit documented earlier; the proxy does not and cannot close it.
- Large uploads through the Webapp (HTTP bodies, 384 MiB frames) were not exercised end to end; the 5 MB WebSocket frame and streamed HTTP bodies were.
- Tokens in a WebSocket query string (`?token=`) are accepted by the gateway; the proxy forwards the query string and nothing logs it, but uvicorn access logs would show it if that form is used. The Webapp itself uses tickets.
- Terminal sockets (`/api/host-terminal`, `/api/pty`), audio sockets and the Electron app were not tested.
