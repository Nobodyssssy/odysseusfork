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
Most likely cause (behaviour confirmed, mechanism not yet instrumented): once any browser logs in through the proxy, Hermes's session cookie sits in the proxy's jar and every later proxied request - from any local process - is authenticated as that session.
The proxy also adds no Odysseus-login requirement (known from Phase 0), so hermes.localhost:7000 currently needs no credentials at all once someone has logged in once.
