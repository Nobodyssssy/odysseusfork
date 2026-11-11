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
Rebinding shape (`Host`+`Origin` = evil.test, `Sec-Fetch-Site: same-origin`): direct 8787 approve/terminal/chat -> 401; sent to the PROXY PORT (127.0.0.1:7000) with an evil Host, the request was ROUTED TO ODYSSEUS (the Host does not match `hermes.localhost:7000`), and Odysseus's own auth middleware returned 401. The proxy did not reject it and Hermes never saw it.
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

## 10. Process rule: `git status --porcelain` on the pinned WebUI (F:\hermes-webui) around this task
BEFORE and AFTER (captured with the system git, compared with `diff`: IDENTICAL):
```
 M api/routes.py
 M server.py
 M static/index.html
?? api/trusted_workspaces.py
?? api/windows_console.py
?? static/trusted-workspaces.js
?? tests/test_trusted_workspaces_prompts.py
?? tests/test_windows_console.py
```
Live diff sha256 `e9bdf4c1c167` == reviewed patch sha256 `e9bdf4c1c167` (still exactly pin + reviewed patch, no stray files).
Correction: an earlier draft of this section said "Identical: False" because the Python step could not find the BEFORE snapshot saved by the shell; that was a bookkeeping error, the comparison above is the real one.
The `csrf_matrix.py` / `tw_matrix.py` probes from the previous turn were written INSIDE the tree by mistake and deleted; since then every probe runs from scratch (`%TEMP%`), including the invariant test (`HERMES_WEBUI_ROOT` points at the tree, nothing is copied into it).

## 11. Q1 - plan/adoption fix (src/hermes_component.py, src/hermes_component_runtime.py)
Bug (code): `plan_existing()` called `verify_checkout(path, pin)` WITHOUT the patch that `serve`/`validate` pass, so a correctly patched adopted WebUI always read as "differs from the reviewed pin".
Fix: `plan_existing(..., application_root=None)` passes `patch_file(application_root, name)`; `discover_existing` passes its root. The acceptance predicate was NOT loosened.
Gap found while testing (tightened): `patch_state()` ignored untracked files, so pin + patch + a stray `csrf_matrix.py` read as `applied` (test failed with `'applied' != 'foreign'`). It now also requires every untracked, non-ignored file to be one of the patch's own files.
[sim, real clone of nesquena/hermes-webui at the pin] `tests/application_modes/test_component_plan_patch.py` (RED first: TypeError + the untracked case), now 10 passed:
- pin + reviewed patch -> MATCHING; clean pin -> accepted exactly as before; same patched tree planned WITHOUT the patch -> DIFFERS (the old bug, kept as a test)
- extra tracked edit -> DIFFERS; stray untracked file (top level and in a new folder) -> DIFFERS; deleted patch file -> DIFFERS; one patched file reverted (partial patch) -> DIFFERS; wrong revision -> DIFFERS; ignored `__pycache__` -> still MATCHING
Related suites: `test_component_patch.py`, `test_component_runtime.py`, `test_component_cli.py` pass. `test_component.py::test_explicit_enable_restores_saved_component_without_reinstalling` FAILS (`KeyError: 'agent'`, hermes_component.py:346) **identically on untouched `release/hermes-mode-windows`** (checked in a throwaway worktree): pre-existing, not caused by this change.
[live, read-only] `hermes-component.py plan` now returns `{"existing": {... "origin": "external", "webui": "F:\\hermes-webui" ...}}` instead of "Hermes source differs from the reviewed pin".
Interpretation note: "pin only (no patch) must FAIL" was implemented as "a patched tree verified without the patch fails"; a CLEAN pin tree is still accepted (today's behaviour; `maintain` then applies the patch). "Patch reverse-applied" was implemented as a partly reverted patch. Say if you meant otherwise.

## 12. GET invariant (integrations/hermes-mode/patches/test_webui_auth_invariant.py)
Now also covers every URL-path literal in every `handle_*`/`_handle_*` function of api/routes.py, GET included, with an explicit hard-coded public allowlist
(`/health`, `/api/auth/status`, `/login`, `/favicon.ico`, `/sw.js`, the login-flow/OIDC/passkey paths, manifests, `/share`, prefixes `/static/`, `/session/static/`, `/share/`, `/api/share/` minus create/revoke) and a test that `auth.PUBLIC_PATHS` equals that list exactly.
[sim, real pinned code, run from scratch] 6 tests OK: 145 write-route paths and 270 handler paths (GET included) x 5 header shapes each; no credential-less request reaches anything outside the allowlist.
Mutation check (negative control): adding `/api/sessions` and `/api/terminal/output` to `PUBLIC_PATHS` makes 2 tests FAIL (`test_every_get_and_other_route_refuses_unless_explicitly_public`, `test_the_set_of_public_paths_is_exactly_the_reviewed_one`).
Known gap: `/health` is public by design and returns active run counts and model names (seen live). Gating that detail is pending (WebUI change, see ownership below).

## 13. Ownership of F:\hermes-webui
`C:\Users\Protagonist\AppData\Local\Odysseus\hermes\managed\component.json`: `"origin": "external"`, `"webui": "F:\\hermes-webui"`, `"agent": "C:\\Users\\Protagonist\\AppData\\Local\\hermes\\hermes-agent"`, `"extension_dir"` present. A managed install would have `"origin": "managed"` and a path under a generation folder (hermes_component.py:398).
=> **ADOPTED / USER-OWNED.** The "report, don't modify" rule applies to any further change inside F:\hermes-webui (Host allowlist, `/health` detail gating, rebinding the file-safety chokepoint from the WebUI hook). Not touched.

## 14. Approval mode (read-only findings, nothing changed)
- Effective `approvals.mode` = `smart`. Source: `hermes_cli/config_defaults.py:1657` (`"mode": "smart"`); the live config.yaml has no `approvals:` block, so the default applies. (The code fallback in `approval_context._get_approval_mode` is `manual`, but the merged default wins.)
- Not a WebUI toggle: no Settings control (grep of static/ and api/), and `/api/commands/exec` only allows `reload-mcp`, `reload-skills`, `codex-runtime`, `credits` (api/commands.py:31), so `/approvals` cannot be run from the WebUI chat. Where it can be set: `approvals: {mode: manual}` in config.yaml; `hermes config set approvals.mode manual` (user's own terminal); `/approvals manual` in the Hermes CLI/TUI; the separate Hermes dashboard config editor (hermes_cli/web_routers/config_env.py).
- **`manual` is not ask-by-default.** tools/approval.py:1310-1330: a prompt appears only when `_command_findings(command)` returns a pattern hit; otherwise `_approved_with_provenance`. The real detector missed `Set-Content`, `echo > D:/...`, `git push origin main`, `curl -X POST -d @file`, `copy secrets.txt D:\leak\` (phase1-gate0.md / earlier probe). Setting `manual` alone will not make those prompt.
- **Permanent allowlist in the live config.yaml** (`command_allowlist`): `Windows PowerShell destructive delete (later in the script)`, `delete in root path`, `execute_code`, `recursive delete`. `command_allowlist` entries bypass the gate in every mode (approval.py:1296 `_command_matches_permanent_allowlist` runs before the prompt). So `execute_code` never asks today, and neither do recursive/root deletes.
- Existing official extension point: a plugin `pre_tool_call` hook may return `{"action": "approve"}` to route ANY tool call (terminal, execute_code) to the same human gate via `request_tool_approval()` (approval.py:1245), or `{"action": "block"}`; its rule keys are namespaced so the user's existing allowlist entries do not match it. A user-level plugin lives outside both pinned trees. Not yet built.
- `approvals.deny` (config) is a glob deny list that blocks even under YOLO; it is pattern matching and must not be described as security.

## 15. Provider sync regression (known P1, caused by the Hermes password)
Cause: `fetch_hermes_providers` does an unauthenticated `GET /api/providers` (provider_adapters.py:153) -> 401 -> state `requires_authentication` -> the Hermes side is treated as EMPTY (`he = []`, provider_sync.py:47).
[live] `GET 127.0.0.1:8787/api/providers` -> 401.
What fails: (1) listing Hermes providers: banner "Hermes asks for a login, so its providers cannot be read here." (provider-sync.js:160), Hermes columns empty; (2) Odysseus->Hermes apply: blocked at provider_sync.py:131 with the message "Hermes is not reachable, so nothing was written." (inaccurate wording: it is a login wall, not an outage); (3) Hermes->Odysseus apply: the source is not found in the empty list -> "unknown provider"; (4) copy-key: needs the Hermes entry from the same list -> fails with "unknown, login-based, or Hermes does not keep its key in its .env file" (misleading wording). Nothing is half-written: every path fails closed before writing.
What still works: Odysseus-side providers are listed and used; Hermes's `.env` and `config.yaml` are read from disk (not via HTTP) only after a provider is found, so they are not reached.
Not done on purpose: storing the Hermes password. Proposed later item (P1): a scoped read-only token for exactly `GET /api/providers`, minted by Hermes, readable only by Odysseus, never a login credential.
Also proposed: make the two error messages say "Hermes requires a login" when the state is `requires_authentication`.

## 16. Session lifetime (read-only)
Controlled by `_resolve_session_ttl()` (api/auth.py:32): env `HERMES_WEBUI_SESSION_TTL` (60 s..1 year) > settings.json `session_ttl_seconds` > default `SESSION_TTL` = 30 days. Neither the env var (user env, launcher file names checked; the live process environment was not read) nor `session_ttl_seconds` (settings.json keys listed) is set => **effective lifetime is the 30-day default**. Cookie `Max-Age` is the same value; sessions persist in `STATE_DIR/.sessions.json`.
Tradeoff: that file is readable by any process running as you, so a LONGER lifetime lengthens the window in which a stolen session is usable; a SHORTER one (e.g. 12 h) shrinks the window at the cost of more logins. No change proposed.
