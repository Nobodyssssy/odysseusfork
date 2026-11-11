
import json, sys
from pathlib import Path
from hermes_cli.webapp_launcher import _control
home = Path(r'F:\Odysseus-Backups\desktop-lab\home')
st = _control(home, 'webapp-status'); print('status:', json.dumps(st)[:300])
ln = _control(home, 'webapp-launch', {}); res = ln.get('result') or ln
url = res.get('url'); print('private:', res.get('private'), '| url shape:', (url or '')[:46] + '...#<ticket redacted>' if url else ln)
open(r'F:\Odysseus-Backups\desktop-lab\results\launch-url.txt', 'w').write(url or '')
