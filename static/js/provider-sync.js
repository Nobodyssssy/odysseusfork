const endpoint = '/api/provider-sync';

const STATUS = {
  connected: 'Connected', requires_authentication: 'Needs authentication', unsupported: 'Not supported',
  offline: 'Offline', disabled: 'Disabled', unknown: 'Not checked',
};
const SYNC = {
  in_sync: 'In sync', models_differ: 'Model lists differ', sharing_off: 'Sharing off',
  pending_hermes: 'Waiting to be added to Hermes', pending_odysseus: 'Waiting to be added to Odysseus',
  deleted_on_hermes: 'Removed in Hermes (not re-added)', deleted_on_odysseus: 'Removed in Odysseus (not re-added)',
  not_shareable: 'Not shareable', not_shareable_credential: 'Login-based: cannot be shared',
};
const SOURCE = { odysseus: 'Odysseus', hermes: 'Hermes', both: 'Both' };

const body = document.querySelector('#providers tbody');
const banner = document.getElementById('banner');
const busy = new Set();

async function call(path, method = 'GET', payload) {
  const response = await fetch(endpoint + path, {
    method, credentials: 'same-origin', signal: AbortSignal.timeout(60000),
    headers: payload ? { 'Content-Type': 'application/json' } : undefined,
    body: payload ? JSON.stringify(payload) : undefined,
  });
  let data = {};
  try { data = await response.json(); } catch (_) { /* non-JSON error body */ }
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function cell(text, className) {
  const td = document.createElement('td');
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function badge(state) {
  const span = document.createElement('span');
  span.className = 'badge ' + state;
  span.textContent = STATUS[state] || state;
  return span;
}

function appCell(status) {
  const td = document.createElement('td');
  td.append(status ? badge(status) : Object.assign(document.createElement('span'), { className: 'badge none', textContent: 'Not added' }));
  return td;
}

function button(label, handler, disabled) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  b.disabled = !!disabled;
  b.addEventListener('click', handler);
  return b;
}

function modelsCell(row) {
  const td = document.createElement('td');
  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = `${row.models.length} model${row.models.length === 1 ? '' : 's'}`;
  const list = document.createElement('ul');
  for (const model of row.models.slice(0, 200)) {
    const li = document.createElement('li');
    li.textContent = model;
    list.append(li);
  }
  details.append(summary, list);
  td.append(details);
  return td;
}

async function act(row, work, done) {
  if (busy.has(row.id)) return;
  busy.add(row.id);
  banner.textContent = 'Working…';
  try { banner.textContent = (await work()) || done || ''; }
  catch (error) { banner.textContent = error.message; }
  finally { busy.delete(row.id); await load(false); }
}

function render(data) {
  body.replaceChildren();
  document.getElementById('empty').hidden = data.providers.length > 0;
  for (const row of data.providers) {
    const tr = document.createElement('tr');
    const name = cell(row.name);
    if (row.base_url) {
      const small = document.createElement('small');
      small.textContent = row.base_url;
      name.append(document.createElement('br'), small);
    }
    if (row.duplicates) {
      const note = document.createElement('small');
      note.textContent = ` (${row.duplicates} duplicate entr${row.duplicates === 1 ? 'y' : 'ies'} hidden)`;
      name.append(note);
    }
    const share = document.createElement('td');
    const toggle = document.createElement('input');
    toggle.type = 'checkbox';
    toggle.checked = row.share_enabled;
    toggle.disabled = !row.portable;
    toggle.setAttribute('aria-label', `Share ${row.name} between Odysseus and Hermes`);
    toggle.addEventListener('change', () => act(row, async () => {
      await call('/share', 'POST', { id: row.id, enabled: toggle.checked });
      return toggle.checked ? 'Sharing enabled. Use "Add" to create it on the other side.' : 'Sharing turned off. Nothing was removed.';
    }));
    share.append(toggle);
    const actions = document.createElement('td');
    const local = row.id.startsWith('ep:http://127.0.0.1');
    actions.append(
      button('Test', () => act(row, async () => {
        const result = await call('/test', 'POST', { id: row.id });
        return result.state === 'online' ? `${row.name}: online, ${result.models.length} models.` : `${row.name}: ${result.message || result.state}`;
      }), !local),
      button('Add', () => act(row, async () => {
        const result = await call('/apply', 'POST', { id: row.id });
        return result.result === 'already_present' ? 'Already present on both sides.' : `Added to ${result.target === 'hermes' ? 'Hermes' : 'Odysseus'}. If it needs a key, enter it there.`;
      }), !row.sync.startsWith('pending_')),
    );
    tr.append(name, cell(SOURCE[row.source] || row.source), modelsCell(row),
      appCell(row.status.odysseus), appCell(row.status.hermes), cell(SYNC[row.sync] || row.sync, 'sync ' + row.sync), share, actions);
    body.append(tr);
  }
  const hermes = data.applications.hermes;
  banner.textContent = hermes === 'ready' ? '' : hermes === 'requires_authentication'
    ? 'Hermes asks for a login, so its providers cannot be read here.' : 'Hermes is not reachable. Odysseus providers are shown; nothing can be added to Hermes.';
}

async function load(refresh) {
  try { render(await call(refresh ? '/refresh' : '', refresh ? 'POST' : 'GET')); }
  catch (error) { banner.textContent = error.message; }
}

document.getElementById('refresh').addEventListener('click', () => load(true));
document.getElementById('apply-all').addEventListener('click', async () => {
  banner.textContent = 'Working…';
  try {
    const { results } = await call('/apply-all', 'POST');
    banner.textContent = results.length ? `${results.filter(r => r.result === 'added' || r.result === 'created').length} of ${results.length} added.` : 'Nothing is waiting.';
  } catch (error) { banner.textContent = error.message; }
  await load(false);
});
load(false);
