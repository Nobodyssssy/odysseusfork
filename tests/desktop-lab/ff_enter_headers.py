
import asyncio, json, os, re, subprocess, sys, time, shutil, tempfile
import websockets
FF = r"C:\Program Files\Mozilla Firefox\firefox.exe"
PORT = int(os.environ["PORT"]); PROF = os.environ.get("FF_PROFILE_DIR")
async def main():
    prof = PROF or tempfile.mkdtemp(prefix="ffb2-")
    proc = subprocess.Popen([FF, "--headless", "--no-remote", "--profile", prof, "--remote-debugging-port=9334"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try: ws = await websockets.connect("ws://127.0.0.1:9334/session", max_size=None); break
            except Exception: await asyncio.sleep(0.5)
        n = 0; pend = {}; events = []
        async def pump():
            async for raw in ws:
                m = json.loads(raw)
                if "id" in m:
                    f = pend.pop(m["id"], None)
                    if f: f.set_result(m)
                else: events.append(m)
        asyncio.create_task(pump())
        async def call(method, **p):
            nonlocal n
            n += 1; fut = asyncio.get_event_loop().create_future(); pend[n] = fut
            await ws.send(json.dumps({"id": n, "method": method, "params": p})); m = await asyncio.wait_for(fut, 60)
            if m.get("type") == "error": raise RuntimeError(m.get("message"))
            return m["result"]
        await call("session.new", capabilities={})
        await call("session.subscribe", events=["network.beforeRequestSent", "network.responseStarted"])
        ctx = (await call("browsingContext.getTree"))["contexts"][0]["context"]
        await call("browsingContext.navigate", context=ctx, url=f"http://localhost:{PORT}/login", wait="complete"); await asyncio.sleep(2)
        t0 = time.time()
        js = ("(() => { const l = document.createElement('a'); l.href = 'http://hermes.localhost:%d/__odysseus/enter?ticket=' + 'A'.repeat(43); l.referrerPolicy = 'origin'; l.rel = 'noopener'; document.body.append(l); l.click(); return 'clicked'; })()" % PORT)
        print((await call("script.evaluate", expression=js, target={"context": ctx}, awaitPromise=False))["result"].get("value"))
        await asyncio.sleep(4)
        for m in events:
            p = m.get("params", {}); req = p.get("request", {})
            if "__odysseus/enter" not in req.get("url", ""): continue
            h = {x["name"].lower(): (x["value"].get("value") if isinstance(x["value"], dict) else x["value"]) for x in (p.get("response", {}).get("headers") if "response" in p else req.get("headers")) or []}
            if m["method"] == "network.beforeRequestSent":
                print("REQ  referer=%s sec-fetch=[%s,%s,%s]" % (h.get("referer"), h.get("sec-fetch-site"), h.get("sec-fetch-mode"), h.get("sec-fetch-dest")))
            else: print("RESP", p["response"]["status"])
        body = (await call("script.evaluate", expression="document.body.innerText.slice(0,80)", target={"context": ctx}, awaitPromise=False))["result"].get("value")
        print("page:", repr(body))
    finally:
        proc.terminate(); time.sleep(1)
        if not PROF: shutil.rmtree(prof, ignore_errors=True)
asyncio.run(main())
