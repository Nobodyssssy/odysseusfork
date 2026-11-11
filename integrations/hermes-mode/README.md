# Hermes mode return extension

This directory is loaded only by the isolated mode-development launcher. It uses the standalone WebUI's existing opt-in extension mechanism, not an iframe or a replacement chat UI. Its only integration authority is navigation between explicitly configured loopback URLs; it must not alter authentication, models, tool permissions, memory, or sessions.

`connection.json` is generated in private development state, never in the Hermes installation. Extensions execute with full WebUI page authority; review the JavaScript before enabling it elsewhere.

## Desktop plugin: `desktop-plugins/odysseus-return`

For the official Hermes Desktop Webapp (not the old WebUI): adds a "← Personal" item to the Webapp status bar that returns the tab to the Odysseus origin
that served it. Install by copying the folder to `<hermes home>/desktop-plugins/odysseus-return/` (the Webapp's own plugin loader picks it up).
See `docs/hermes-webapp-preview.md`.
