const COLORS = ['#ff6b35','#22b8cf','#94d82d','#fcc419','#e599f7','#ff8787',
                '#74c0fc','#63e6be'];
const map = L.map('map').setView([39.5, -95.0], 4);  // neutral until a start is set
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
            {attribution:'&copy; OpenStreetMap'}).addTo(map);
let candLayers = [];   // [{gpx, casing, line}]
let currentPath = null;

// per-browser session id: isolates this user's routes and current-route
// state from everyone else's on a shared server
function makeSid() {
  if (crypto.randomUUID) return crypto.randomUUID();  // needs secure context
  const a = new Uint8Array(16);
  crypto.getRandomValues(a);
  return Array.from(a, b => b.toString(16).padStart(2, '0')).join('');
}
let SID = localStorage.getItem('rg_sid');
if (!SID) { SID = makeSid(); localStorage.setItem('rg_sid', SID); }
function hdrs() {
  const h = {'Content-Type': 'application/json', 'X-Session-Id': SID};
  const inv = localStorage.getItem('rg_invite');
  if (inv) h['X-Invite-Code'] = inv;
  return h;
}
const POLL_INTERVAL_MS = 1200;
const JOB_WAIT_LIMIT_MS = 30 * 60 * 1000;
let sending = false;

async function apiFetch(url, options = {}) {
  const headers = {...hdrs(), ...(options.headers || {})};
  if (options.body instanceof FormData) delete headers['Content-Type'];
  let response = await fetch(url, {...options, headers});
  if (response.status === 401) {
    const code = prompt('This server needs an invite code:');
    if (code) {
      localStorage.setItem('rg_invite', code);
      headers['X-Invite-Code'] = code;
      response = await fetch(url, {...options, headers});
    }
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || (typeof error.detail === 'string' ? error.detail : null) || `Request failed (${response.status}).`);
  }
  return response;
}

function reportError(error) { banner(error.message || String(error), false); }
function setBusy(busy) {
  sending = busy;
  ['go','uploadbtn','undobtn'].forEach(id => document.getElementById(id).disabled = busy);
  document.querySelectorAll('.selbtn').forEach(button => button.disabled = busy);
}


const norm = p => (p || '').replaceAll('\\\\', '/').replaceAll('\\', '/');

function banner(msg, ok) {
  const b = document.getElementById('banner');
  b.textContent = msg || '';
  b.className = !msg ? '' :
    (ok === 'partial' ? 'warn' : ok === false ? 'bad' : 'ok');
}

function restyleLines() {
  candLayers.forEach(c => {
    const sel = norm(c.gpx) === norm(currentPath);
    // selected stays fully visible; the rest fade back out of the way
    c.line.setStyle({opacity: sel ? 0.95 : 0.28, weight: sel ? 6 : 4});
    c.casing.setStyle({opacity: sel ? 0.55 : 0.08});
    if (sel) { c.casing.bringToFront(); c.line.bringToFront(); }
  });
}

async function refreshCurrent() {
  const r = await apiFetch('/api/current').then(r => r.json());
  currentPath = r.current || null;
  document.getElementById('current').textContent =
      currentPath ? 'selected route: ' + currentPath : 'no route selected yet';
  document.querySelectorAll('.cand').forEach(row => {
    row.classList.toggle('selected',
        norm(row.dataset.gpx) === norm(currentPath));
  });
  restyleLines();
  return r;
}
refreshCurrent().then(data => {
  if (data.candidates && data.candidates.length) showResult(data);
}).catch(reportError);

// ---- starting point (persisted per browser) ----
const startInput = document.getElementById('start');
async function applyStart(place) {
  const r = await apiFetch('/api/geocode?q=' + encodeURIComponent(place));
  const g = await r.json();
  localStorage.setItem('rg_start', place);
  startInput.value = place;
  startInput.classList.remove('unset');
  map.setView([g.lat, g.lon], 11);
  return true;
}
document.getElementById('setstart').onclick = () => applyStart(startInput.value.trim()).catch(reportError);
startInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') applyStart(startInput.value.trim()).catch(reportError);
});
startInput.addEventListener('change', () => {
  const v = startInput.value.trim();
  if (v && v !== localStorage.getItem('rg_start')) applyStart(v).catch(reportError);
});
const savedStart = localStorage.getItem('rg_start');
if (savedStart) { applyStart(savedStart).catch(reportError); }
else { startInput.classList.add('unset'); startInput.focus(); }

function showResult(result) {
  if (!result.candidates || !result.candidates.length) {
    // nothing came back — keep whatever is on screen, the log says why
    return;
  }
  candLayers.forEach(c => { map.removeLayer(c.casing); map.removeLayer(c.line); });
  candLayers = [];
  const box = document.getElementById('results');
  box.innerHTML = '';
  let bounds = null;
  result.candidates.forEach((c, i) => {
    const color = COLORS[i % COLORS.length];
    // dark casing under each line; restyleLines() dims non-selected ones
    const casing = L.polyline(c.latlngs, {color:'#10151b', weight: 8,
                                          opacity: 0.55});
    const line = L.polyline(c.latlngs, {color, weight: 5, opacity: 0.85});
    casing.addTo(map); line.addTo(map);
    candLayers.push({gpx: c.gpx, casing, line});
    bounds = bounds ? bounds.extend(line.getBounds()) : line.getBounds();
    const row = document.createElement('div');
    row.className = 'cand';
    row.dataset.gpx = c.gpx;
    // labels carry user-typed text (place names) — build with textContent
    const dot = document.createElement('span');
    dot.className = 'dot'; dot.style.background = color;
    const label = document.createElement('span');
    label.className = 'label'; label.textContent = c.label + ' ';
    const sel = document.createElement('span');
    sel.className = 'sel'; sel.textContent = '✓ selected';
    label.appendChild(sel);
    const dl = document.createElement('a');
    dl.href = '/api/gpx?path=' + encodeURIComponent(c.gpx);
    dl.title = 'download for your Garmin'; dl.textContent = 'gpx';
    dl.onclick = async event => {
      event.preventDefault(); event.stopPropagation();
      try {
        const response = await apiFetch(dl.href);
        const url = URL.createObjectURL(await response.blob());
        const download = document.createElement('a');
        download.href = url; download.download = norm(c.gpx).split('/').pop();
        download.click(); setTimeout(() => URL.revokeObjectURL(url), POLL_INTERVAL_MS);
      } catch (error) { reportError(error); }
    };
    const btn = document.createElement('button');
    btn.className = 'selbtn'; btn.title = 'ride/edit this one instead';
    btn.textContent = 'select';
    row.append(dot, label, dl, btn);
    row.onclick = () => {
      map.fitBounds(line.getBounds(), {padding:[30,30]});
      // peek at a non-selected candidate without changing the selection
      line.setStyle({opacity: 0.95, weight: 6});
      casing.setStyle({opacity: 0.55});
      casing.bringToFront(); line.bringToFront();
      setTimeout(restyleLines, 2500);
    };
    row.querySelector('button').onclick = async (ev) => {
      ev.stopPropagation();
      if (sending) return;
      try {
        await apiFetch('/api/current', {method:'POST', body: JSON.stringify({text: c.gpx})});
        await refreshCurrent();
      } catch (error) { reportError(error); }
    };
    box.appendChild(row);
  });
  if (bounds) map.fitBounds(bounds, {padding:[30,30]});
  refreshCurrent().catch(reportError);
}

async function send() {
  const text = document.getElementById('ask').value.trim();
  if (!text || sending) return;
  const cancelBtn = document.getElementById('cancelbtn');
  const log = document.getElementById('log');
  setBusy(true); banner('', true); log.textContent = '...\n';
  const start = localStorage.getItem('rg_start');
  const intent = document.getElementById('intent').value;
  try {
    const response = await apiFetch('/api/ask', {method:'POST', body: JSON.stringify({text, start, intent})});
    const {job} = await response.json();
    cancelBtn.hidden = false;
    const deadline = Date.now() + JOB_WAIT_LIMIT_MS;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS));
      const response = await apiFetch('/api/job/' + job);
      const j = await response.json();
      log.textContent = j.log || '...'; log.scrollTop = log.scrollHeight;
      if (j.status === 'done') {
        banner(j.result.summary || 'Done.', j.result.ok);
        showResult(j.result); await refreshCurrent(); return;
      }
      if (j.status === 'error') throw new Error('Something went wrong - details in the log.');
      if (j.status === 'cancelled') {
        banner('Cancelled - the previous selection is unchanged.', 'partial');
        await refreshCurrent(); return;
      }
      if (j.status !== 'running') throw new Error('Unrecognized job state. Refresh to recover the selected route.');
    }
    throw new Error('Stopped waiting for this job. It may still be running; refresh before submitting another request.');
  } catch (error) { reportError(error); }
  finally { setBusy(false); cancelBtn.hidden = true; }
}
document.getElementById('go').onclick = send;
document.getElementById('cancelbtn').onclick = async () => {
  const button = document.getElementById('cancelbtn');
  button.disabled = true;
  try {
    await apiFetch('/api/cancel', {method:'POST'});
    banner('Cancellation requested. Waiting for the current routing call to finish.', 'partial');
  } catch (error) { reportError(error); }
  finally { button.disabled = false; }
};

document.getElementById('undobtn').onclick = async () => {
  if (sending) return;
  try {
    const response = await apiFetch('/api/undo', {method:'POST'});
    const result = await response.json();
    banner(result.summary, result.ok); showResult(result); await refreshCurrent();
  } catch (error) { reportError(error); }
};

// ---- upload an existing route ----
const gpxInput = document.getElementById('gpxfile');
document.getElementById('uploadbtn').onclick = () => gpxInput.click();
gpxInput.onchange = async () => {
  const file = gpxInput.files[0];
  if (!file || sending) return;
  setBusy(true);
  try {
    const body = new FormData(); body.append('file', file);
    const response = await apiFetch('/api/upload', {method:'POST', body});
    const result = await response.json();
    banner(result.summary, result.ok); showResult(result);
    document.getElementById('log').textContent = 'Uploaded ' + file.name + '.';
  } catch (error) { reportError(error); }
  finally { setBusy(false); gpxInput.value = ''; }
};
document.getElementById('ask').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send();
});
