
import asyncio, json, os, re, shutil, subprocess, tempfile, time
import websockets
FF = r"C:\Program Files\Mozilla Firefox\firefox.exe"; PORT = int(os.environ["ODY_PORT"]); SECRET = os.environ["ODY_SESSION_FILE"]
async def main():
    prof = tempfile.mkdtemp(prefix="ffflow-")
    proc = subprocess.Popen([FF, "--headless", "--no-remote", "--profile", prof, "--remote-debugging-port=9336"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try: ws = await websockets.connect("ws://127.0.0.1:9336/session", max_size=None); break
            except Exception: await asyncio.sleep(0.5)
        n = 0; pend = {}
        async def pump():
            async for raw in ws:
                m = json.loads(raw)
                if "id" in m:
                    f = pend.pop(m["id"], None)
                    if f: f.set_result(m)
        asyncio.create_task(pump())
        async def call(method, **p):
            nonlocal n
            n += 1; fut = asyncio.get_event_loop().create_future(); pend[n] = fut
            await ws.send(json.dumps({"id": n, "method": method, "params": p})); m = await asyncio.wait_for(fut, 90)
            if m.get("type") == "error": raise RuntimeError(f"{method}: {m.get('message')}")
            return m["result"]
        async def ev(ctx, expr, wait=False):
            r = (await call("script.evaluate", expression=expr, target={"context": ctx}, awaitPromise=wait))["result"]
            return r.get("value")
        await call("session.new", capabilities={})
        ctx = (await call("browsingContext.getTree"))["contexts"][0]["context"]
        sess = json.load(open(SECRET))["session"]
        await call("storage.setCookie", cookie={"name": "odysseus_session", "value": {"type": "string", "value": sess}, "domain": "localhost", "path": "/", "httpOnly": True, "sameSite": "lax"})
        # --- optional new tab: the second tab runs the same switch from ?open=projects
        await call("browsingContext.navigate", context=ctx, url=f"http://localhost:{PORT}/", wait="complete"); await asyncio.sleep(4)
        tab = (await call("browsingContext.create", type="tab"))["context"]
        await call("browsingContext.navigate", context=tab, url=f"http://localhost:{PORT}/?open=projects", wait="none")
        ok = False
        for _ in range(90):
            await asyncio.sleep(1)
            u = await ev(tab, "location.href") or ""
            if u.startswith("http://hermes.localhost") and "enter" not in u: ok = True; break
        body = await ev(tab, "(document.body.innerText || '').slice(0, 60)")
        orig = await ev(ctx, "location.href")
        print(f"new tab: reached Hermes={ok} url={u[:45]} page={body!r}\n   original tab untouched: {orig}")
        await asyncio.sleep(3)
        # --- logout: Hermes answers while the Odysseus session lives, and 401 + X-Odysseus-Grant after it ends
        probe = "fetch('/api/status',{credentials:'include',cache:'no-store'}).then(r=>r.status+' grant-header='+r.headers.get('x-odysseus-grant')).catch(e=>'ERR '+e)"
        before = await ev(tab, probe, wait=True)
        out = await ev(ctx, "fetch('/api/auth/logout',{method:'POST',credentials:'same-origin'}).then(r=>r.status)", wait=True)
        await asyncio.sleep(2)
        after = await ev(tab, probe, wait=True)
        await asyncio.sleep(3)
        u2 = await ev(tab, "location.href") or ""
        print(f"logout: Hermes API before={before!r} | Odysseus logout status={out} | Hermes API after={after!r}\n   the Hermes tab then went to: {u2[:45]}")
        await ws.close()
    finally:
        proc.terminate(); time.sleep(1); shutil.rmtree(prof, ignore_errors=True)
asyncio.run(main())
