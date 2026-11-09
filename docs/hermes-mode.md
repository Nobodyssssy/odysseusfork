# Hermes Mode — draft Windows component integration

**Status: development draft, not a production release.** This branch preserves the existing Windows-fork base and does not include the separate Trusted Workspaces implementation. Do not merge it into a live installation yet.

## Architecture and implemented scope

Odysseus and the standalone Hermes WebUI remain separate backends and complete native interfaces. Mode switching uses top-level navigation, not an iframe or reverse proxy. Hermes framing protection is unchanged. There is no shared database, memory migration, permission manager, or model-server launcher.

The branch includes:

- An opt-in application selector and native Hermes return extension, with draft-preservation and departure guards.
- A local-admin/same-origin launch route; forwarded requests and Agent internal/API identities cannot launch Hermes through that route.
- Immutable Agent/WebUI source pins in `config/hermes-component.lock.json`, verified tracked source, isolated managed runtime provisioning, and read-only adoption planning.
- Persistent component configuration, disable, re-enable of an installed component, and non-destructive repair generations. Disabling does not terminate a running service.
- A CLI and runtime wrapper that reuse a healthy Hermes backend without taking shutdown ownership and refuse unrelated or degraded listeners.
- External, private backup helpers with online SQLite backup, integrity checks, and SHA-256 manifests. A backup is not an atomic cross-application snapshot.

The default Windows component directory is `%LOCALAPPDATA%\Odysseus\hermes`, outside the Odysseus checkout. Override it with `ODYSSEUS_HERMES_COMPONENT_ROOT` for an isolated trial. Adoption retains the existing Hermes home and WebUI state paths instead of copying them into Odysseus.

## Current manual entry points

Run from the checkout with its existing Python environment. These are development entry points, not an automatic-install release recipe:

```powershell
.\venv\Scripts\python.exe scripts\hermes-component.py status
.\venv\Scripts\python.exe scripts\hermes-component.py plan
```

`status` and `plan` do not install or start Hermes. `plan` may inspect a running local backend to resolve its actual source and state paths; it does not emit provider credentials.

The CLI also implements `ensure`, `repair`, `disable`, `enable`, and `serve`. `ensure` can adopt a verified existing installation or provision pinned sources and a managed runtime. `--fresh` requires a new component configuration and is intended for an explicitly isolated installation. Provisioning and extension registration write state, so review the target paths and obtain the required authorization before using them against an existing installation. `enable` currently expects a previously installed component; enabling a disable-only/not-installed marker still needs completion.

The application discovers saved component configuration on startup. The normal Windows launcher does **not yet** automatically invoke provisioning. No scheduled task or production startup wrapper was changed.

## Verification performed for this draft

- Focused suite: 39 tests discovered; 37 passed and 2 opt-in end-to-end tests skipped in the ordinary run.
- The real network-backed installation test was separately exercised in an external disposable directory: fresh provisioning, idempotent reuse, retained fixture state, and unchanged write-policy setting.
- The real managed-server lifecycle test was separately exercised on an ephemeral loopback port: actual WebUI startup, session/draft save, backend restart, draft restoration through the native session API, return-extension injection, unchanged `X-Frame-Options: DENY`, and configured state-directory inheritance. Only processes launched for that private fixture were terminated.
- The original Phase 2 browser/lifecycle evidence applies to its isolated prototype, not blanket acceptance of this newer draft.

## Remaining release gates

- Wire provisioning into the native Windows install/startup path, with single-instance admission and recoverable optional-component failure.
- Complete disable-before-install enablement and explicit destination/return-URL update behavior.
- Complete the fresh-install, upgrade, rollback, failed-repair, and latest browser acceptance matrix.
- Review source/runtime integrity, extension registration concurrency, path/ACL assumptions, and metadata validation. Current checks are not an OS sandbox or a defense against an administrator or the same OS account modifying executable dependencies.
- Obtain independent review; this draft has not received independent security approval.
- Verify real inference/model connectivity and tool execution separately. No real model prompt was sent by the managed-server lifecycle test; OS reboot behavior is untested.
- Confirm the production merge target and perform a separately gated backup/upgrade. This draft is not permission to restart an active backend or replace the running installation.

## Safety boundaries

`HERMES_WRITE_SAFE_ROOT` is inherited rather than expanded by mode switching. Reading an already-approved Windows User environment setting when it is absent from a launcher process is not a permission-approval mechanism. Existing permissions, credentials, databases, model configuration, startup registrations, and active runs must be preserved.

An occupied port alone is not evidence of a Hermes service. A health marker is a compatibility check, not cryptographic authentication. All configured mode destinations and owned Hermes startup bindings are restricted to `127.0.0.1`; remote exposure, shared-origin proxying, authentication changes, and protected human-approval infrastructure are outside this draft.
