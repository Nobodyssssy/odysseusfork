# Trusted Workspaces (Hermes Mode)

When you pick a folder in the Hermes UI that Hermes's file tools are not yet allowed to write in, a dialog asks **Deny / Allow once / Always allow**. You no longer edit `HERMES_WRITE_SAFE_ROOT` by hand.

## How it works

Hermes's Agent already refuses file writes outside `HERMES_WRITE_SAFE_ROOT` and re-reads that variable on every write. The Agent runs inside the WebUI process, so the WebUI keeps the variable equal to **the roots set when Hermes started + the folders you approved**. An approval or revocation applies to the very next file write, including in a run that is already active. No restart is needed.

- **Always allow** is saved in `<hermes home>\vault\webui-trusted-workspaces.json`. That file is under the Agent's protected `vault` folder, so its file tools cannot write it (checked with the real guard on 2026-11-11, including when a launch root covers `<hermes home>\webui`). **Nothing else in this list is protected that way**: see "Keep these out of HERMES_WRITE_SAFE_ROOT" below.
- **Allow once** lasts until Hermes restarts.
- **Revoke** in Hermes **Settings → Preferences → Trusted folders**.
- Roots set at launch (your existing `HERMES_WRITE_SAFE_ROOT`) are always kept and cannot be revoked there. **They are not checked against the "cannot be approved" list below**: whatever the variable names, the Agent's file tools can write, with no prompt.
- With nothing approved, Hermes can write nowhere. An empty list never means "unrestricted".
- The feature does nothing unless `HERMES_WRITE_SAFE_ROOT` is already set (or `HERMES_WEBUI_TRUSTED_WORKSPACES=1`), so it never turns a restriction on or off by itself.
- In the dialog, drive roots, your home folder, Hermes's own data folder, system folders and network/device paths cannot be approved. That list covers the data folder itself, not its sub-folders (for example `<hermes home>\webui`), and it does not apply to launch roots. The dialog shows the real folder after resolving junctions, and a folder swapped for a junction after approval loses its approval until re-approved.

## Keep these out of HERMES_WRITE_SAFE_ROOT

Measured on 2026-11-11 with the Agent's own `get_write_denied_error` and the launch value that was set on this PC (7 roots, including `<hermes home>\webui`, `<Odysseus component root>`, `F:\hermes-webui`, `F:\Odysseus`, `F:\Odysseus-Backups`):

| Path | Agent file tools |
|---|---|
| WebUI `settings.json` (password hash), `.sessions.json`, `.signing_key` | **writable** |
| Odysseus-Hermes component config (`managed\component.json`) | **writable** |
| WebUI source incl. `api\auth.py`, `server.py` and the guard `api\trusted_workspaces.py` | **writable** |
| Odysseus source, `.env`, `data\app.db`, backups | **writable** |
| `<hermes home>\vault\...` (this feature's approvals) | protected |
| `<hermes home>\.env`, `state.db`, `sessions\` | protected |
| `<hermes home>\config.yaml`, `auth.json`, `plugins\`, `hermes-agent\` | refused (outside the roots) |

So an Agent with file tools can edit its own password hash, session store and enforcement code if any root covers them. **Do not list those folders in `HERMES_WRITE_SAFE_ROOT`**; use a narrower root such as one project folder. A never-allow entry also protects a folder inside a root, but only on the paths that enforce it (see `docs/hermes-ask-policy.md`).

## What it is not (read this)

- **It is not an operating-system sandbox.** It governs Hermes's *file tools* only. The terminal tool, other programs and anything else running as your Windows account are unaffected: an Agent with a shell can still touch any file you can.
- **Approval is a browser interaction, not a hard security boundary.** The approve endpoint accepts only same-origin browser requests, and the dialog ignores scripted clicks; an AI chat message cannot approve. But a process running as you that forges browser headers could call the endpoint. Closing that gap requires a separate Windows account or an elevated prompt, which this feature deliberately does not install.
- It does not weaken `HERMES_WRITE_SAFE_ROOT`: approvals are added to it in-process; the Windows environment variable itself is never modified.

## Packaging

The feature is a reviewed patch to the pinned Hermes WebUI (`integrations/hermes-mode/patches/webui-trusted-workspaces.patch`). The component installer applies it to the pinned checkout and accepts a checkout only if it is clean at the pin or equals the pin plus exactly that patch. Tests for the patched module: `integrations/hermes-mode/patches/test_webui_trusted_workspaces.py` (runs against the real Agent write check).

## Rollback

Delete `vault\webui-trusted-workspaces.json` to drop saved approvals, or `git apply -R` the patch in the WebUI checkout and restart Hermes.
