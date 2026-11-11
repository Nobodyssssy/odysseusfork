"""Disposable stand-in for Odysseus: the REAL HermesHostProxy (gateway mode) in the real uvicorn, on a spare port.
Everything that is not hermes.localhost:<port> gets a fixed marker page, like Odysseus' own routes would.
Usage: proxy_host.py PORT GATEWAY_PORT   (env ODYSSEUS_HERMES_PROXY_* is honoured)"""
import os, sys
sys.path.insert(0, os.environ.get('ODY_SRC', r'F:\Odysseus\integration-backups\webapp-proxy'))
import uvicorn
from src.hermes_proxy import HermesHostProxy, ProxyPolicy, uvicorn_ws_max_size

async def odysseus(scope, receive, send):
    if scope['type'] == 'websocket':
        await receive(); return await send({'type': 'websocket.close', 'code': 1008})
    if scope['type'] != 'http':
        return
    await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/plain')]})
    await send({'type': 'http.response.body', 'body': b'ODYSSEUS-STUB'})

port, gw = int(sys.argv[1]), int(sys.argv[2])
os.environ.setdefault('ODYSSEUS_HERMES_PROXY_MODE', 'gateway')
app = HermesHostProxy(odysseus, f'http://127.0.0.1:{gw}', mode='gateway', policy=ProxyPolicy.from_environment(os.environ))
uvicorn.run(app, host='127.0.0.1', port=port, log_level='info', ws_max_size=uvicorn_ws_max_size())
