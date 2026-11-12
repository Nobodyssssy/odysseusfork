
import asyncio, json, os, re, shutil, subprocess, tempfile, time
import websockets
FF = r"C:\Program Files\Mozilla Firefox\firefox.exe"
PORT = int(os.environ["ODY_PORT"]); SCEN = os.environ["SCEN"]; SECRET = os.environ["ODY_SESSION_FILE"]; PROF = os.environ.get("FF_PROFILE_DIR"); REAL = os.environ.get("REAL_INPUT")
def red(t): return re.sub(r"ticket=[A-Za-z0-9_\-]+", "ticket=<T>", t)
async def main():
    prof = PROF or tempfile.mkdtemp(prefix="ffheal-")
    proc = subprocess.Popen([FF, "--headless", "--no-remote", "--profile", prof, "--remote-debugging-port=9335"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try: ws = await websockets.connect("ws://127.0.0.1:9335/session", max_size=None); break
            except Exception: await asyncio.sleep(0.5)
        n = 0; pend = {}; events = []; enters = []; timeline = []; T0 = time.time()
        async def call(method, **p):
            nonlocal n
            n += 1; fut = asyncio.get_event_loop().create_future(); pend[n] = fut
            await ws.send(json.dumps({"id": n, "method": method, "params": p})); m = await asyncio.wait_for(fut, 90)
            if m.get("type") == "error": raise RuntimeError(f"{method}: {m.get('message')}")
            return m["result"]
        async def pump():
            async for raw in ws:
                m = json.loads(raw)
                if "id" in m:
                    f = pend.pop(m["id"], None)
                    if f: f.set_result(m)
                    continue
                ev = m.get("method"); p = m.get("params", {})
                if ev in ("browsingContext.navigationStarted", "browsingContext.load", "network.responseStarted", "network.beforeRequestSent"):
                    u_ = (p.get("url") or p.get("request", {}).get("url") or "")
                    if ev.startswith("browsingContext") or re.search(r"__odysseus/enter|/webapp/launch|localhost:\d+/(\?open=projects)?$|projects/launch", u_):
                        timeline.append((round(time.time() - T0, 1), ev.split(".")[1], red(u_)[:80], (p.get("response") or {}).get("status")))
                if ev == "network.beforeRequestSent" and "__odysseus/enter" in p.get("request", {}).get("url", ""):
                    h = {x["name"].lower(): x["value"]["value"] for x in p["request"].get("headers", [])}
                    enters.append({"n": len(enters) + 1, "url": red(p["request"]["url"]), "referer": h.get("referer"), "blocked": p.get("isBlocked"), "id": p["request"]["request"], "headers": p["request"].get("headers", []), "raw_url": p["request"]["url"]})
                    if p.get("isBlocked"): asyncio.create_task(decide(enters[-1]))
                elif ev == "network.responseStarted" and "__odysseus/enter" in p.get("request", {}).get("url", ""):
                    for e in reversed(enters):
                        if e["id"] == p["request"]["request"]: e["status"] = p["response"]["status"]; break
        async def decide(e):
            # SCEN strip-first: the first click navigation loses its Referer (what a privacy extension does to initiator-bearing navigations)
            # SCEN strip-always: every enter request loses it (a deterministic cause: the recovery must stop, not loop)
            # SCEN spent-first: the first request carries an already-spent ticket (the ticket-level refusal)
            first = e["n"] == 1
            kw = {}
            if SCEN == "strip-always" or (SCEN == "strip-first" and first):
                kw["headers"] = [h for h in e["headers"] if h["name"].lower() != "referer"]
            if SCEN == "spent-two" and e["n"] <= 2:
                kw["url"] = re.sub(r"ticket=[A-Za-z0-9_\-]+", "ticket=" + "A" * 43, e["raw_url"])
            if SCEN == "spent-first" and first:
                kw["url"] = re.sub(r"ticket=[A-Za-z0-9_\-]+", "ticket=" + "A" * 43, e["raw_url"])
            e["altered"] = sorted(kw)
            await call("network.continueRequest", request=e["id"], **kw)
        asyncio.create_task(pump())
        await call("session.new", capabilities={})
        await call("session.subscribe", events=["network.beforeRequestSent", "network.responseStarted", "browsingContext.navigationStarted", "browsingContext.load", "browsingContext.historyUpdated"])
        await call("network.addIntercept", phases=["beforeRequestSent"], urlPatterns=[{"type": "pattern", "protocol": "http", "hostname": "hermes.localhost", "port": str(PORT), "pathname": "/__odysseus/enter"}])
        ctx = (await call("browsingContext.getTree"))["contexts"][0]["context"]
        sess = json.load(open(SECRET))["session"]
        await call("storage.setCookie", cookie={"name": "odysseus_session", "value": {"type": "string", "value": sess}, "domain": "localhost", "path": "/", "httpOnly": True, "sameSite": "lax"})
        await call("browsingContext.navigate", context=ctx, url=f"http://localhost:{PORT}/", wait="complete"); await asyncio.sleep(5)
        async def ev(expr): return (await call("script.evaluate", expression=expr, target={"context": ctx}, awaitPromise=False))["result"].get("value")
        t0 = time.time()
        await ev("(() => { const s = document.getElementById('application-mode-selector'); s.focus(); return 1; })()")
        await call("input.performActions", context=ctx, actions=[{"type": "key", "id": "k", "actions": [{"type": "keyDown", "value": "\ue015"}, {"type": "keyUp", "value": "\ue015"}]}])
        final = ""
        for _ in range(int(os.environ.get("WAIT", "60"))):
            await asyncio.sleep(1)
            u = await ev("location.href") or ""
            body = await ev("(document.body && document.body.innerText || '').slice(0,90)") or ""
            if ("hermes.localhost" in u and "/__odysseus/enter" not in u and "Opening" not in body and "Could not" not in body) or ("Could not open Hermes" in body and u.startswith("http://hermes.localhost") and not re.search(r"Opening", body)):
                final = (u, body); break
        await asyncio.sleep(int(os.environ.get("SETTLE", "2")))
        u = await ev("location.href"); body = await ev("(document.body && document.body.innerText || '').slice(0,110)")
        print(f"[{SCEN}] after {round(time.time()-t0)} s: url={red(u)[:70]}\n   page={body!r}")
        for e in enters: print(f"   enter request {e['n']}: referer-sent={e['referer']!r} altered={e.get('altered')} -> status {e.get('status')}")
        print(f"   total enter requests: {len(enters)}")
        print("   timeline:"); [print("     ", x) for x in timeline if x[1] != "load" or "localhost" in x[2]]
        await ws.close()
    finally:
        proc.terminate(); time.sleep(1)
        if not PROF: shutil.rmtree(prof, ignore_errors=True)
asyncio.run(main())
