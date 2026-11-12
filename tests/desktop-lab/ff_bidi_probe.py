
import asyncio, json, os, re, shutil, subprocess, sys, tempfile, time
import websockets

FF = r"C:\Program Files\Mozilla Firefox\firefox.exe"
PORT = int(os.environ.get("ODY_PORT", "19800"))
CYCLES = int(os.environ.get("CYCLES", "1"))
SECRET = os.environ["ODY_SESSION_FILE"]
OUT = os.environ.get("OUT", "")
EXTRA_PREFS = os.environ.get("FF_PREFS", "")
MODE = os.environ.get("MODE", "switch")      # switch | newtab

def redact(text):
    text = re.sub(r"ticket=[A-Za-z0-9_\-]+", "ticket=<T>", text)
    text = re.sub(r"(odysseus_session|hermes_grant|odysseus_hg|[A-Za-z_]*grant[A-Za-z_]*)=[A-Za-z0-9_\-%.]{8,}", r"\1=<C>", text)
    return text

class Bidi:
    def __init__(self, ws):
        self.ws = ws; self.n = 0; self.pending = {}; self.events = []; self.waiters = []
    async def pump(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m:
                f = self.pending.pop(m["id"], None)
                if f: f.set_result(m)
            else:
                self.events.append((time.time(), m))
    async def call(self, method, **params):
        self.n += 1; fut = asyncio.get_event_loop().create_future(); self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        m = await asyncio.wait_for(fut, 60)
        if m.get("type") == "error": raise RuntimeError(f"{method}: {m.get('error')} {m.get('message')}")
        return m["result"]


async def key(b, ctx, *keys):
    acts = []
    for k in keys:
        acts += [{"type": "keyDown", "value": k}, {"type": "keyUp", "value": k}]
    await b.call("input.performActions", context=ctx, actions=[{"type": "key", "id": "kbd", "actions": acts}])

async def click_at(b, ctx, expr):
    r = (await b.call("script.evaluate", expression=expr, target={"context": ctx}, awaitPromise=False))["result"]
    if r.get("type") != "string": return False
    x, y = [int(float(v)) for v in r["value"].split(",")]
    await b.call("input.performActions", context=ctx, actions=[{"type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"}, "actions": [
        {"type": "pointerMove", "x": x, "y": y}, {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0}]}])
    return True

def hdr(h):
    return {x["name"].lower(): (x["value"].get("value") if isinstance(x.get("value"), dict) else x.get("value")) for x in h or []}

def summarize(events, since, stage):
    rows = []
    for ts, m in events:
        if ts < since or not m.get("method", "").startswith("network."): continue
        p = m["params"]; req = p.get("request", {}); url = req.get("url", "")
        dest = req.get("destination") or ""
        if not ("/projects/launch" in url or "__odysseus/enter" in url or (dest == "document") or (m["method"] == "network.fetchError")): continue
        if m["method"] == "network.beforeRequestSent":
            h = hdr(req.get("headers"))
            rows.append(f"  REQ  {req.get('method')} {redact(url)[:110]} | referer={h.get('referer')} sec-fetch=[{h.get('sec-fetch-site')},{h.get('sec-fetch-mode')},{h.get('sec-fetch-dest')},{h.get('sec-fetch-user')}] cookies={sorted(c.split('=')[0] for c in (h.get('cookie') or '').split('; ') if c)} initiator={p.get('initiator',{}).get('type')}")
        elif m["method"] == "network.responseStarted":
            r = p.get("response", {}); h = hdr(r.get("headers"))
            sc = [x["value"]["value"].split("=")[0] for x in r.get("headers", []) if x["name"].lower() == "set-cookie"]
            rows.append(f"  RESP {r.get('status')} {redact(url)[:90]} | location={redact(h.get('location','') or '')[:70]} set-cookie={sc} cache-control={h.get('cache-control')} fromCache={r.get('fromCache')}")
        elif m["method"] == "network.fetchError":
            rows.append(f"  ERR  {redact(url)[:90]} {p.get('errorText')}")
    print(f"--- {stage}"); print("\n".join(rows) if rows else "  (no matching requests)")

async def run():
    given = os.environ.get("FF_PROFILE_DIR")
    prof = given or tempfile.mkdtemp(prefix="ffbidi-")
    open(os.path.join(prof, "user.js"), "w").write('user_pref("browser.shell.checkDefaultBrowser", false);\nuser_pref("datareporting.policy.dataSubmissionEnabled", false);\n' + EXTRA_PREFS)
    proc = subprocess.Popen([FF, "--headless", "--no-remote", "--profile", prof, "--remote-debugging-port=9333"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                ws = await websockets.connect("ws://127.0.0.1:9333/session", max_size=None); break
            except Exception: await asyncio.sleep(0.5)
        b = Bidi(ws); pump = asyncio.create_task(b.pump())
        caps = await b.call("session.new", capabilities={"alwaysMatch": {"webSocketUrl": True}} if False else {})
        print("browser:", caps["capabilities"].get("browserName"), caps["capabilities"].get("browserVersion"))
        await b.call("session.subscribe", events=["network.beforeRequestSent", "network.responseStarted", "network.fetchError", "browsingContext.load", "browsingContext.navigationStarted"])
        for path in [p for p in os.environ.get("EXT_PATHS", "").split("|") if p]:
            try:
                await b.call("webExtension.install", extensionData={"type": "archivePath" if path.endswith(".xpi") else "path", "path": path})
            except Exception as exc:
                print("install failed:", os.path.basename(path), str(exc)[:100])
        if os.environ.get("EXT_PATHS"): await asyncio.sleep(8)
        tree = await b.call("browsingContext.getTree"); ctx = tree["contexts"][0]["context"]
        sess = json.load(open(SECRET))["session"]
        await b.call("storage.setCookie", cookie={"name": "odysseus_session", "value": {"type": "string", "value": sess}, "domain": "localhost", "path": "/", "httpOnly": True, "sameSite": "lax"})
        await b.call("browsingContext.navigate", context=ctx, url=f"http://localhost:{PORT}/", wait="complete")
        await asyncio.sleep(6)
        if MODE == "inspect":
            js_ = "JSON.stringify({attrs: [...document.documentElement.attributes].map(a => a.name + '=' + String(a.value).slice(0, 20)), headStyles: [...document.querySelectorAll('style,link')].map(n => (n.className || n.id || n.tagName)).filter(x => /dark|ublock|ubo|privacy|decentra|localcdn|tamper|bitwarden/i.test(x)).slice(0, 12), globals: Object.keys(window).filter(k => /dark|ublock|tamper|bitwarden|clearurls|privacy/i.test(k)).slice(0, 10)})"
            print("INSPECT", (await b.call("script.evaluate", expression=js_, target={"context": ctx}, awaitPromise=False))["result"].get("value"))
            await ws.close(); return
        for cycle in range(1, CYCLES + 1):
            t0 = time.time()
            if os.environ.get("REAL_INPUT"):
                await b.call("script.evaluate", expression="(() => { const s = document.getElementById('application-mode-selector'); s.focus(); return s.value + ' options=' + [...s.options].map(o => o.value).join('/'); })()", target={"context": ctx}, awaitPromise=False)
                await key(b, ctx, "\ue015")                      # ArrowDown: a trusted keystroke changes the closed <select> and fires change
                print(f"cycle {cycle}: trigger -> real ArrowDown on the mode selector")
            else:
                r = await b.call("script.evaluate", expression="(() => { const s = document.getElementById('application-mode-selector'); if (!s) return 'NO SELECTOR'; s.value = 'hermes'; s.dispatchEvent(new Event('change')); return 'ok ' + location.href; })()", target={"context": ctx}, awaitPromise=False)
                print(f"cycle {cycle}: trigger ->", r["result"].get("value"))
            ok = False
            for _ in range(240):
                await asyncio.sleep(0.5)
                u = (await b.call("script.evaluate", expression="location.href", target={"context": ctx}, awaitPromise=False))["result"].get("value", "")
                if "hermes.localhost" in u: ok = True; break
            await asyncio.sleep(4)
            u = (await b.call("script.evaluate", expression="location.href", target={"context": ctx}, awaitPromise=False))["result"].get("value", "")
            body = (await b.call("script.evaluate", expression="(document.body && document.body.innerText || '').slice(0,160)", target={"context": ctx}, awaitPromise=False))["result"].get("value", "")
            print(f"cycle {cycle}: reached hermes host={ok} url={redact(u)[:100]} | body={body!r}")
            summarize(b.events, t0, f"cycle {cycle} requests")
            if "Could not open Hermes" in body:
                print(f"cycle {cycle}: FAILED page shown; now plain reload (F5)")
                t1 = time.time(); await b.call("browsingContext.reload", context=ctx, wait="complete"); await asyncio.sleep(4)
                u = (await b.call("script.evaluate", expression="location.href", target={"context": ctx}, awaitPromise=False))["result"].get("value", "")
                body = (await b.call("script.evaluate", expression="(document.body && document.body.innerText || '').slice(0,100)", target={"context": ctx}, awaitPromise=False))["result"].get("value", "")
                print(f"after reload: url={redact(u)[:90]} body={body!r}"); summarize(b.events, t1, "after reload")
            if "Could not open Hermes" in body:
                await b.call("browsingContext.navigate", context=ctx, url=f"http://localhost:{PORT}/", wait="complete"); await asyncio.sleep(3)
            else:
                t2 = time.time()
                nav = (await b.call("script.evaluate", expression="String(typeof window.navigation) + ' entries=' + (window.navigation ? window.navigation.entries().length : '-') + ' idx=' + (window.navigation ? window.navigation.currentEntry.index : '-') + ' fromPersonal=' + sessionStorage.getItem('odysseus.fromPersonal')", target={"context": ctx}, awaitPromise=False))["result"].get("value")
                print(f"cycle {cycle}: in Hermes tab: navigation api {nav}")
                if os.environ.get("REAL_INPUT"):
                    ok_ = await click_at(b, ctx, "(() => { const a = [...document.querySelectorAll('a')].find(n => /personal/i.test(n.title + n.getAttribute('aria-label') + n.textContent)); if (!a) return null; const r = a.getBoundingClientRect(); return (r.x + r.width / 2) + ',' + (r.y + r.height / 2); })()")
                    print(f"cycle {cycle}: real click on the Personal chip found={ok_}")
                else:
                    await b.call("script.evaluate", expression="(() => { const n = window.navigation ? window.navigation.currentEntry.index + 1 : 1; history.go(-n); return n; })()", target={"context": ctx}, awaitPromise=False)
                for _ in range(40):
                    await asyncio.sleep(0.5)
                    u = (await b.call("script.evaluate", expression="location.href", target={"context": ctx}, awaitPromise=False))["result"].get("value", "")
                    if u.startswith(f"http://localhost:{PORT}"): break
                persisted = (await b.call("script.evaluate", expression="(() => { const e = performance.getEntriesByType('navigation')[0]; return e ? e.type : 'n/a'; })()", target={"context": ctx}, awaitPromise=False))["result"].get("value")
                sel = (await b.call("script.evaluate", expression="(() => { const s = document.getElementById('application-mode-selector'); return s ? s.value + ' disabled=' + s.disabled : 'NO SELECTOR'; })()", target={"context": ctx}, awaitPromise=False))["result"].get("value")
                print(f"cycle {cycle}: back on {u} nav-type={persisted} selector={sel}")
                await asyncio.sleep(3)
        await ws.close()
    finally:
        proc.terminate(); time.sleep(1)
        if not given: shutil.rmtree(prof, ignore_errors=True)

asyncio.run(run())
