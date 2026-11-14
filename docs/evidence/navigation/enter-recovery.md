# Projects hand-over: refusal recovery (Firefox)

Symptom: Personal -> Projects showed "Could not open Hermes from Odysseus. Return to Odysseus and choose Projects again." at `hermes.localhost:<port>/__odysseus/enter`; a hard refresh then opened the Webapp.

## What the evidence says

- **Correction (after deployment).** I first wrote that production logged nothing for the failure. That was wrong: the old code logged the same generic line `grant exchange refused` for *every* ticket-stage refusal, and the production log has such lines at 15:43:32, 15:46:28 and 15:46:33, two of them 5 s apart at a time when no test of mine was running, so they are most likely the user's failed clicks. What the old line cannot say is which refusal it was. A hard refresh of the same URL then worked, which means the ticket was **not** spent, so it was refused before it was redeemed: the old code has exactly two such paths, the `Referer`/`Sec-Fetch-*` checks (which logged nothing) and the query-shape check (`ticket=` not the only parameter, or repeated), which logged the generic line without consuming the ticket. The 15:46 lines therefore point at **query-shape**, but that is an inference, not a measurement.
- **Not reproduced outside the user's browser.** Firefox 160 (the installed build), clean profile and a replica of the user's profile (prefs, 42 extensions with their settings and granted permissions, no cookies/history), trusted keyboard and mouse input, BFCache returns through the `← Personal` chip, a cold gateway start, 4 consecutive cycles, against the disposable preview: every `enter` request carried `Referer: http://localhost:<port>/`, `Sec-Fetch: cross-site, navigate, document`, and passed.
- **Not caching, not a service worker, not the cookies, not a race**: the refusal is `no-store`; no service worker is registered; cookie handling happens after the checks; the ticket is minted after the gateway is ready, with 30 s to live.
- **Unproven:** which pre-redemption check refused in the user's browser (referer, fetch-metadata or query-shape). The deployed build now logs the reason; the first real failure will name it. The simulations below reproduce the same signature (refused before redemption, same ticket accepted on reload) for the referer case only.

## Fix (no check weakened)

1. (Deployed 2026-11-12, `1b2d2f80`.) Every refusal logs a reason code and the non-secret facts (`reason=referer seen=absent site=cross-site`, `reason=fetch-metadata mode=... dest=...`, `reason=ticket-unknown-or-already-used|ticket-expired|odysseus-session-not-live|query-shape|ticket-malformed|grant-capacity`). Clients still get one indistinguishable response.
2. The refusal page recovers by itself, at most three steps within 30 s (state in the tab's `sessionStorage`): reload once, then return to `http://localhost:<port>/?open=projects` for a fresh ticket, then stop and show the message with a link back to Odysseus. The page is served with `script-src 'sha256-...'` for its single script and `default-src 'none'`.

## Firefox 160 results (clean profile, WebDriver BiDi, trusted input)

| Scenario | Result |
|---|---|
| 4 full Personal -> Projects -> `← Personal` cycles, no interception | all pass, no refusal in the server log |
| Referer removed from the first click navigation (the observed signature) | refused (`reason=referer seen=absent`), page reloads itself, request 2 is accepted, Webapp opens in about 1 s |
| Ticket already spent / unknown on two requests | `reason=ticket-unknown-or-already-used` twice, bounce to Odysseus, fresh ticket, request 3 accepted, Webapp opens in about 2 s |
| Referer removed from every request (a cause the retry cannot fix) | exactly 3 requests, then the message stays; 20 s of silence, no loop (a screenshot of the message was captured during development and is not published) |
| Optional "Open Projects in new tab" | opens Hermes, the original tab is untouched |
| Logout in Odysseus | Hermes API 200 before, 401 after; the Hermes tab leaves for the Odysseus login |

Tests: `tests/application_modes/test_proxy_grants.py::test_a_refused_hand_over_recovers_by_itself_without_relaxing_anything` plus the existing refusal tests (134 passed across proxy, backend and service-control files). Probes: `tests/desktop-lab/ff_*.py` (need Firefox, `websockets`, a preview).

## Production check after deployment (no login available to the agent)

Real Firefox 160 against `localhost:7000`, bogus tickets, redacted: a normal link click and a typed URL both produce the refusal page, which reloads once, goes back to Odysseus once and ends on the Odysseus login in about 0.2 s; the server log carries `reason=ticket-unknown-or-already-used` / `reason=referer seen=absent site=cross-site`; no ticket value appears anywhere in the log (checked with a byte-offset scan of the new log text). Authenticated Personal <-> Projects cycles on production were left to the user's browser. Note for anyone counting log lines in a script: use a byte offset, not `len(text.split("\n"))`.

## Root cause (measured, 2026-11-12, after the first deployment)

After the user clicked Projects five times and each click showed the refusal page, the production log named the refusal on every attempt:
`enter refused before the ticket: reason=fetch-metadata mode=same-origin dest=empty` (15 lines, three per click: first request, reload, fresh ticket). A link navigation is `navigate/document`; `same-origin/empty` is a plain fetch.

The user's Firefox profile (`serviceworker.txt`, scope strings only) still holds the **old Hermes WebUI's service worker**: `http://hermes.localhost:7000/` -> `sw.js?v=exp-v0.52.653-dirty-27d6d68d`. Its navigation handler is `fetch(new Request(event.request, {cache:'no-store'}))` (`F:\hermes-webui\static\sw.js`); building a Request from a navigation request turns mode `navigate` into `same-origin` and drops the destination. The proxy correctly refuses that on `/__odysseus/enter`. A hard refresh bypasses service workers, which is why only Ctrl+Shift+R worked, and why every earlier test (fresh or replica profiles with no hermes.localhost worker) passed. The official Webapp bundle registers no worker (searched `dist-webapp`).

Reproduced in Firefox 160 with the real `sw.js` on a stand-in origin (`tests/desktop-lab/sw_standin.py`, `sw_driver.py`; the stand-in makes the same two decisions as the proxy and serves the real refusal page from `src/hermes_proxy.py`):

| Case | Server sees on the enter request | Result |
|---|---|---|
| no worker | `navigate / document`, Referer = Odysseus | accepted |
| old worker, previous page | `same-origin / empty`, Referer = `.../sw.js` (twice: click and reload) | refused, bounce, refused |
| old worker, fixed page | refused once, then `navigate / document` | accepted after 0.1 s; no registration left |

Fix: the refusal page unregisters every service worker on the origin and deletes the `hermes-shell-*` caches before it retries. One brief refusal flash on the first click after deployment, then no worker exists and clicks pass first time. No check was relaxed.
