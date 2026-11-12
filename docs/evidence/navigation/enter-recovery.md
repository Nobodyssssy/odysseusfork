# Projects hand-over: refusal recovery (Firefox)

Symptom: Personal -> Projects showed "Could not open Hermes from Odysseus. Return to Odysseus and choose Projects again." at `hermes.localhost:<port>/__odysseus/enter`; a hard refresh then opened the Webapp.

## What the evidence says

- **The refusal came before the ticket was read.** Production logged nothing for the first failure (a ticket-level refusal always logs `grant exchange refused`), and logged `grant ... issued` for the hard refresh. A refusal before the ticket leaves it unspent (it lives 30 s), which is why the same URL worked on reload. Only two checks run there: `Referer` must be the Odysseus origin, and `Sec-Fetch-Mode/Dest` must be `navigate/document`.
- **Not reproduced outside the user's browser.** Firefox 160 (the installed build), clean profile and a replica of the user's profile (prefs, 42 extensions with their settings and granted permissions, no cookies/history), trusted keyboard and mouse input, BFCache returns through the `← Personal` chip, a cold gateway start, 4 consecutive cycles, against the disposable preview: every `enter` request carried `Referer: http://localhost:<port>/`, `Sec-Fetch: cross-site, navigate, document`, and passed.
- **Not caching, not a service worker, not the cookies, not a race**: the refusal is `no-store`; no service worker is registered; cookie handling happens after the checks; the ticket is minted after the gateway is ready, with 30 s to live.
- **Unproven:** which of the two headers differed in the user's browser. The production build did not log it. The simulation below reproduces the same signature (silent pre-ticket refusal, same ticket accepted on reload).

## Fix (no check weakened)

1. Every refusal logs a reason code and the non-secret facts (`reason=referer seen=absent site=cross-site`, `reason=fetch-metadata mode=... dest=...`, `reason=ticket-unknown-or-already-used|ticket-expired|odysseus-session-not-live|query-shape|ticket-malformed|grant-capacity`). Clients still get one indistinguishable response.
2. The refusal page recovers by itself, at most three steps within 30 s (state in the tab's `sessionStorage`): reload once, then return to `http://localhost:<port>/?open=projects` for a fresh ticket, then stop and show the message with a link back to Odysseus. The page is served with `script-src 'sha256-...'` for its single script and `default-src 'none'`.

## Firefox 160 results (clean profile, WebDriver BiDi, trusted input)

| Scenario | Result |
|---|---|
| 4 full Personal -> Projects -> `← Personal` cycles, no interception | all pass, no refusal in the server log |
| Referer removed from the first click navigation (the observed signature) | refused (`reason=referer seen=absent`), page reloads itself, request 2 is accepted, Webapp opens in about 1 s |
| Ticket already spent / unknown on two requests | `reason=ticket-unknown-or-already-used` twice, bounce to Odysseus, fresh ticket, request 3 accepted, Webapp opens in about 2 s |
| Referer removed from every request (a cause the retry cannot fix) | exactly 3 requests, then the message stays; 20 s of silence, no loop (`enter-giveup.png`) |
| Optional "Open Projects in new tab" | opens Hermes, the original tab is untouched |
| Logout in Odysseus | Hermes API 200 before, 401 after; the Hermes tab leaves for the Odysseus login |

Tests: `tests/application_modes/test_proxy_grants.py::test_a_refused_hand_over_recovers_by_itself_without_relaxing_anything` plus the existing refusal tests (134 passed across proxy, backend and service-control files). Probes: `tests/desktop-lab/ff_*.py` (need Firefox, `websockets`, a preview).
