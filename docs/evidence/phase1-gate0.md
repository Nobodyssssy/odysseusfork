# Phase 1 / P0-0 evidence (GATE 0) - 2026-11-11

Branch `feature/phase1-permissions` off `release/hermes-mode-windows` @ 8b428243 (same SHA on origin: ls-remote showed 8b428243 after push).
Target: live Hermes WebUI on 127.0.0.1:8787 (pin f328d3f8e + reviewed patch), reached also as http://hermes.localhost:7000.
All requests below are read-only or carry an empty/invalid body; none changed state.

## 1. Origin / CSRF per endpoint
Command (curl, POST with `Origin: http://evil.example` and, separately, `Sec-Fetch-Site: cross-site`, body `{}`):

| endpoint | Origin: evil | Sec-Fetch-Site: cross-site |
|---|---|---|
| /api/chat/start | 403 | 403 |
| /api/chat/steer | 403 | 403 |
| /api/terminal/start | 403 | 403 |
| /api/terminal/input | 403 | 403 |
| /api/approval/respond | 403 | 403 |
| /api/commands/exec | 403 | 403 |
| /api/trusted-workspaces/approve | 403 | 403 |
| /api/session/new | 403 | 403 |

Through the proxy: `POST http://hermes.localhost:7000/api/terminal/start` with `Origin: http://evil.example` -> 403.
Code: central check at `api/routes.py:19243` (`if not _csrf_exempt_path(path) and not _check_csrf(handler)`); exempt paths are only login, passkey login/options, csp-report (`routes.py:7744`).

## 2. FINDING: Host-header (DNS rebinding) bypass of the origin check
The check compares `Origin` to the request's own `Host` header (`routes.py:7701`). There is no Host allow-list.
Simulation of a rebinding attacker page whose name resolves to 127.0.0.1 (`Host` and `Origin` both `evil.test:8787`):

```
POST /api/approval/respond  Host: evil.test:8787  Origin: http://evil.test:8787  body {}
-> 400 {"error": "session_id is required"}          (i.e. it passed the CSRF/origin check and reached the handler)
GET  /api/sessions          Host: evil.test:8787
-> 200                                                (no auth: auth_enabled=false)
```
`GET /api/auth/status` -> `"auth_enabled": false, "password_auth_enabled": false`.
`password_hash` in `webui/settings.json`: empty. `HERMES_WEBUI_PASSWORD`: not set in the user environment.
With no password a rebinding page can read sessions and call chat/terminal endpoints. A password closes it (the session cookie is bound to the attacker's host, so it is not sent).

## 3. Proxy (src/hermes_proxy.py)
- `_permitted()`: peer must be in `_LOOPBACK` and no `forwarded`, `x-forwarded-*`, `x-real-*` header present.
- Observed: `GET http://hermes.localhost:7000/api/sessions` -> 200; same with `X-Forwarded-For: 1.2.3.4` -> 403.
- Websocket upgrades on that host are closed with 1008.
- Host not matching exactly (`Host: hermes.localhost:7001` to 127.0.0.1:7000) falls through to Odysseus (401).

## 4. YOLO / approval mode (actual values)
- `GET /api/session/yolo?session_id=...` for all 10 sessions: none enabled.
- `HERMES_YOLO_MODE` user/machine env: empty. Agent `_YOLO_MODE_FROZEN` = False.
- `tools.approval_context._get_approval_mode()` = `'smart'` (no `approvals:` key in config.yaml -> default).
  `smart` lets an automatic judge approve commands (the agent's own terminal calls in this session were reported as "auto-approved by smart approval").

## 5. Cookies / same-site
- Hermes cookie: `HttpOnly; SameSite=Lax; Path=/`, no Domain (auth.py:889). Odysseus cookie: `httponly, samesite=lax, path=/`, no domain (auth_routes.py:212). Both host-only.
- Not verified in a browser: whether `localhost` and `hermes.localhost` count as same-site. Cookies are not port-isolated, so `localhost:7000` and `localhost:8787` share a cookie jar; `hermes.localhost` has its own.
- `/health` is in PUBLIC_PATHS and is unauthenticated; it returns session/run counts and model names (seen in this session).
