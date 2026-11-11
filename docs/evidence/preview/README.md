# Preview evidence (2026-11-11)

Everything here was captured in the disposable preview (real Odysseus app on 127.0.0.1:19800, official Webapp gateway on
127.0.0.1:19120, scripted stub model, Low-integrity gateway). Nothing touched production.

Screenshots (1440x900 unless the browser window reset to its default size): 01 Personal (real Odysseus, Personal|Projects selector),
02 Projects (official Webapp, "<- Personal" in the status bar), 03 back in Personal, 04 chat + denied tool call, 05 approval card,
06 approval decisions, 07 two sessions, 08-11 Capabilities / Messaging / Artifacts / Scheduled jobs, 12 Settings, 13-18 layout, terminal panel,
Settings > Workspace, 19 new session, 20 fresh tab without sign-in, 21 after Hermes sign-out, 22 re-launch after sign-out,
23 Projects with the sign-in gate on (the official Sign-in page), 24-25 gated and signed in, 26 Personal after, 27 Projects (gated),
28-29 streaming mid-run and after Stop.

Data files: `gate-probe.json` (sign-in gate probe, 41 pass / 0 fail / 9 informational), `stub-stream-cancel.log` (the model side saw the
client disconnect after 47 of 60 chunks when Stop was clicked), `sweep-1.json`, `sweep-2.json` (area sweep of the Webapp).

The Odysseus-side hostile-request matrices (launch route, WebSocket tickets) are tabulated in `docs/hermes-webapp-preview.md`.
