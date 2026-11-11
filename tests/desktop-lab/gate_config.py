"""Lab only: turn the Webapp sign-in gate on/off in the lab gateway config (webapp.public_url + dashboard.basic_auth).
Run with the Hermes interpreter, PYTHONPATH=<lab src>.   python gate_config.py on|off"""
import json, os, re, secrets, sys
CFG = r'F:\Odysseus-Backups\desktop-lab\home\config.yaml'
SECRET = r'F:\Odysseus-Backups\desktop-lab\results\gate-secret.json'
PUBLIC = 'http://hermes.localhost:19800'
text = open(CFG, encoding='utf-8', newline='').read().replace('\r\n', '\n')
text = re.sub(r'\ndashboard:\n(?:  .*\n)+', '\n', text)
text = re.sub(r'(webapp:\n(?:  .*\n)*?  public_url: ).*', r'\1""', text)
if sys.argv[1] == 'on':
    from plugins.dashboard_auth.basic import hash_password
    pw = secrets.token_urlsafe(18)
    json.dump({'username': 'labgate', 'password': pw}, open(SECRET, 'w'))
    text = re.sub(r'(webapp:\n(?:  .*\n)*?  public_url: ).*', r'\1"' + PUBLIC + '"', text)
    text = text.rstrip('\n') + '\ndashboard:\n  basic_auth:\n    username: labgate\n    password_hash: "' + hash_password(pw) + '"\n    secret: "' + secrets.token_urlsafe(32) + '"\n'
elif os.path.exists(SECRET):
    os.remove(SECRET)
open(CFG, 'w', encoding='utf-8', newline='').write(text.replace('\n', '\r\n'))
print('gate', sys.argv[1], '| public_url line:', re.search(r'public_url: .*', text).group(0), '| dashboard block present:', 'dashboard:' in text)
