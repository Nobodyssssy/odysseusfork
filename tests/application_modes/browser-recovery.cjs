/* Isolated development ports only. Stops the private Hermes (18810) and starts it again from the mode selector. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const state = path.resolve(__dirname, '../..', '.mode-dev');
const ODY = 'http://localhost:17010';
const HERMES = 'http://hermes.localhost:17010';

(async () => {
  assert.equal(JSON.parse(fs.readFileSync(path.join(state, 'isolation.json'))).version, 1);
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const context = await browser.newContext();
    const sessions = JSON.parse(fs.readFileSync(path.join(state, 'odysseus/sessions.json')));
    const token = Object.keys(sessions).find(key => sessions[key].expiry > Date.now() / 1000);
    await context.route(`${ODY}/__login-probe`, route => route.fulfill({
      status: 200, contentType: 'text/plain', body: 'ok',
      headers: { 'set-cookie': `odysseus_session=${token}; Path=/; HttpOnly; SameSite=Lax` },
    }));
    const page = await context.newPage();
    await page.goto(`${ODY}/__login-probe`);
    execFileSync('powershell.exe', ['-NoProfile', '-Command',
      '$p=(Get-NetTCPConnection -LocalPort 18810 -State Listen -ErrorAction Stop).OwningProcess;taskkill /PID $p /T /F | Out-Null'], { stdio: 'inherit' });
    await new Promise(resolve => setTimeout(resolve, 2000));
    await page.goto(ODY, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#application-mode-selector', { timeout: 45000 });
    const started = Date.now();
    await page.locator('#application-mode-selector').selectOption('hermes');
    await page.waitForURL(`${HERMES}/**`, { timeout: 120000, waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#msg', { timeout: 60000 });
    console.log(JSON.stringify({ check: 'One selector click restarts a stopped Hermes and opens it', seconds: Math.round((Date.now() - started) / 1000), passed: true }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
