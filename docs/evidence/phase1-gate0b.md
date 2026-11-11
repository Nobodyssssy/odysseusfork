# Phase 1 / GATE 0b evidence - 2026-11-11 (continuation of phase1-gate0.md)

Labels: **[live]** = real HTTP request to the running server; **[sim]** = real code from the pinned WebUI / Odysseus
called in-process with a fake request object (no server state touched); **[code]** = read from source.
No browser test was run. No state-changing request with a valid body was sent anywhere.

## 0. Branch
`feature/phase1-permissions` HEAD caeef93d, parent 8b428243. `release/hermes-mode-windows`: local 8b428243, origin (ls-remote) 8b428243.

## 1. The password changed the live picture mid-audit
[live] `GET /api/auth/status` at 14:57: `auth_enabled: true, password_auth_enabled: true`. `webui/settings.json` mtime 14:51:17,
`password_hash` present (boolean checked only; value never read). After that, every unauthenticated POST/GET I sent directly to
127.0.0.1:8787 returned 401 "Authentication required" (all 5 trusted-workspaces POSTs x 5 header shapes, plus 3 GETs with Host: evil.test).
`/health` stays 200 (public by design). The pre-password live result is in phase1-gate0.md section 2 (rebinding request reached the handler).

## 2. Origin/CSRF check - central `_check_csrf` (routes.py:19243), every POST/PUT/PATCH/DELETE except login/passkey/csp-report
[sim] Real `routes._check_csrf` with `api.auth.is_auth_enabled` forced False (= live state before the password) then True:

```
##### auth_enabled forced to False  (matches the LIVE server today)
case                     Origin                          None same-origin   same-site  cross-site        none
legit direct             None                            PASS        PASS     blocked     blocked        PASS
legit direct             null                         blocked     blocked     blocked     blocked     blocked
legit direct             http://127.0.0.1:8787           PASS        PASS        PASS     blocked        PASS
REBINDING Host==Origin   None                            PASS        PASS     blocked     blocked        PASS
REBINDING Host==Origin   null                         blocked     blocked     blocked     blocked     blocked
REBINDING Host==Origin   http://evil.test:8787           PASS        PASS        PASS     blocked        PASS
foreign Origin           None                            PASS        PASS     blocked     blocked        PASS
foreign Origin           null                         blocked     blocked     blocked     blocked     blocked
foreign Origin           http://evil.example          blocked     blocked     blocked     blocked     blocked

##### auth_enabled forced to True  (after a password is set; no CSRF cookie/token sent)
case                     Origin                          None same-origin   same-site  cross-site        none
legit direct             None                            PASS        PASS     blocked     blocked        PASS
legit direct             null                         blocked     blocked     blocked     blocked     blocked
legit direct             http://127.0.0.1:8787        blocked     blocked     blocked     blocked     blocked
REBINDING Host==Origin   None                            PASS        PASS     blocked     blocked        PASS
REBINDING Host==Origin   null                         blocked     blocked     blocked     blocked     blocked
REBINDING Host==Origin   http://evil.test:8787        blocked     blocked     blocked     blocked     blocked
foreign Origin           None                            PASS        PASS     blocked     blocked        PASS
foreign Origin           null                         blocked     blocked     blocked     blocked     blocked
foreign Origin           http://evil.example          blocked     blocked     blocked     blocked     blocked
```
Reading it:
- **Missing headers: the check fails OPEN.** No Origin/Referer/Sec-Fetch-Site -> PASS (auth off or on; with auth on the separate login gate then returns 401 - see section 1). `Sec-Fetch-Site: same-origin` or `none` without Origin also PASS. This is deliberate for curl/MCP/agent callers.
- **Origin: null -> blocked** in every row.
- **Rebinding (Host==Origin) with auth OFF passes** for SFS absent/same-origin/same-site/none. With auth ON it is blocked only because the attacker page has no CSRF cookie/token - i.e. the password is what stops it, not the origin check.
- `/api/shutdown` (routes.py:19260) sits behind the same check; deliberately NOT probed live.

## 3. Trusted-workspaces approve / revoke / decide / block / unblock
[code] routes.py:21547-21552: one shared guard, `_check_same_origin_browser_request(require_provenance=True)`.
[sim]:
```
trusted-workspaces approve/revoke/decide/block/unblock share ONE guard: _check_same_origin_browser_request(require_provenance=True)
case                       Origin                          None same-origin   same-site  cross-site        none
rebinding Host==Origin     None                         blocked     blocked     blocked     blocked     REACHES
rebinding Host==Origin     null                         blocked     blocked     blocked     blocked     blocked
rebinding Host==Origin     http://evil.test:8787        REACHES     REACHES     REACHES     blocked     REACHES
local process, real Host   None                         blocked     blocked     blocked     blocked     REACHES
local process, real Host   null                         blocked     blocked     blocked     blocked     blocked
local process, real Host   http://127.0.0.1:8787        REACHES     REACHES     REACHES     blocked     REACHES
foreign Origin             None                         blocked     blocked     blocked     blocked     REACHES
foreign Origin             null                         blocked     blocked     blocked     blocked     blocked
foreign Origin             http://evil.example          blocked     blocked     blocked     blocked     blocked
```
- Rebinding with `Origin: http://evil.test:8787` REACHES the handler for SFS absent/same-origin/same-site/none (real browsers send same-origin).
- A local process with no browser headers is blocked, but one that sends `Origin: http://127.0.0.1:8787` REACHES the handler. So with auth off, the agent's own shell could self-approve folders. With the password set, the login gate (401) sits in front.
- No-Origin + `Sec-Fetch-Site: none` REACHES (provenance rule accepts "none").

## 4. Odysseus (7000)
- [live] AUTH_ENABLED: log `Auth middleware enabled (AUTH_ENABLED=true)` at 07:30:59, 11:32:33, 11:43:59 (data/logs/app.log). `.env` lines 81/99 are commented examples; the variables are not in the user/machine environment or the launcher files.
- [code] app.py:299 `LOCALHOST_BYPASS = getenv(..., "false") == "true"` -> false. Enforcement: `AuthMiddleware.dispatch` (app.py:~410-470); loopback bypass only if `LOCALHOST_BYPASS and _is_trusted_loopback`.
- [live] Unauthenticated GET with `Host: evil.test:7000, Origin: http://evil.test:7000, Sec-Fetch-Site: same-origin`: /api/application-modes 401, /api/backup/full 401, /api/provider-sync 401; /api/health 200 (exempt). 401 for a loopback caller = no loopback bypass is active.
- [sim] `require_local()` (application_modes.py:205) allows only hostnames `127.0.0.1` and `localhost`: Host==Origin with `evil.test:7000` -> 403 "Application modes are local-only"; localhost / 127.0.0.1 pass it and then need the admin cookie.
- Not sent live: POST hermes/stop, hermes/restart, odysseus/restart, hermes/start, provider-sync copy-key (a wrongly-open route would restart services or copy keys). Their protection is the same middleware (proved by the GET 401s) + `require_local` + `require_browser_admin`.
- Note: the Odysseus session cookie is host-only (samesite=lax, no Domain) - so it is not sent to hermes.localhost; Odysseus admin routes are not reachable from a hermes.localhost page.

## 5. NEW FINDING - Hermes password is bypassed through the proxy
[live], after the password was set:
```
GET http://127.0.0.1:8787/api/sessions                -> 401
GET http://hermes.localhost:7000/api/sessions         -> 200 (gzip JSON, session list), no cookie sent
GET http://hermes.localhost:7000/api/auth/status      -> "auth_enabled":true,"logged_in":true   (same curl, no cookie)
GET http://127.0.0.1:8787/api/sessions -H "Host: hermes.localhost:7000" -> 401   (so it is not the Host header)
```
[code] src/hermes_proxy.py:35-40 builds one long-lived `httpx.AsyncClient` shared by all requests, with no cookie-jar restriction.
httpx stores Set-Cookie from responses in the client jar and replays it on later requests that carry no Cookie header of their own.
CONFIRMED cause (code: httpx 0.28.1 `_client.py:1737` `self.cookies.extract_cookies(response)` on every response; `_merge_cookies` re-adds the jar when the request has no Cookie header; reproduced in isolation by `test_the_proxy_never_remembers_or_replays_cookies`, which failed before the fix with `'a=1; b=2' is not None`): once any browser logs in through the proxy, Hermes's session cookie sits in the proxy's jar and every later proxied request - from any local process - is authenticated as that session.
The proxy also adds no Odysseus-login requirement (known from Phase 0), so hermes.localhost:7000 currently needs no credentials at all once someone has logged in once.

## 6. TASK 1 - proxy cookie-jar fix (src/hermes_proxy.py)
Change: `_StatelessClient(httpx.AsyncClient)` overrides the public `cookies` property to return an always-empty `_NoCookies` jar
(`extract_cookies`, `set_cookie_header`, `set` are no-ops). One shared client is kept (keepalive and SSE streaming unchanged); no per-response
`clear()`; the caller's own `Cookie` header is forwarded untouched and upstream `Set-Cookie` is still returned to the browser. Module docstring corrected.
[sim, real uvicorn + fake Hermes] `tests/application_modes/test_proxy.py`: new test RED before the fix, then 12 passed, including
`test_headers_cookies_query_and_origin_pass_through_unchanged`, `test_sse_is_streamed_not_buffered`, `test_large_uploads_content_length_and_chunked`.
[live, throwaway proxy process on a random port in front of the REAL Hermes 8787; Odysseus on 7000 untouched]:
```
proxied GET /api/sessions, no cookie (x3): 401, 401, 401
proxied GET /api/auth/status, no cookie:   200 {"auth_enabled": true, "logged_in": false, ...}
proxied GET /health: 200 ; GET /: 302 ; GET /login: 200
```
NOT YET TRUE ON THE LIVE 7000 PROXY: that process still runs the old code (and a possibly poisoned jar) until Odysseus is restarted.
NOT TESTED: browser login through the proxy and reload persistence (needs the password, which I must not use); that behaviour is covered only by the
fake-Hermes test that a client-supplied Cookie header is forwarded unchanged.

## 7. Live confirmation of Task 1 after your restart (15:08 Odysseus pid 16660, 15:09 Hermes pid 19216, Chroma 15:08)
[live] cookie-less `GET http://hermes.localhost:7000/api/sessions` -> 401 (was 200 before the restart); `127.0.0.1:8787/api/sessions` -> 401;
`/api/auth/status` and `/health` -> 200 on both (public by design); with `Origin: http://evil.example` through the proxy -> 401.

## 8. TASK 2 - state-changing POSTs, no cookie, MALFORMED payloads only [live]
Through the proxy (`http://hermes.localhost:7000`), 4 shapes each (no headers + `{}`; Origin/Sec-Fetch-Site same-origin + `{}`; same-origin + `{not json`; no headers + empty body):

| endpoint | no hdrs | same-origin | malformed | empty |
|---|---|---|---|---|
| /api/trusted-workspaces/approve | 401 | 401 | 401 | 401 |
| /api/trusted-workspaces/revoke | 401 | 401 | 401 | 401 |
| /api/trusted-workspaces/block | 401 | 401 | 401 | 401 |
| /api/trusted-workspaces/unblock | 401 | 401 | 401 | 401 |
| /api/trusted-workspaces/decide | 401 | 401 | 401 | 401 |
| /api/terminal/start | 401 | 401 | 401 | 401 |
| /api/commands/exec | 401 | 401 | 401 | 401 |
| /api/chat/start | 401 | 401 | 401 | 401 |
| /api/approval/respond | 401 | 401 | 401 | 401 |

FAIL list (any 400/422/other = reached a handler): **none**. Direct 8787 control (approve, revoke, block, terminal/start x4 shapes): all 401.
Rebinding shape (`Host`+`Origin` = evil.test, `Sec-Fetch-Site: same-origin`): direct 8787 approve/terminal/chat -> 401; sent to the proxy port with an evil Host (not routed to Hermes) approve/terminal -> 401.
No valid approval and no real command was sent.

CSRF token for an unauthenticated client [live]: `GET /` -> 302 (empty body), `/login` 200 (2684 B), `/api/auth/status` 200, `/health` 200, `/api/auth/csrf` 401, `/api/csrf` 401.
No CSRF-like token in any body, no `Set-Cookie` or csrf header on `/login`. [code] `csrf_token_for_session()` (auth.py:1107) is derived from an authenticated session cookie and
is embedded only in the authenticated shell page (routes.py:17469-17494). Unauthenticated clients cannot obtain one.

## 9. Invariant (recorded, tested)
The origin guard fails OPEN for header-less clients BY DESIGN (curl/MCP/CLI send no Origin/Referer/Sec-Fetch-Site; section 2 matrix). Therefore auth must ALWAYS be on.
Test `integrations/hermes-mode/patches/test_webui_auth_invariant.py` (copy to `tests/`, run with the WebUI's interpreter; stdlib unittest) [sim, real pinned code]:
```
PasswordOn.test_the_route_list_is_not_empty_and_covers_the_dangerous_endpoints ... ok
PasswordOn.test_every_state_changing_route_refuses_a_credential_less_request ... ok   (every /api/ literal in handle_post/put/patch/delete x 5 header shapes)
PasswordOn.test_the_origin_guard_fails_open_for_headerless_clients_so_auth_must_stay_on ... ok
PasswordOff.test_without_a_password_the_same_request_is_not_refused... ok              (negative control)
Ran 4 tests in 3.868s  OK
```
The route list is parsed from `api/routes.py` with `ast`, so a newly added state-changing route is covered automatically. Nothing was changed to break CLI/MCP.
