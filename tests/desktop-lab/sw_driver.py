
import asyncio, json, os, re, shutil, subprocess, tempfile, time
import websockets
FF = r"C:\Program Files\Mozilla Firefox\firefox.exe"; PORT = int(os.environ["PORT"]); WITH_SW = os.environ["WITH_SW"] == "1"; LOG = os.environ["LOG"]
async def main():
    prof = tempfile.mkdtemp(prefix="ffsw-")
    proc = subprocess.Popen([FF, "--headless", "--no-remote", "--profile", prof, "--remote-debugging-port=9339"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try: ws = await websockets.connect("ws://127.0.0.1:9339/session", max_size=None); break
            except Exception: await asyncio.sleep(0.5)
        n = 0; pend = {}; tl = []; T0 = time.time()
        async def pump():
            async for raw in ws:
                m = json.loads(raw)
                if "id" in m:
                    f = pend.pop(m["id"], None)
                    if f: f.set_result(m)
                elif m["method"] == "browsingContext.navigationStarted": tl.append((round(time.time() - T0, 1), m["params"]["url"].replace("ticket=" + "T" * 43, "ticket=<T>")[:70]))
        asyncio.create_task(pump())
        async def call(method, **q):
            nonlocal n
            n += 1; fut = asyncio.get_event_loop().create_future(); pend[n] = fut
            await ws.send(json.dumps({"id": n, "method": method, "params": q})); m = await asyncio.wait_for(fut, 60)
            if m.get("type") == "error": raise RuntimeError(m.get("message"))
            return m["result"]
        async def ev(ctx, expr, aw=False): return (await call("script.evaluate", expression=expr, target={"context": ctx}, awaitPromise=aw))["result"].get("value")
        await call("session.new", capabilities={}); await call("session.subscribe", events=["browsingContext.navigationStarted"])
        ctx = (await call("browsingContext.getTree"))["contexts"][0]["context"]
        if WITH_SW:
            await call("browsingContext.navigate", context=ctx, url=f"http://hermes.localhost:{PORT}/register", wait="complete")
            for _ in range(30):
                await asyncio.sleep(0.5)
                if str(await ev(ctx, "document.title")).startswith("registered:activated"): break
            print("service worker:", await ev(ctx, "document.title"))
        await call("browsingContext.navigate", context=ctx, url=f"http://localhost:{PORT}/odysseus", wait="complete"); await asyncio.sleep(1)
        tl.clear(); T0 = time.time()
        await ev(ctx, "(() => { const l = document.createElement('a'); l.href = 'http://hermes.localhost:%d/__odysseus/enter?ticket=%s'; l.referrerPolicy = 'origin'; l.rel = 'noopener'; document.body.append(l); l.click(); })()" % (PORT, "T" * 43))
        await asyncio.sleep(float(os.environ.get("WAIT", "6")))
        regs = await ev(ctx, "navigator.serviceWorker ? navigator.serviceWorker.getRegistrations().then(r => r.length) : -1", True)
        print("final page:", repr(await ev(ctx, "document.title")), "| url:", (await ev(ctx, "location.href")).replace("T" * 43, "<T>")[:60], "| SW registrations left on this origin:", regs)
        print("navigations:", *[f"\n   {t}s {u}" for t, u in tl])
        await ws.close()
    finally:
        proc.terminate(); time.sleep(1); shutil.rmtree(prof, ignore_errors=True)
asyncio.run(main())
