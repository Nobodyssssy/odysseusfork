# Desktop lab harness

Isolated official Hermes Webapp lab: throwaway Hermes home, Webapp on 19120, API server 18642, scripted stub model on 18700,
gateway running at **Low integrity** (OS-enforced write boundary; only folders labelled Low are writable). No admin, no Docker.

Paths are hard-coded to `F:\Odysseus-Backups\desktop-lab` (src = `git archive` of the pinned agent checkout, built with
`node apps/desktop/scripts/build-webapp.mjs` using Node 26 + pnpm from `%LOCALAPPDATA%\hermes\tools`).

    python tools/lab.py setup plugin_off|plugin_on [wide]   # fresh home, synthetic fixtures; `wide` puts the Hermes home in the safe root
    python tools/lab.py start | status | stop
    python tools/t1_webapp_security.py                       # auth, Host/Origin, WebSocket
    python tools/t2_direct_routes.py                         # direct fs/git/MCP routes vs the agent guard
    python tools/t4_webapp_essentials.py LABEL [SCENARIO[:once|deny] ...]   # agent tool matrix through the real /api/ws

Limits: Low integrity blocks MSYS/Git bash from starting, so shell tools cannot execute in the lab (they fail "No shell found");
decisions are therefore verified at the approval layer, not by running shell commands.
