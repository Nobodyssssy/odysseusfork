/* Isolated development ports only (17010 Odysseus, 18810 Hermes) and a private browser context. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const ROOT = path.resolve(__dirname, '../..');
const state = path.join(ROOT, '.mode-dev');
const PORT = 17010;
const ODY = `http://localhost:${PORT}`;
const HERMES = `http://hermes.localhost:${PORT}`;
const ok = (check) => console.log(JSON.stringify({ check, passed: true }));

function killIsolatedHermes() {
  // Port 18810 is the private development port; it was free before this stack was launched.
  const script = `$p=(Get-NetTCPConnection -LocalPort 18810 -State Listen -ErrorAction Stop).OwningProcess;taskkill /PID $p /T /F | Out-Null`;
  execFileSync('powershell.exe', ['-NoProfile', '-Command', script], { stdio: 'inherit' });
}

(async () => {
  assert.equal(JSON.parse(fs.readFileSync(path.join(state, 'isolation.json'))).version, 1);
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const sessions = JSON.parse(fs.readFileSync(path.join(state, 'odysseus/sessions.json')));
    const token = Object.keys(sessions).find(key => sessions[key].expiry > Date.now() / 1000);
    assert.ok(token, 'The isolated snapshot needs a valid Odysseus login session.');
    // A real Set-Cookie header, like Odysseus's login: no Domain attribute, so the cookie is host-only.
    await context.route(`${ODY}/__login-probe`, route => route.fulfill({
      status: 200, contentType: 'text/plain', body: 'ok',
      headers: { 'set-cookie': `odysseus_session=${token}; Path=/; HttpOnly; SameSite=Lax` },
    }));
    const page = await context.newPage();
    await page.goto(`${ODY}/__login-probe`);
    const streams = [];
    const hermesCookieHeaders = [];
    page.on('request', async request => {
      if (request.url().startsWith(HERMES)) hermesCookieHeaders.push((await request.allHeaders()).cookie || '');
    });
    page.on('response', response => {
      if ((response.headers()['content-type'] || '').includes('text/event-stream')) streams.push(response.url());
    });

    await page.goto(ODY, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#application-mode-selector', { timeout: 45000 });
    const modes = await page.evaluate(async () => (await fetch('/api/application-modes')).json());
    assert.equal(modes.proxy, true);
    ok('Odysseus at localhost serves its own UI and reports the proxy as available');

    await page.locator('#message').first().fill('Proxy navigation draft: do not send.');
    await page.locator('#application-mode-selector').selectOption('hermes');
    await page.waitForURL(`${HERMES}/**`, { timeout: 120000, waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#msg', { timeout: 60000 });
    await page.waitForSelector('#odysseus-return-control', { timeout: 15000 });
    assert.equal(new URL(page.url()).port, String(PORT));
    ok('Hermes Mode opens the complete Hermes UI at hermes.localhost on the same port');

    const hermesHealth = await page.evaluate(async () => {
      const r = await fetch('/health');
      return { status: r.status, body: await r.json(), xfo: r.headers.get('x-frame-options'), csp: r.headers.get('content-security-policy') };
    });
    assert.equal(hermesHealth.status, 200);
    assert.equal(typeof hermesHealth.body.server_started_at, 'number');
    assert.equal(hermesHealth.xfo, 'DENY');
    assert.ok(hermesHealth.csp.includes("frame-ancestors 'none'"));
    const odysseusHealth = await page.evaluate(async () => (await fetch('/api/health')).status);
    assert.notEqual(odysseusHealth, 200, 'Odysseus routes must not answer on the Hermes host');
    ok('Hermes keeps its own anti-framing headers and CSP; Odysseus routes are not exposed on the Hermes host');

    assert.ok(hermesCookieHeaders.length > 5, 'Hermes requests were not observed');
    assert.ok(hermesCookieHeaders.every(value => !value.includes('odysseus_session')), 'The Odysseus login cookie must not reach Hermes');
    ok(`Cookies stay per hostname: the Odysseus session cookie was sent to none of ${hermesCookieHeaders.length} Hermes requests`);

    await page.locator('.session-item[data-sid]').first().click();
    await page.waitForFunction(() => typeof S !== 'undefined' && S.session && S.session.session_id && _loadingSessionId === null);
    await page.locator('#msg').fill('Proxy Hermes draft: do not send.');
    const before = await page.evaluate(() => ({ id: S.session.session_id, workspace: S.session.workspace, model: S.session.model }));
    await page.locator('#odysseus-return-control').click();
    await page.waitForURL(`${ODY}/**`, { timeout: 30000, waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#application-mode-selector');
    await page.waitForFunction(() => document.getElementById('message').value === 'Proxy navigation draft: do not send.', null, { timeout: 20000 });
    ok('Return button goes back to localhost and restores the Odysseus draft');
    await page.locator('#application-mode-selector').selectOption('hermes');
    await page.waitForURL(`${HERMES}/**`, { timeout: 60000, waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.getElementById('msg')?.value === 'Proxy Hermes draft: do not send.', null, { timeout: 15000 });
    const after = await page.evaluate(() => ({ id: S.session.session_id, workspace: S.session.workspace, model: S.session.model }));
    assert.deepEqual(after, before);
    ok('Hermes session, workspace, model and composer draft survive the round trip');

    await page.evaluate(() => localStorage.clear());
    await page.goto(HERMES, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof S !== 'undefined' && S._bootReady && !S.session);
    const marker = 'proxied-ok-' + Date.now();
    await page.locator('#msg').fill(`Reply with exactly this single token and nothing else: ${marker}`);
    const uploadSeen = page.waitForResponse(r => r.url().includes('/api/upload') && r.request().method() === 'POST', { timeout: 60000 }).catch(() => null);
    const attachment = path.join(state, 'proxy-upload.txt');
    fs.writeFileSync(attachment, 'proxy upload check\n');
    await page.setInputFiles('#fileInput', attachment);
    await page.locator('#msg').press('Enter');
    const sid = await page.waitForFunction(() => S.session && S.session.session_id, null, { timeout: 60000 }).then(h => h.jsonValue());
    const reply = await page.waitForFunction(async ({ sid, marker }) => {
      const r = await fetch('/api/session?session_id=' + sid);
      const data = await r.json();
      return (data.session.messages || []).some(m => m.role === 'assistant' && JSON.stringify(m.content).includes(marker));
    }, { sid, marker }, { timeout: 180000, polling: 2000 }).then(() => true);
    assert.ok(reply);
    assert.ok(streams.some(url => url.startsWith(HERMES)), 'A server-sent event stream must have passed through the proxy');
    const upload = await uploadSeen;
    assert.ok(!upload || upload.status() === 200, 'upload through the proxy failed');
    ok(`Real chat streamed through the proxy (SSE: ${streams.length}, upload ${upload ? 'seen' : 'not triggered by the UI'}) and the reply persisted`);

    // Hermes writes a finished chat to disk shortly after the reply; measure that gap instead of racing it.
    const sessionFile = path.join(state, 'hermes/webui/sessions', `${sid}.json`);
    const writeWaitStarted = Date.now();
    let onDisk = false;
    while (Date.now() - writeWaitStarted < 90000 && !onDisk) {
      onDisk = fs.existsSync(sessionFile) && fs.readFileSync(sessionFile, 'utf8').includes(marker);
      if (!onDisk) await new Promise(resolve => setTimeout(resolve, 1000));
    }
    console.log(`chat reached disk: ${onDisk} after ${Math.round((Date.now() - writeWaitStarted) / 1000)}s following the reply`);
    assert.ok(onDisk, 'The finished chat was never written to Hermes state');
    killIsolatedHermes();
    await new Promise(resolve => setTimeout(resolve, 2000));
    const down = await context.request.get(`${HERMES}/health`);
    assert.equal(down.status(), 502);
    assert.equal(down.headers()['x-frame-options'], 'DENY');
    assert.equal((await context.request.get(`${ODY}/static/js/application-modes.js`)).status(), 200);
    ok('With Hermes stopped the proxy answers 502 and Odysseus keeps working');
    await page.goto(ODY, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#application-mode-selector');
    const restartStarted = Date.now();
    let attempts = 0;
    for (; attempts < 3; attempts++) {
      await page.locator('#application-mode-selector').selectOption('hermes');
      const arrived = await page.waitForURL(`${HERMES}/**`, { timeout: 110000, waitUntil: 'domcontentloaded' }).then(() => true, () => false);
      if (arrived) break;
      console.log('selector message:', await page.locator('#application-mode-status').innerText());
      await page.goto(ODY, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('#application-mode-selector');
    }
    assert.ok(page.url().startsWith(HERMES), 'Hermes Mode never opened after the backend was stopped');
    console.log(`restart took ${Math.round((Date.now() - restartStarted) / 1000)}s over ${attempts + 1} selector attempt(s)`);
    await page.waitForSelector('#msg', { timeout: 60000 });
    const recovered = await page.evaluate(async sid => (await (await fetch('/api/session?session_id=' + sid)).json()).session.session_id, sid);
    assert.equal(recovered, sid);
    ok('Selecting Hermes Mode restarts the stopped backend and the persisted session is intact');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
