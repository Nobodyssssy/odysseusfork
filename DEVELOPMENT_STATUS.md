# Development status: Odysseus + Hermes integration (fork)

This fork (`Nobodyssssy/odysseusfork`) carries an independent line of work on top of the upstream Odysseus project. It is a personal, best-effort
integration, not an upstream release. Nothing here has been proposed to or accepted by upstream.

## License and attribution
- Odysseus is licensed under the **GNU AGPL-3.0**; this fork keeps `LICENSE`, `ACKNOWLEDGMENTS.md` and `THIRD_PARTY_PROVENANCE.json` unchanged. Changes in
  this fork are released under the same license.
- Upstream: `odysseus-dev/odysseus`. This fork does not have write access there. Upstream contributors' commits that were adopted keep their original authorship.
- Hermes Agent / Hermes Webapp (Nous Research) and hermes-webui are MIT licensed. They are pinned and patched, never vendored (see below).

## What this branch contains
- A Hermes Webapp component: pinned upstream release, side-by-side release staging, explicit activation, rollback.
- Hermes served on its own origin behind Odysseus: host proxy, access grants bound to the Odysseus session, launch tickets, route guard.
- Workspace permissions: ask-policy and workspace-grants plugins, Trusted Workspaces patch for hermes-webui, gateway restart guard.
- Wiki Steward (MCP server and maintenance tooling) and MCP recovery for the managed gateway.
- Projects dashboard (read-only aggregator), sidebar entry, Back to Hermes, native-project import tool, app-only restart script.
- Windows 11 Cookbook work (llama.cpp runtime discovery/installation, failed-task diagnosis, durable cancellation, cached model scan).

## Reproducing the Hermes side
- Pins live in `config/hermes-component.lock.json` (full 40-hex commits). Patches applied to upstream are versioned in `integrations/hermes-mode/patches/`
  (`webapp-route-guard.patch`, `webapp-mcp-recovery.patch`, `webui-trusted-workspaces.patch`); the component verifies patch state before use.
- Build and management: `scripts/hermes-webapp-component.py`; see `docs/hermes-webapp-component.md`.

## Not in this branch (kept on separate local branches, unfinished or unreviewed)
- Projects overlay inside Hermes (create/rename/archive/move conversations), chat-backend reliability fixes (save-failure handling, replay-buffer bounds,
  Stop confirmation), composer changes, Cookbook dependency-catalog and model-list refresh, provider sync.

## Known limitations
- Windows-first. Linux/macOS behavior of the new Windows paths is untested.
- A Hermes release restart is required to pick up component changes; MCP changes need a reload.
- Some tests need the `playwright` Node package and are skipped or fail without it; a few Cookbook tests assume POSIX llama-server behavior.
- Documents under `docs/` describe a specific machine layout in places; paths are examples.

## Security notes
- No credentials, databases or personal data are intended to be in this repository. Report problems privately to the repository owner.
- Test fixtures contain obviously fake secrets (for example `sk-SENTINEL...`).
