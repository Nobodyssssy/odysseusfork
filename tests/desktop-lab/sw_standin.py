
"""Stand-in for the Hermes host proxy, to test the stale-service-worker problem in a real browser. It makes the same two decisions the real proxy makes on
/__odysseus/enter before it looks at a ticket (Sec-Fetch navigate/document, Referer = the Odysseus origin) and serves the REAL refusal page from
src/hermes_proxy.py. /sw.js is the old Hermes WebUI's real service worker. Port N serves both hosts: localhost:N (Odysseus stand-in) and hermes.localhost:N."""
import http.server, json, os, socketserver, sys, threading
sys.path.insert(0, os.environ["APP"])
from src import hermes_proxy as hp
PORT = int(os.environ["PORT"]); LOG = os.environ["LOG"]
SW = open(r"F:\hermes-webui\static\sw.js", encoding="utf-8").read().replace("__WEBUI_VERSION__", "exp-test")
def log(rec):
    with open(LOG, "a", encoding="utf-8") as f: f.write(json.dumps(rec) + "\n")
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def send(self, status, body, ctype="text/html; charset=utf-8", extra=()):
        if isinstance(body, str): body = body.encode()
        self.send_response(status); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        for k, v in extra: self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]; path = self.path.split("?")[0]; h = self.headers
        if path == "/sw.js": return self.send(200, SW, "application/javascript")
        if host == "hermes.localhost" and path == "/register":
            return self.send(200, "<!doctype html><title>reg</title><script>navigator.serviceWorker.register('/sw.js',{scope:'/'}).then(()=>navigator.serviceWorker.ready).then(r=>{document.title='registered:'+r.active.state})</script>")
        if host == "localhost" and path == "/odysseus":
            return self.send(200, "<!doctype html><title>odysseus</title><body>personal</body>")
        if host == "hermes.localhost" and path == "/__odysseus/enter":
            mode, dest, site, ref = h.get("Sec-Fetch-Mode"), h.get("Sec-Fetch-Dest"), h.get("Sec-Fetch-Site"), h.get("Referer")
            ok = mode == "navigate" and dest == "document" and ref == f"http://localhost:{PORT}/"
            log({"enter": True, "mode": mode, "dest": dest, "site": site, "referer": ref, "accepted": ok})
            if ok: return self.send(200, "<!doctype html><title>HERMES OPEN</title><body>HERMES OPEN</body>")
            body = hp._enter_page(PORT); extra = [(k.decode(), v.decode()) for k, v in hp._ENTER_HEADERS if k != b"content-type" and k != b"cache-control" and k != b"pragma"]
            return self.send(403, body, "text/html; charset=utf-8", extra)
        if host == "hermes.localhost" and path == "/":
            return self.send(200, "<!doctype html><title>HERMES HOME</title><body>HERMES HOME</body>")
        self.send(404, "nope")
class S(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads = True; allow_reuse_address = True
S(("127.0.0.1", PORT), H).serve_forever()
