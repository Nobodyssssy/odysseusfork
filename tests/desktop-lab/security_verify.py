
import asyncio, http.client, json, os, re, socket, sys, time, subprocess
import websockets
P = 19800; H = f"hermes.localhost:{P}"; O = f"localhost:{P}"
PV = r"F:\Odysseus-Backups\desktop-lab\odysseus-preview"
R = r"F:\Odysseus\integration-backups\webapp-proxy"
spec = __import__("importlib.util").util.spec_from_file_location("pvmod", r"F:\Odysseus-Backups\desktop-lab\tools\preview.py"); pvmod = __import__("importlib.util").util.module_from_spec(spec); spec.loader.exec_module(pvmod)
LABCRED = json.load(open(PV + r"\lab-admin.secret"))
LOGIN_SET_COOKIES = []
def mint():
    body = json.dumps({"username": LABCRED["username"], "password": LABCRED["password"]})
    c = http.client.HTTPConnection("127.0.0.1", P, timeout=20)
    c.request("POST", "/api/auth/login", body=body, headers={"Host": f"localhost:{P}", "Origin": f"http://localhost:{P}", "Content-Type": "application/json"})
    r = c.getresponse(); r.read()
    if r.status != 200: raise SystemExit(f"preview login failed: {r.status}")
    cookies = [v for k, v in r.getheaders() if k.lower() == "set-cookie"]; LOGIN_SET_COOKIES.extend(cookies)
    return next(v.split("=", 1)[1].split(";")[0] for v in cookies if v.startswith("odysseus_session="))
def req(host, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", P, timeout=15); h = {"Host": host}; h.update(headers or {})
    c.request(method, path, body=body, headers=h); r = c.getresponse(); data = r.read()
    return r.status, [(k.lower(), v) for k, v in r.getheaders()], data
def hv(hdrs, name): return [v for k, v in hdrs if k == name]
NAV = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "cross-site", "Referer": f"http://localhost:{P}/"}
results = []
def rec(area, name, ok, detail=""): results.append((area, name, "PASS" if ok else "FAIL", detail)); print(("PASS " if ok else "FAIL "), f"[{area}] {name}", ("| " + detail) if detail else "", flush=True)
def launch(session, origin=f"http://localhost:{P}"):
    return req(O, "POST", "/api/application-modes/projects/launch", {"Cookie": "odysseus_session=" + session, "Origin": origin, "Content-Length": "0"}, b"")
def ticket_of(body):
    u = json.loads(body)["url"]; return re.search(r"ticket=([A-Za-z0-9_-]{43})$", u).group(1), u
def enter(ticket, extra=None, host=H):
    h = dict(NAV); h.update(extra or {}); return req(host, "GET", "/__odysseus/enter?ticket=" + ticket, h)
def grant_cookie(hdrs):
    for v in hv(hdrs, "set-cookie"):
        if v.startswith("odysseus_hermes_grant="): return v
    return None
async def main():
    S1 = mint()
    low = " ".join(LOGIN_SET_COOKIES).lower()
    rec("cookie", "Odysseus session cookie: HttpOnly, SameSite, Path=/ (login response)", "httponly" in low and "samesite=" in low and "path=/" in low, re.sub(r"=[A-Za-z0-9_-]{20,}", "=<C>", " ".join(LOGIN_SET_COOKIES))[:160])
    # ---- launch route
    st, h, b = launch(S1); rec("ticket", "launch with a live session returns an opaque ticket URL", st == 200 and re.search(r"^http://hermes\.localhost:%d/__odysseus/enter\?ticket=[A-Za-z0-9_-]{43}$" % P, json.loads(b)["url"]) is not None, f"status {st}")
    tk, url = ticket_of(b)
    rec("ticket", "the Hermes launch link (a gateway credential) is not in the browser-visible URL", "launch" not in url.lower() and "token" not in url.lower().replace("ticket", ""))
    st2, _, _ = req(O, "POST", "/api/application-modes/projects/launch", {"Origin": f"http://localhost:{P}", "Content-Length": "0"}, b""); rec("auth", "launch without an Odysseus session is refused", st2 in (401, 403), f"status {st2}")
    st3, _, _ = launch(S1, origin="http://evil.test"); rec("origin", "launch from a foreign Origin is refused", st3 in (401, 403), f"status {st3}")
    st4, _, _ = req(O, "POST", "/api/application-modes/projects/launch", {"Cookie": "odysseus_session=" + "A" * 43, "Origin": f"http://localhost:{P}", "Content-Length": "0"}, b""); rec("auth", "launch with a forged session cookie is refused", st4 in (401, 403), f"status {st4}")
    # ---- redemption
    st, h, b = enter(tk); ck = grant_cookie(h)
    rec("ticket", "valid ticket redeems once: 303 to the server-held target", st == 303 and hv(h, "location") and hv(h, "location")[0].startswith("/webapp/launch"), f"status {st}")
    flags = ck or ""; low = flags.lower()
    rec("cookie", "grant cookie: HttpOnly, SameSite=Lax, Path=/, no Domain, Max-Age set", all(x in low for x in ("httponly", "samesite=lax", "path=/", "max-age=")) and "domain=" not in low, re.sub(r"=[A-Za-z0-9_-]{20,}", "=<C>", flags)[:120])
    rec("cookie", "grant cookie has no Secure flag (plain http on loopback; Secure cookies would not be sent)", "secure" not in low, "NOTE: this is expected over http://*.localhost; the cookie is host-only and loopback-only")
    rec("ticket", "response is no-store / no-referrer and does not echo the ticket", hv(h, "cache-control") == ["no-store"] and hv(h, "referrer-policy") == ["no-referrer"] and tk not in json.dumps(h))
    secret = ck.split("=", 1)[1].split(";")[0]
    st, h, b = enter(tk); rec("ticket", "replay of the same ticket is refused, no cookie issued", st == 403 and not grant_cookie(h), f"status {st}")
    # ---- unauthorized access through the proxy
    st, h, b = req(H, "GET", "/api/status"); rec("auth", "Hermes API without a grant: 401 + X-Odysseus-Grant: required", st == 401 and hv(h, "x-odysseus-grant") == ["required"], f"status {st}")
    st, h, b = req(H, "GET", "/api/status", {"Cookie": "odysseus_hermes_grant=" + "A" * 43}); rec("auth", "Hermes API with a forged grant cookie: 401", st == 401, f"status {st}")
    st, h, b = req(H, "GET", "/api/status", {"Cookie": "odysseus_hermes_grant=" + secret}); rec("auth", "Hermes API with the real grant cookie: 200", st == 200, f"status {st}")
    st, h, b = req(H, "GET", "/api/status", {"Cookie": f"odysseus_hermes_grant={secret}; odysseus_hermes_grant={'A'*43}"}); rec("auth", "duplicate/conflicting grant cookies (cookie tossing): 401", st == 401, f"status {st}")
    st, h, b = req("evil.test:%d" % P, "GET", "/api/status", {"Cookie": "odysseus_hermes_grant=" + secret}); rec("host", "foreign Host header with a valid grant never reaches Hermes", not (st == 200 and b.lstrip().startswith(b"{") and b'"ok"' in b[:60] and b"hermes" in b.lower()), f"status {st}")
    for name, value in (("X-Forwarded-For", "1.2.3.4"), ("Forwarded", "for=1.2.3.4"), ("X-Forwarded-Host", "evil.test")):
        st, h, b = req(H, "GET", "/api/status", {"Cookie": "odysseus_hermes_grant=" + secret, name: value}); rec("host", f"forwarding header {name} is refused", st == 403, f"status {st}")
    st, h, b = req(H, "POST", "/api/echo-nothing", {"Cookie": "odysseus_hermes_grant=" + secret, "Origin": "http://evil.test", "Content-Length": "0"}, b""); rec("origin", "POST from a foreign Origin is refused", st == 403, f"status {st}")
    st, h, b = req(H, "POST", "/api/echo-nothing", {"Cookie": "odysseus_hermes_grant=" + secret, "Origin": f"http://{H}", "Sec-Fetch-Site": "cross-site", "Content-Length": "0"}, b""); rec("origin", "cross-site POST (Sec-Fetch-Site) is refused", st == 403, f"status {st}")
    s = socket.create_connection(("127.0.0.1", P), 5); s.sendall(f"GET /api/status HTTP/1.1\r\nHost: {H}\r\nHost: evil.test\r\nConnection: close\r\n\r\n".encode()); first = s.recv(200).split(b"\r\n")[0].decode(); s.close()
    rec("host", "ambiguous (duplicate) Host header is refused", " 400 " in first or " 403 " in first, first)
    # enter route: wrong method / wrong fetch metadata / extra params
    st, _, _ = req(H, "POST", "/__odysseus/enter?ticket=" + "A" * 43, dict(NAV, **{"Content-Length": "0"}), b""); rec("ticket", "POST to the enter route is refused", st in (403, 405), f"status {st}")
    S0 = mint(); st, h, b = launch(S0); t0, _ = ticket_of(b); st, _, _ = req(H, "GET", f"/__odysseus/enter?ticket={t0}&next=/x", NAV); rec("ticket", "extra query parameter (client-chosen target) is refused", st == 403, f"status {st}")
    st, _, _ = enter(t0, {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}); rec("ticket", "a fetch() instead of a navigation cannot redeem (the old-service-worker case)", st == 403, f"status {st}")
    st, _, _ = enter(t0, {"Referer": "http://evil.test/"}); rec("ticket", "a foreign Referer cannot redeem", st == 403, f"status {st}")
    st, h, _ = enter(t0); rec("ticket", "refused attempts before the ticket is read do not consume it", st == 303, f"status {st}")
    # ---- expiry and capacity
    st, h, b = launch(S0); te, _ = ticket_of(b); time.sleep(32); st, _, _ = enter(te); rec("ticket", "a ticket older than 30 s is refused", st == 403, f"status {st}")
    S9 = mint(); codes = []
    for _ in range(10): st, h, b = launch(S9); codes.append(st)
    rec("ticket", "at most 8 live tickets per session (9th refused)", codes[:8] == [200] * 8 and 200 not in codes[8:], f"statuses {codes}")
    # ---- websocket
    ws_url = f"ws://{H}/api/ws"
    async def wsconnect(cookie=None, origin=f"http://{H}"):
        try:
            c = await websockets.connect(ws_url, host="127.0.0.1", port=P, origin=origin, additional_headers=({"Cookie": cookie} if cookie else None), max_size=None, open_timeout=10)
            return c, None
        except Exception as e: return None, type(e).__name__ + " " + str(e)[:80]
    c, err = await wsconnect(None); rec("websocket", "WebSocket without a grant is rejected", c is None, err or "connected!")
    if c: await c.close()
    c, err = await wsconnect("odysseus_hermes_grant=" + "A" * 43); rec("websocket", "WebSocket with a forged grant is rejected", c is None, err or "connected!")
    if c: await c.close()
    c, err = await wsconnect("odysseus_hermes_grant=" + secret, origin="http://evil.test"); rec("websocket", "WebSocket with a valid grant but a foreign Origin is rejected", c is None, err or "connected!")
    if c: await c.close()
    c, err = await wsconnect("odysseus_hermes_grant=" + secret, origin=None); rec("websocket", "WebSocket with a valid grant and no Origin is rejected", c is None, err or "connected!")
    if c: await c.close()
    try:
        c = await websockets.connect(f"ws://{H}/__odysseus/enter?ticket=" + "A" * 43, host="127.0.0.1", port=P, origin=f"http://{H}", max_size=None, open_timeout=10); await c.close(); okws = False
    except Exception: okws = True
    rec("websocket", "the enter route is not a WebSocket endpoint", okws)
    live, err = await wsconnect("odysseus_hermes_grant=" + secret); rec("websocket", "WebSocket with a valid grant and the right Origin connects", live is not None, err or "")
    # ---- logout ends everything, including the live socket and a fresh ticket
    sT = mint(); st, h, b = launch(sT); tT, _ = ticket_of(b)          # a ticket minted but never redeemed ...
    st, h, b = req(O, "POST", "/api/auth/logout", {"Cookie": "odysseus_session=" + S1, "Origin": f"http://localhost:{P}", "Content-Length": "0"}, b""); rec("logout", "Odysseus logout succeeds", st == 200, f"status {st}")
    await asyncio.sleep(2)
    st, h, b = req(H, "GET", "/api/status", {"Cookie": "odysseus_hermes_grant=" + secret}); rec("logout", "the grant stops working at once after logout", st == 401, f"status {st}")
    closed = False
    if live:
        try: await asyncio.wait_for(live.recv(), 8)
        except websockets.exceptions.ConnectionClosed as e: closed = True; closed_info = f"code {e.rcvd.code if e.rcvd else None}"
        except asyncio.TimeoutError: closed_info = "still open after 8 s"
        except Exception as e: closed = True; closed_info = type(e).__name__
    rec("logout", "a live WebSocket is closed when the session ends", closed, closed_info if live else "no socket")
    S1b = mint(); st, h, b = req(H, "GET", "/api/status", {"Cookie": "odysseus_hermes_grant=" + secret}); rec("logout", "logging in again does not revive the old grant", st == 401, f"status {st}")
    st, _, _ = launch(S1); rec("logout", "the logged-out session can no longer mint a ticket", st in (401, 403), f"status {st}")
    req(O, "POST", "/api/auth/logout", {"Cookie": "odysseus_session=" + sT, "Origin": f"http://localhost:{P}", "Content-Length": "0"}, b""); st, _, _ = enter(tT); rec("logout", "a ticket minted before logout cannot be redeemed after it", st == 403, f"status {st}")
    # ---- the direct gateway port (known, documented)
    c = http.client.HTTPConnection("127.0.0.1", 19130, timeout=8); c.request("GET", "/api/status"); r = c.getresponse(); r.read(); rec("direct-port", "gateway answers on its own loopback port WITHOUT any grant (known same-user limit)", r.status != 200, f"direct GET /api/status -> {r.status}: the proxy gate does not protect this port")
    json.dump(results, open(r"F:\Odysseus-Backups\desktop-lab\results\security-verification.json", "w"), indent=1)
    print("\nSUMMARY:", {k: sum(1 for r in results if r[2] == k) for k in ("PASS", "FAIL")})
asyncio.run(main())
