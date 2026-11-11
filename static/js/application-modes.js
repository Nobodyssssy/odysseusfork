const endpoint = '/api/application-modes';
const snapshotKey = 'odysseus-mode-navigation-v1';

async function assertCanLeave() {
  if (document.getElementById('incognito-toggle')?.checked) {
    throw new Error('Leave Nobody/incognito mode before switching applications; its ephemeral state will not be saved.');
  }
  if (sessionStorage.getItem(snapshotKey)) {
    throw new Error('A previous draft still awaits restoration. Return to its chat and reload before switching again.');
  }
  if (document.getElementById('file-input')?.files?.length || document.getElementById('attach-strip')?.children.length) {
    throw new Error('Send or remove pending attachments before switching modes; no attachment was discarded.');
  }
  const sessions = await import('./sessions.js');
  const sid = sessions.getCurrentSessionId();
  if ((sid && window.chatModule?.hasActiveStream?.(sid)) || document.getElementById('chat-container')?.getAttribute('aria-busy') === 'true') {
    throw new Error('Wait for the active Odysseus stream before switching modes; it was not interrupted.');
  }
}

async function saveChatState() {
  const sessions = await import('./sessions.js');
  const input = document.getElementById('message');
  const history = document.getElementById('chat-history');
  sessionStorage.setItem(snapshotKey, JSON.stringify({
    sessionId: sessions.getCurrentSessionId(), text: input?.value || '',
    start: input?.selectionStart || 0, end: input?.selectionEnd || 0,
    scroll: history?.scrollTop || 0,
  }));
}

async function restoreChatState(message) {
  const raw = sessionStorage.getItem(snapshotKey);
  if (!raw) return;
  const saved = JSON.parse(raw);
  const sessions = await import('./sessions.js');
  for (let attempt = 0; attempt < 150; attempt++) {
    if (!document.getElementById('app-loader') && sessions.getCurrentSessionId() === saved.sessionId) {
      const input = document.getElementById('message');
      if (input && (!input.value || input.value === saved.text)) {
        input.value = saved.text;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.setSelectionRange(saved.start, saved.end);
        const history = document.getElementById('chat-history');
        if (history) history.scrollTop = saved.scroll;
        sessionStorage.removeItem(snapshotKey);
        return;
      }
      break;
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  message.textContent = 'Your previous chat draft is retained in this tab, but was not restored over a different chat or draft.';
}

async function request(path, method = 'GET') {
  const response = await fetch(path, { method, credentials: 'same-origin', signal: AbortSignal.timeout(5000) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Application mode request failed');
  return result;
}

async function post(path, timeout = 60000) {
  const response = await fetch(path, { method: 'POST', credentials: 'same-origin', signal: AbortSignal.timeout(timeout) });
  const result = await response.json().catch(() => ({}));
  return { response, result };
}

async function hermesAction(kind) {
  let force = false;
  for (;;) {
    const { response, result } = await post(`${endpoint}/hermes/${kind}${force ? '?force=true' : ''}`);
    if (response.ok) return result;
    if (response.status === 409 && result.force_required && !force) {
      if (!window.confirm(`${result.detail}\n\n${kind === 'stop' ? 'Stop' : 'Restart'} Hermes anyway?`)) throw new Error('Cancelled. Hermes was not changed.');
      force = true;
      continue;
    }
    throw new Error(result.detail || 'Hermes request failed');
  }
}

async function waitForHermes(message) {
  const deadline = Date.now() + 90000;
  for (;;) {
    const status = await request(endpoint);
    if (status.state === 'ready') return status;
    if (status.state === 'conflict') throw new Error('Another program is using the Hermes port. Nothing was restarted.');
    if (Date.now() > deadline) throw new Error('Hermes did not become ready in 90 seconds. Check its launch log.');
    message.textContent = 'Starting Hermes…';
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}

async function waitForOdysseus(message) {
  const deadline = Date.now() + 150000;
  let sawDown = false;
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    try {
      const response = await fetch(endpoint, { credentials: 'same-origin', signal: AbortSignal.timeout(3000) });
      if (response.ok && sawDown) return location.reload();
      if (!response.ok) sawDown = true;
    } catch (_) {
      sawDown = true;
      message.textContent = 'Odysseus is restarting…';
    }
  }
  message.textContent = 'Odysseus did not come back in time. Check the scheduled task "Odysseus AutoStart".';
}

function serviceMenu(status, message) {
  const available = status.controls || {};
  const actions = [
    ['hermes_restart', 'Restart Hermes', async () => {
      message.textContent = 'Restarting Hermes…';
      await hermesAction('restart');
      await waitForHermes(message);
      message.textContent = 'Hermes restarted.';
    }],
    ['hermes_stop', 'Stop Hermes', async () => {
      if (!window.confirm('Stop Hermes? Anything open at hermes.localhost will stop responding until you start it again.')) return;
      message.textContent = 'Stopping Hermes…';
      await hermesAction('stop');
      message.textContent = 'Hermes stopped. Choose Hermes Mode or Restart Hermes to start it again.';
    }],
    ['odysseus_restart', 'Restart Odysseus', async () => {
      if (!window.confirm('Restart Odysseus? This page reloads when it is back, and any chat still streaming here is interrupted.')) return;
      const { response, result } = await post(`${endpoint}/odysseus/restart`);
      if (!response.ok) throw new Error(result.detail || 'Odysseus could not restart itself.');
      message.textContent = 'Odysseus is restarting…';
      await waitForOdysseus(message);
    }],
  ].filter(([key]) => available[key]);
  if (!actions.length) return null;
  const menu = document.createElement('details');
  menu.id = 'application-mode-services';
  menu.className = 'application-mode-services';
  const summary = document.createElement('summary');
  summary.textContent = 'Services';
  summary.title = 'Restart or stop the local Hermes and Odysseus services';
  const list = document.createElement('div');
  list.className = 'application-mode-service-list';
  for (const [, label, run] of actions) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', async () => {
      for (const other of list.querySelectorAll('button')) other.disabled = true;
      try { await run(); }
      catch (error) { message.textContent = error.name === 'TimeoutError' ? 'The request timed out; check the service state before retrying.' : error.message; }
      finally { for (const other of list.querySelectorAll('button')) other.disabled = false; }
    });
    list.append(button);
  }
  menu.append(summary, list);
  return menu;
}

async function launchProjects() {
  const { response, result } = await post(`${endpoint}/projects/launch`, 30000);
  if (!response.ok || typeof result.url !== 'string') throw new Error(result.detail || 'Projects could not be opened');
  const url = new URL(result.url);
  const ticketed = url.pathname === '/__odysseus/enter' && url.search.startsWith('?ticket=') && !url.hash;
  if (url.protocol !== 'http:' || url.hostname !== 'hermes.localhost' || url.port !== location.port || url.username || url.password
      || (!ticketed && url.pathname === '/__odysseus/enter')) {
    throw new Error('Projects returned an unexpected address; nothing was opened');
  }
  return url.href;
}

function openProjects(href) {
  // Odysseus sends Referrer-Policy: no-referrer on every page. The grant exchange needs to know the navigation came from this origin, so this
  // one link opts in to sending the origin (never the path) and only to the page it points at.
  const link = document.createElement('a');
  link.href = href;
  link.referrerPolicy = 'origin';
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();
}

function hermesURL(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.pathname !== '/' || url.search || url.hash || url.username || url.password) {
    throw new Error('Hermes must use a configured loopback URL');
  }
  return url;
}

function destination(status) {
  if (status.proxy && location.protocol === 'http:' && location.hostname === 'localhost' && location.port) {
    return new URL(`http://hermes.localhost:${location.port}/`);
  }
  return hermesURL(status.url);
}

async function initialize() {
  let status;
  try { status = await request(endpoint); } catch (_) { return; }
  if (!status.enabled) return;
  const stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet';
  stylesheet.href = '/static/css/application-modes.css';
  await new Promise((resolve, reject) => {
    stylesheet.onload = resolve;
    stylesheet.onerror = reject;
    document.head.append(stylesheet);
  });
  const bar = document.createElement('nav');
  bar.className = 'application-mode-bar';
  bar.setAttribute('aria-label', 'Application mode');
  const selector = document.createElement('select');
  selector.id = 'application-mode-selector';
  selector.disabled = true;
  selector.setAttribute('aria-label', 'Application mode');
  const gateway = status.backend === 'gateway';
  for (const [value, label] of gateway ? [['odysseus', 'Personal'], ['hermes', 'Projects']] : [['odysseus', 'Odysseus Mode'], ['hermes', 'Hermes Mode']]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    selector.append(option);
  }
  const message = document.createElement('span');
  message.id = 'application-mode-status';
  message.setAttribute('role', 'status');
  const providers = document.createElement('a');
  providers.id = 'application-mode-providers';
  providers.href = '/static/provider-sync.html';
  providers.textContent = 'Providers';
  providers.title = 'Shared Odysseus and Hermes providers';
  providers.style.cssText = 'margin-left:8px;color:inherit';
  const rememberPage = () => { providers.href = '/static/provider-sync.html?return=' + encodeURIComponent(location.pathname + location.search + location.hash); };
  for (const type of ['pointerenter', 'focus', 'click', 'auxclick']) providers.addEventListener(type, rememberPage);
  const services = serviceMenu(status, message);
  bar.append(selector, ...(gateway ? [] : [providers]), ...(services ? [services] : []), message);
  document.querySelector('#chat-container').prepend(bar);
  try { await restoreChatState(message); }
  catch (_) { message.textContent = 'The saved draft could not be restored. Your current composer was not changed.'; }
  selector.addEventListener('change', async () => {
    if (selector.value !== 'hermes') return;
    selector.disabled = true;
    message.textContent = 'Checking Hermes…';
    try {
      await assertCanLeave();
      status = await request(endpoint);
      if (gateway && status.state !== 'ready') throw new Error('The official Hermes Webapp is not running. Odysseus does not start it; start it first.');
      if (status.state !== 'ready') {
        message.textContent = 'Starting Hermes…';
        status = await request(`${endpoint}/hermes/start`, 'POST');
      }
      const deadline = Date.now() + 90000;
      while (status.state !== 'ready' && Date.now() < deadline) {
        if (status.state === 'conflict') throw new Error('Another program is using the Hermes port. No existing process was restarted.');
        message.textContent = 'Starting Hermes…';
        await new Promise(resolve => setTimeout(resolve, 1000));
        status = await request(endpoint);
      }
      if (status.state !== 'ready') throw new Error('Hermes is not ready yet. Stay here and try again; no process was stopped.');
      await assertCanLeave();
      await saveChatState();
      if (gateway) openProjects(await launchProjects());
      else location.assign(destination(status).href);
    } catch (error) {
      message.textContent = error.message;
      selector.value = 'odysseus';
      selector.disabled = false;
    }
  });
  selector.disabled = false;
}

initialize();
