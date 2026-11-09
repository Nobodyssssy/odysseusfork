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
  for (const [value, label] of [['odysseus', 'Odysseus Mode'], ['hermes', 'Hermes Mode']]) {
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
  bar.append(selector, providers, message);
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
      location.assign(destination(status).href);
    } catch (error) {
      message.textContent = error.message;
      selector.value = 'odysseus';
      selector.disabled = false;
    }
  });
  selector.disabled = false;
}

initialize();
