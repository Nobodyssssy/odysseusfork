# Security verification of the deployed Projects hand-over (ed0bea07)

Date 2026-11-12. Scope: the hand-over and gate fixed this session (one-time tickets, grants, cookies, Host/Origin, WebSockets, logout, back/forward). It is **not** a verification of the whole permission system (file/Git/MCP routes, the terminal, same-user isolation); see "Not covered".

Legend: **PASS** measured live; **PARTIAL** measured only indirectly; **LIMIT** a known, documented property that is not fixed; **NOT TESTED**; **MANUAL** needs the user's own browser or login.

Environment: disposable preview on `localhost:19800` (same commit as production, real Odysseus + managed gateway, real Firefox 160 via WebDriver BiDi, synthetic lab account), plus read-only reads of the production log. Production was not modified. Probes: `tests/desktop-lab/security_verify.py` (HTTP/WebSocket), `ff_backforward_logout.py` (browser).

## 1. Production log from byte offset 3,085,783 (after the deploy)

One line: `enter refused before the ticket: reason=fetch-metadata mode=same-origin dest=empty` at 17:05:44, the old service worker on the user's first click. No refusal afterwards, no errors, no tickets or grant secrets in the text. **PASS.** The user confirmed Projects opens and Personal returns.

## 2. Results

| Area | Check | Result |
|---|---|---|
| Ticket | launch returns an opaque `http://hermes.localhost:<port>/__odysseus/enter?ticket=<43 chars>`; no gateway credential in the URL | PASS |
| Ticket | redeems exactly once; 303 to the server-held target; replay refused with no cookie | PASS |
| Ticket | older than 30 s refused (`ticket-expired` logged) | PASS |
| Ticket | extra query parameter / client-chosen target refused; POST refused; foreign Referer refused; `cors/empty` fetch refused (the old-service-worker case) | PASS |
| Ticket | refusals before the ticket is read do not consume it | PASS |
| Ticket | at most 8 live tickets per session (9th and 10th refused) | PASS |
| Ticket | a ticket minted before logout cannot be redeemed after it | PASS |
| Session | launch without a session, with a forged session cookie, or from a foreign Origin: refused (401/401/403) | PASS |
| Cookie | grant cookie `HttpOnly; SameSite=Lax; Path=/; Max-Age=28800`, host-only (no Domain) | PASS |
| Cookie | Odysseus session cookie `HttpOnly; SameSite=lax; Path=/; Max-Age=604800` | PASS |
| Cookie | no `Secure` flag on either cookie | LIMIT: plain http on loopback; a Secure cookie would not be sent. Host-only and loopback-only |
| Unauthorized | Hermes API without a grant: 401 + `X-Odysseus-Grant: required`; forged grant: 401; conflicting duplicate grant cookies: 401; real grant: 200 | PASS |
| Host/Origin | foreign Host with a valid grant never reaches Hermes; `X-Forwarded-For`, `Forwarded`, `X-Forwarded-Host` refused (403); foreign-Origin POST 403; cross-site POST 403; duplicate Host header 400 | PASS |
| WebSocket | no grant 401; forged grant 401; valid grant + foreign Origin 403; valid grant + no Origin 403; the enter route is not a WebSocket endpoint | PASS |
| WebSocket | valid grant + right Origin + the gateway's one-time socket ticket connects, and a live socket closes when the session ends | PARTIAL: the probe had no gateway socket ticket (the gateway answered 403 itself, which is its own authentication working). The relay, `_authorize`, `_ws_refuse`, grant watch/revoke code is byte-identical to the code behind the earlier live 17/17 logout probes (`git` comparison with `4d31efa7`); in the real browser the Hermes tab held 3 connections to the gateway while open and 0 after leaving it |
| Logout | logout 200; the grant stops working at once (401); logging in again does not revive it; the logged-out session cannot mint a ticket | PASS |
| Back/forward | Personal -> Projects -> chip -> Personal (bfcache) -> forward -> back: all work with a live session | PASS |
| Back/forward | logout from Personal, then history-forward to the Hermes entry: lands on the Odysseus login within 3 s, stays there at 15 s; back and a fresh visit also land on login | PASS |
| Refusal page | one indistinguishable response for every refusal; carries only its own script (CSP hash), no ticket echoed, removes stale service workers on its own origin only | PASS (unit test + live) |
| Logging | refusal reasons logged without ticket values (byte-offset scan of the production log) | PASS |
| Gateway port | direct request to the gateway's loopback port without credentials: protected routes 401, including with a hostile Origin or a rebinding Host | PASS |
| Gateway port | `/api/status` is public on the gateway port and returns version, paths, pid, `install_id` and memory/disk figures to any local process | LIMIT (low): information only; the proxy gates it, the port does not |
| Gateway port | a same-user local process can obtain a gateway token (launch control) and then call everything | LIMIT: the documented same-user limit (`hermes-windows-isolation.md`); only a separate account or sandbox closes it |
| Ticket | the ticket is a 30-second bearer: anyone who sees the URL within 30 s *and* can send navigation-shaped requests with the Odysseus Referer could redeem it first | LIMIT (low): one-time, 30 s, the legitimate redemption normally wins; the spent ticket stays in browser history |

Focused unit tests run this session on the changed files: `test_proxy_grants.py` + `test_proxy_gateway.py` 101 passed (including 8 refusal-page and ticket-lifecycle tests). The full suite was not rerun.

## 3. Not covered by this verification

- NOT TESTED: grant expiry at Odysseus-session expiry live (unit-tested), total-grant capacity, two Odysseus users with separate sessions, Chrome/Edge/Safari, a browser with the old WebUI worker *and* the new refusal page on the production proxy (the cleanup was proven against the real `sw.js` on a stand-in and by the user's first click).
- MANUAL: logout of the real production session with a Projects tab open; the optional new-tab flow on production; repeated cycles after the worker is gone (the user reports it works).
- OUT OF SCOPE here: the file/Git/MCP route guard (earlier evidence `docs/evidence/guard/`), terminal pane, Claude/Codex credential handling, approval flows.

## 4. Is the deployed fix complete?

For the navigation/authentication failure: **yes.** Root cause measured (a service worker left by the old Hermes WebUI at `hermes.localhost` turned the navigation into a fetch, which the enter route correctly refuses), reproduced with the real worker, fixed without relaxing a check, deployed, and confirmed in the production log and by the user. The security system as a whole is **not** claimed verified: the limits above and the not-covered list remain.
