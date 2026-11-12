# Personal / Projects navigation: what is implemented, what was measured, what to build

Requirement (user decision): one application window, Personal and Projects as first-class modes, **instant and state-preserving switching** as a core requirement of the final browser and native desktop experience. No iframe, no weakened authentication, no duplicated official Hermes UI, no large frontend rewrite now.

Labels: **measured** = observed in the disposable preview with a real browser; **source** = read in the pinned code; **unverified** = not tested.

## 1. Implemented now (browser)

| Piece | Status |
|---|---|
| Same-tab switch (Personal selector, "← Personal" in the Webapp status bar) | working, unchanged |
| **Open Projects in new tab** (link in the Personal bar) | working, measured: opens a `noopener` tab to `/?open=projects`; that tab starts Hermes on demand (32 s cold) and lands on Projects; the instruction is stripped from the address bar; the first tab stays on Personal with its draft |

Why that design: the link is a plain anchor (no popup blocker, no waiting on a click gesture while Hermes starts), the start-on-demand logic exists once, and the tab's draft lives in per-tab `sessionStorage`, so the new tab starts empty and cannot disturb the first tab's draft. The cost: the new tab loads the whole Odysseus page before it switches.

## 2. Measurements that decide the browser approach

| Question | Result |
|---|---|
| Personal after "Back" from Projects (same tab) | **measured: restored from the browser's back/forward cache in about 0.5 s with its JavaScript state and the unsent draft intact** (a marker set in the page survived; no reload) |
| Projects after Back/Forward | **measured: not restored from the cache; the page reloads.** Chrome's own reasons: `response-cache-control-no-store` (the gateway sends it) and `websocket` (the Webapp holds open sockets). The Webapp restored its own draft from its storage and re-attached to its server-side sessions |
| Personal to Projects via the selector (new one-use launch link) | **measured once, no sleeps: 3.3 s to a usable Webapp.** Earlier figures in these docs of 7 to 14 s included fixed waits in my own scripts and were wrong; cold start (Hermes stopped) about 42 s and a Services restart about 24 s were measured with polling and stand |
| Projects via history without a new launch link | unverified (an attempt was invalid: the test browser connection was unreliable) |
| Other browsers | **unverified**: only the Chromium in the test harness was measured; Firefox and Safari have different cache rules |

## 3. Recommended approach: browser

Order is by value for effort; none needs a frontend rewrite.

1. **Keep same-tab as the default and make Personal restore from the cache.** Personal already does when the user returns with browser Back. Make "← Personal" do the same: in the Desktop plugin, traverse history to the entry before the first Projects entry (`history.go(-(navigation.currentEntry.index + 1))`, valid because `navigation.entries()` lists only same-origin entries), and fall back to a normal navigation if the page is still visible after about 300 ms (opened in a new tab, no previous entry). Result: Personal returns instantly with draft, scroll position and open panels, in the same tab. Small, upstream-compatible (a runtime plugin), not yet built.
2. **Make Projects cache-eligible (optional, Chrome-specific).** Needs two small changes: the proxy relaxes `Cache-Control: no-store` to `no-cache` on the Webapp's HTML shell only (the shell is a static bundle; API responses keep `no-store`), and the Webapp closes its WebSockets on `pagehide` and reconnects on `pageshow` when `persisted` (a small patch to the pinned Webapp source, which already ships as a reviewed patch). A restored page must revalidate its Odysseus grant on `pageshow` and go to the login page if the grant is gone, so logout is not bypassed by a cached page. Do this only after step 1 and only if the 3 s Projects load is still too slow.
3. **Do not pursue a single document hosting both UIs in the browser.** Without an iframe the browser cannot hold two origins in one document, and merging the two SPAs is the rewrite we ruled out. The two-origin split (`localhost` and `hermes.localhost`) is also what keeps an XSS in one app from reading the other's storage and cookies.
4. **Two tabs stay an option, not the design** (the new-tab link). A named-tab reuse trick (`target=name`) does not work with `noopener`; avoiding `noopener` would give the Webapp page a handle to the Odysseus tab, which we do not want.

## 4. Recommended approach: native desktop (Electron)

**Target: one window, two always-alive views; switching is showing one and hiding the other, so nothing reloads and every state survives** (conversations, scroll, drafts, panels, running agent streams, open sockets).

- **Shell:** a small Odysseus Electron main process with one `BaseWindow` holding two `WebContentsView`s: the Odysseus UI and the official Hermes Webapp. Both are real pages, so no iframe and no copy of the Hermes UI. Switching changes which view is visible (or its bounds); the hidden view keeps running.
- **Isolation:** separate session partitions per view, `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`, no remote module, a navigation allowlist (`will-navigate` and `setWindowOpenHandler` deny anything outside the two loopback origins), no shared cookie jar.
- **Backend:** the Hermes view loads the same Webapp build (`dist-webapp`) the browser uses, from the managed gateway, through the same Odysseus proxy and grant gate. That keeps one integration for browser and native, one set of security controls, and the managed component keeps owning install, pin, patch, start and stop. A direct connection to the gateway (the upstream Desktop supports remote gateways with its own sign-in) would remove the proxy but also the Odysseus logout gate; keep it as a later, explicit choice.
- **Why not patch upstream's Desktop:** source of the pin: its Electron main creates many `BrowserWindow`s and no `WebContentsView`, and its renderer depends on its own preload bridge. Adopting it whole means carrying a large fork of `main.ts`; hosting the browser build in our own shell uses only supported surfaces. Cost: features that need the upstream preload bridge (native file dialogs, tray, auto-update of the Desktop itself) are not available in the browser build; the Webapp already degrades to its remote file picker.
- **Hotkeys and chrome:** the shell owns one switcher (Personal, Projects, shortcut), so the Hermes "← Personal" status item is not needed there.
- **Status:** design only; **unverified**: no `WebContentsView` prototype was built; memory cost of keeping both views alive and focus/IME behaviour across views are untested. First prototype, when approved: a shell that opens the two preview URLs and switches visibility, measured for switch time and state survival.

## 5. Next steps (in order)

1. "← Personal" via history traversal (item 1 above), with a focused test in the preview.
2. Native shell prototype against the preview (not before production migration).
3. Cache-eligible Projects only if still needed.

## 6. Evidence
`docs/evidence/navigation/measurements.json` (raw numbers above). The back/forward-cache reasons are Chrome's own `notRestoredReasons` for the Projects entry.
