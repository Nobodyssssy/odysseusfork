
import asyncio, http.client, json, os, re, shutil, subprocess, tempfile, time
import websockets
FF = r"C:\Program Files\Mozilla Firefox\firefox.exe"; P = 19800; GW = 19130; ODY_PID = os.environ["ODY_PID"]
PV = r"F:\Odysseus-Backups\desktop-lab\odysseus-preview"; cred = json.load(open(PV + r"\lab-admin.secret"))
def login():
    c = http.client.HTTPConnection("127.0.0.1", P, timeout=20); c.request("POST", "/api/auth/login", body=json.dumps({"username": cred["username"], "password": cred["password"]}), headers={"Host": f"localhost:{P}", "Origin": f"http://localhost:{P}", "Content-Type": "application/json"})
    r = c.getresponse(); r.read(); return next(v.split("=", 1)[1].split(";")[0] for k, v in r.getheaders() if k.lower() == "set-cookie" and v.startswith("odysseus_session="))
def conns():
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-NetTCPConnection -State Established -OwningProcess {ODY_PID} -EA SilentlyContinue | ? {{ $_.RemotePort -eq {GW} }}).Count"], capture_output=True, text=True).stdout.strip()
    return int(out or 0)
async def main():
    prof = tempfile.mkdtemp(prefix="ffbf-")
    proc = subprocess.Popen([FF, "--headless", "--no-remote", "--profile", prof, "--remote-debugging-port=9340"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try: ws = await websockets.connect("ws://127.0.0.1:9340/session", max_size=None); break
            except Exception: await asyncio.sleep(0.5)
        n = 0; pend = {}
        async def pump():
            async for raw in ws:
                m = json.loads(raw)
                if "id" in m:
                    f = pend.pop(m["id"], None)
                    if f: f.set_result(m)
        asyncio.create_task(pump())
        async def call(method, **q):
            nonlocal n
            n += 1; fut = asyncio.get_event_loop().create_future(); pend[n] = fut
            await ws.send(json.dumps({"id": n, "method": method, "params": q})); m = await asyncio.wait_for(fut, 90)
            if m.get("type") == "error": raise RuntimeError(f"{method}: {m.get('message')}")
            return m["result"]
        async def ev(expr, aw=False):
            r = (await call("script.evaluate", expression=expr, target={"context": ctx}, awaitPromise=aw))["result"]; return r.get("value")
        await call("session.new", capabilities={}); ctx = (await call("browsingContext.getTree"))["contexts"][0]["context"]
        sess = login()
        await call("storage.setCookie", cookie={"name": "odysseus_session", "value": {"type": "string", "value": sess}, "domain": "localhost", "path": "/", "httpOnly": True, "sameSite": "lax"})
        def red(u): return re.sub(r"ticket=[A-Za-z0-9_-]+", "ticket=<T>", u or "")
        async def where(label, wait=0):
            await asyncio.sleep(wait)
            u = await ev("location.href"); b = await ev("(document.body && document.body.innerText || '').slice(0,50).replace(/\\n/g,' | ')"); nt = await ev("(performance.getEntriesByType('navigation')[0]||{}).type")
            print(f"{label:46} url={red(u)[:52]:52} nav={nt} gateway-conns={conns()} page={b!r}"); return u
        await call("browsingContext.navigate", context=ctx, url=f"http://localhost:{P}/", wait="complete"); await where("1 Personal opened", 4)
        await ev("(() => { const s = document.getElementById('application-mode-selector'); s.focus(); return 1 })()")
        await call("input.performActions", context=ctx, actions=[{"type": "key", "id": "k", "actions": [{"type": "keyDown", "value": "\ue015"}, {"type": "keyUp", "value": "\ue015"}]}])
        for _ in range(60):
            await asyncio.sleep(1)
            if "hermes.localhost" in (await ev("location.href") or ""): break
        await where("2 Projects (Hermes Webapp) opened", 12)
        # Back via the Personal chip (real click)
        r = (await call("script.evaluate", expression="(() => { const a = [...document.querySelectorAll('a')].find(n => /personal/i.test(n.title + n.getAttribute('aria-label') + n.textContent)); if (!a) return null; const r = a.getBoundingClientRect(); return (r.x + r.width / 2) + ',' + (r.y + r.height / 2); })()", target={"context": ctx}, awaitPromise=False))["result"]
        x, y = [int(float(v)) for v in r["value"].split(",")]
        await call("input.performActions", context=ctx, actions=[{"type": "pointer", "id": "m", "parameters": {"pointerType": "mouse"}, "actions": [{"type": "pointerMove", "x": x, "y": y}, {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0}]}])
        await where("3 Personal chip clicked -> Personal", 5)
        await call("browsingContext.traverseHistory", context=ctx, delta=1); await where("4 history FORWARD -> Hermes (session still live)", 8)
        await call("browsingContext.traverseHistory", context=ctx, delta=-1); await where("5 history BACK -> Personal", 5)
        out = await ev("fetch('/api/auth/logout',{method:'POST',credentials:'same-origin'}).then(r => r.status)", True); print(f"6 logout from the Personal page: HTTP {out}")
        await asyncio.sleep(2)
        await call("browsingContext.traverseHistory", context=ctx, delta=1)
        u = await where("7 history FORWARD -> Hermes after LOGOUT (t+3s)", 3)
        u = await where("   ... same tab, t+15 s", 12)
        probe = await ev("fetch('/api/status',{credentials:'include',cache:'no-store'}).then(r => r.status + ' grant=' + r.headers.get('x-odysseus-grant')).catch(e => 'ERR')", True) if "hermes.localhost" in u else "(tab already left Hermes)"
        print("   Hermes API from this tab now:", probe)
        await call("browsingContext.traverseHistory", context=ctx, delta=-1); u = await where("8 history BACK -> Personal page after logout", 4)
        await call("browsingContext.navigate", context=ctx, url=f"http://localhost:{P}/", wait="complete"); await where("9 open Odysseus root after logout", 3)
        await ws.close()
    finally:
        proc.terminate(); time.sleep(1); shutil.rmtree(prof, ignore_errors=True)
asyncio.run(main())
