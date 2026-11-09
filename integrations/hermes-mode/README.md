# Hermes mode return extension

This directory is loaded only by the isolated mode-development launcher. It uses the standalone WebUI's existing opt-in extension mechanism, not an iframe or a replacement chat UI. Its only integration authority is navigation between explicitly configured loopback URLs; it must not alter authentication, models, tool permissions, memory, or sessions.

`connection.json` is generated in private development state, never in the Hermes installation. Extensions execute with full WebUI page authority; review the JavaScript before enabling it elsewhere.
