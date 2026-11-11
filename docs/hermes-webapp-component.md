# Hermes Webapp component (official Gateway + Desktop Webapp)

Branch `feature/webapp-proxy`, local only (not pushed, not deployed). Entry points: `scripts/hermes-webapp-component.py`,
`src/hermes_webapp_component.py`, pin `webapp` in `config/hermes-component.lock.json`. Tests: `tests/application_modes/test_webapp_component.py` (39).

## Isolation from the WebUI component
Separate state (`<root>/managed/webapp/`), separate lock and releases. `src/hermes_component.py` and its `component.json` are not edited, read or locked
by this code (test: `WebUiIsolationTests`). The new lock key is additive; `load_pins()` still returns only `agent` and `webui`.

## Pinning
`webapp` = `NousResearch/hermes-agent` at one full 40-hex commit (currently `3b421578588243f920886e6fe79a6faa87ea026e`). Anything else (short SHA, branch,
other repository, upper case) fails closed. Node and pnpm majors come from the pinned checkout's own `package.json` `engines`, not from this repo.

## Ownership
| | managed | adopted |
|---|---|---|
| source | pinned clone in `releases/<id>/src` | the user's checkout, which must already be at the pin (never modified) |
| bundle | official `apps/desktop/scripts/build-webapp.mjs` in that clone | same script run on a `git archive` export of the pin in `releases/<id>/src`; served with `HERMES_WEBAPP_DIST` |
| runtime | `--python` or a built venv | `--python` (required) |
| gateway home | created, owned; only `webapp.*` and `desktop.repo_scan_*` keys are set (round-trip YAML, comments kept, backup `config.yaml.pre-odysseus-*`) | read-only; `plan` reports what the user must decide |
| start / stop / restart / rollback | yes | refused: "user-owned" |
Ownership never switches. Disable keeps everything. Upgrade (`repair`) builds a new release next to the old one; `rollback` flips back (bundle receipt hash and runtime
re-verified first). A failed build is marked `failed`, never referenced, and leaves the previous config untouched.

## Safety rules enforced in code
- Build environment: no `HERMES_*`, `ODYSSEUS_*`, credential-shaped variables; caches and `TEMP` live inside the component.
- Gateway environment is an allowlist (system variables only), so provider keys in Odysseus' environment never reach the agent's shell. `HERMES_WRITE_SAFE_ROOT` is
  passed through only if the operator already set it; it is never invented.
- New managed home: `approvals.mode: manual`, repository discovery off, `_config_version` copied from the pinned source (without it Hermes treats the file as ancient).
- `webapp.public_url` is refused unless the home already has an auth provider: upstream will not bind otherwise (observed in the real run) and Odysseus will not invent credentials.
- Stop only kills a listener proven by the gateway's own `gateway.pid` (kind, pid, home, `argv[0]` = this release's `gateway/run.py`); refuses when sessions look active
  (`/api/status` is best-effort, so 0 is not proof) unless `--force`. Never matches on the process name.
- Desktop plugin `odysseus-return` is copied into a managed home only; a different file of the same name is never overwritten, and the conflict is found before the config is touched.

## Evidence
- live (disposable root `desktop-lab/component-root`, port 19130): real clone at the pin, real `pnpm install` (11m42s) + official build, real gateway start through the component,
  `/` 200 serving the built bundle, `/api/health` commit `3b42157`, clean stop; real ruamel edit keeps comments/keys and writes a backup.
- live: adopted mode against a real pinned clone: `plan` writes nothing, `ensure` records it, start/stop/restart/rollback refused, checkout `git status` identical before/after, export = 23586 files = `git ls-files`.
- live (negative): adopting an extracted non-git copy is refused; nothing changed.
- simulation: fixtures with fake node/pnpm and a stub gateway for the rest (ownership, rollback, tamper, foreign listener, pid mismatch).
- Existing `tests/application_modes`: no failures. Production (7000/8100/8787) untouched.

## Not covered
Production move, the logout cascade, an OS-level sandbox, `websockets` in the production venv, hooking the component into the Odysseus service menu / backup set.

## Logout
Upstream facts (pinned source): `POST /auth/logout` clears cookies in one browser; the basic-auth provider's `revoke_session` is a documented no-op (stateless HMAC tokens), so
there is no upstream per-session revocation, and `webapp-sign-out` is global. Scoped logout is therefore implemented on the Odysseus side as server-side access grants bound to the
Odysseus session: see `docs/hermes-webapp-grants.md` (design, live evidence, and the limits: it cannot revoke copied Hermes credentials or protect direct gateway access).
