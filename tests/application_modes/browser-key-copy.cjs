/* ISOLATED stack only (17020/18820). Drives the real Providers page in Edge: the Copy key button and its confirmation. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const state = path.resolve(__dirname, '../..', '.mode-dev');
const ODY = 'http://localhost:17020';

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

    // Reset: remove the endpoint the API test created, so the button is offered again (isolated Odysseus only).
    const removed = await page.evaluate(async origin => {
      const list = await (await fetch('/api/model-endpoints')).json();
      const items = Array.isArray(list) ? list : (list.endpoints || []);
      const zen = items.find(e => JSON.stringify(e).includes('opencode.ai'));
      if (!zen) return 'none';
      const r = await fetch('/api/model-endpoints/' + zen.id, { method: 'DELETE', headers: { Origin: origin } });
      return r.status;
    }, ODY);
    console.log('reset: removed isolated Zen endpoint ->', removed);

    await page.goto(`${ODY}/static/provider-sync.html`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#providers tbody tr', { timeout: 60000 });
    const row = page.locator('#providers tbody tr', { hasText: 'OpenCode Zen' });
    const copy = row.getByRole('button', { name: 'Copy key to Odysseus' });
    await copy.waitFor({ timeout: 20000 });
    for (const other of ['OpenAI Codex', 'Nous Portal', 'Solstice', 'Claude Subscription']) {
      assert.equal(await page.locator('#providers tbody tr', { hasText: other }).getByRole('button', { name: 'Copy key to Odysseus' }).count(), 0, other + ' must not offer a key copy');
    }
    console.log('PASS the button is offered only on OpenCode Zen');

    // 1. Cancel in the confirmation: nothing happens
    let dialogText = '';
    page.once('dialog', d => { dialogText = d.message(); d.dismiss(); });
    await copy.click();
    await page.waitForTimeout(1500);
    assert.ok(/OpenCode Zen/.test(dialogText) && /never shown/.test(dialogText) && /Hermes keeps its own copy/.test(dialogText), dialogText);
    assert.equal(await copy.count(), 1, 'still offered after Cancel');
    assert.equal(await page.evaluate(async () => JSON.stringify(await (await fetch('/api/model-endpoints')).json()).includes('opencode.ai')), false, 'Cancel created nothing');
    console.log('PASS Cancel in the confirmation copies nothing');

    // 2. Accept: copied
    page.once('dialog', d => d.accept());
    await copy.click();
    await page.waitForFunction(() => document.getElementById('banner').textContent.includes('copied key'), null, { timeout: 30000 });
    await row.getByText('Odysseus has a key').waitFor({ timeout: 20000 });
    assert.equal(await row.getByRole('button', { name: 'Copy key to Odysseus' }).count(), 0, 'button gone once Odysseus has the key');
    const pageText = await page.evaluate(() => document.documentElement.outerHTML);
    assert.equal(pageText.includes('sk-HER'), false, 'the key never appears in the page');
    console.log('PASS Accept copies the key; the page shows "Odysseus has a key" and never the key');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('FAIL', e.message.split('\n').slice(0, 6).join(' | ')); process.exit(1); });
