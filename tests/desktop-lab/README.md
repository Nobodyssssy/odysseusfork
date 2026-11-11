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

## Preview of the real Odysseus app in front of the lab gateway

    python tools/preview.py setup|start|status|stop|clean   # real Odysseus app on 19800, gateway proxy mode, synthetic admin (secrets in odysseus-preview/lab-admin.secret, never printed)
    python tools/gate_config.py on|off                      # Webapp sign-in gate: webapp.public_url + dashboard.basic_auth (needs PREVIEW_ENV_ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST=public; restart the lab gateway)
    python tools/gate_probe.py                              # sign-in gate probe through the Odysseus proxy (41 checks; run with the venv that has `websockets`)
    python tools/proxy_host.py PORT GATEWAY_PORT            # stand-in host wrapping the real proxy class (earlier lab step)

`stub_llm.py` also answers `STREAM:SLOW` with 60 tokens 0.4 s apart and logs whether the client disconnected (`logs/stub-stream.log`), for streaming and Stop checks.
Paths are hard-coded to `F:\Odysseus-Backups\desktop-lab`; the preview needs a venv built from `requirements.txt` at `ody-full-venv`.
