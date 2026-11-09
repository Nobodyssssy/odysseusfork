/* Standalone WebUI extension: navigation only, no permission or model changes. */
(async function () {
  const scriptURL = document.currentScript?.src || new URL('extensions/odysseus-mode/return-to-odysseus.js', document.baseURI);
  const response = await fetch(new URL('connection.json', new URL('.', scriptURL)), { credentials: 'same-origin', signal: AbortSignal.timeout(5000) });
  if (!response.ok) return;
  const config = await response.json();
  const target = new URL(config.odysseus_url);
  if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1' || !target.port || target.pathname !== '/' || target.search || target.hash || target.username || target.password || target.origin === location.origin) return;
  const nav = document.createElement('nav');
  nav.id = 'odysseus-mode-return';
  nav.setAttribute('aria-label', 'Application mode');
  const link = document.createElement('a');
  link.id = 'odysseus-return-control';
  link.textContent = '← Odysseus Mode';
  link.href = target.href;
  link.title = 'Return to Odysseus; the Hermes backend stays running';
  const status = document.createElement('span');
  status.id = 'odysseus-return-status';
  status.setAttribute('role', 'status');
  link.addEventListener('click', async event => {
    event.preventDefault();
    if (link.getAttribute('aria-disabled') === 'true') return;
    link.setAttribute('aria-disabled', 'true');
    status.textContent = '';
    try {
      if (typeof S === 'undefined' || !S._bootReady) throw new Error('Wait for Hermes to finish loading before returning.');
      const input = document.getElementById('msg');
      const text = input?.value || '';
      const sid = S.session?.session_id || null;
      if (S.pendingFiles?.length || document.getElementById('fileInput')?.files?.length) throw new Error('Send or remove pending attachments before returning; no attachment was discarded.');
      try {
        // Reachability only: opaque no-cors responses do not prove app identity.
        await fetch(new URL('api/health', target), { mode: 'no-cors', credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(4000) });
      } catch (_) { throw new Error('Odysseus is not reachable. Hermes stays open; start Odysseus and retry.'); }
      if (sid && typeof _saveComposerDraftNow === 'function') {
        const continuation = typeof _peekRestoredGoalContinuationDraft === 'function' ? _peekRestoredGoalContinuationDraft() : null;
        const continuationId = continuation && continuation.text === text ? continuation.id : '';
        let timer;
        try {
          await Promise.race([
            _saveComposerDraftNow(sid, text, S.pendingFiles ? [...S.pendingFiles] : [], continuationId),
            new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Draft save timed out; stay here and retry.')), 5000); }),
          ]);
        } finally { clearTimeout(timer); }
      }
      if (!sid) {
        const key = 'odysseus-mode-new-draft-v1:' + (S.activeProfile || 'default');
        sessionStorage.setItem(key, JSON.stringify({ text, start: input?.selectionStart || 0, end: input?.selectionEnd || 0 }));
      }
      if ((input?.value || '') !== text || (S.session?.session_id || null) !== sid || S.pendingFiles?.length) throw new Error('Your chat, draft or attachments changed while saving. Click Return again.');
      location.assign(target.href);
    } catch (error) {
      status.textContent = error.message;
      link.removeAttribute('aria-disabled');
    }
  });
  nav.append(link, status);
  document.body.append(nav);
  for (let attempt = 0; attempt < 150; attempt++) {
    if (typeof S !== 'undefined' && S._bootReady) {
      const key = 'odysseus-mode-new-draft-v1:' + (S.activeProfile || 'default');
      try {
        const raw = sessionStorage.getItem(key);
        if (raw) {
          const saved = JSON.parse(raw);
          const input = document.getElementById('msg');
          if (!S.session && input && typeof saved.text === 'string' && (!input.value || input.value === saved.text)) {
            input.value = saved.text;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.setSelectionRange(saved.start || 0, saved.end || 0);
            sessionStorage.removeItem(key);
          } else {
            status.textContent = 'Your new-chat draft is retained in this tab; it was not restored over a different chat or draft.';
          }
        }
      } catch (_) { status.textContent = 'The saved new-chat draft could not be restored; the current composer was not changed.'; }
      break;
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
})().catch(() => { console.warn('Odysseus return extension could not load its local configuration.'); });
