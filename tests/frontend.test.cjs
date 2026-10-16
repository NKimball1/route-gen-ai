// Exercise the shipped browser controller with a minimal DOM/network adapter.
// Run: node --test tests/frontend.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

async function browser(onLoad) {
  const nodes = new Map();
  function element() {
    return {value: '', textContent: '', disabled: false, hidden: true, style: {}, dataset: {},
      classList: {add() {}, remove() {}, toggle() {}}, focus() {}, addEventListener() {},
      appendChild() {}, append() {}, click() {}, querySelector() {return element();}};
  }
  const calls = [], values = new Map();
  let respond = onLoad || (async () => ({ok: true, status: 200, json: async () => ({current: null, candidates: []})}));
  const map = {setView() {return this;}, removeLayer() {}, fitBounds() {}};
  const context = vm.createContext({
    document: {getElementById(id) {if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id);},
      querySelectorAll() {return [];}, createElement: element},
    localStorage: {getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value)},
    crypto: {randomUUID: () => 'test-session'},
    L: {map: () => map, tileLayer: () => ({addTo() {}})},
    fetch: async (...args) => {calls.push(args); return respond(...args);},
    FormData, URL, Date, Promise, Uint8Array,
    prompt: () => 'new-invite', alert() {},
    setTimeout: callback => setImmediate(callback),
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  calls.length = 0;
  return {context, nodes, calls, values, response: fn => {respond = fn;}};
}

test('an explicit-address request works without a saved start', async () => {
  const app = await browser();
  app.nodes.get('ask').value = '20 mile loop from a public landmark';
  vm.runInContext("document.getElementById('intent').value = 'route'", app.context);
  app.response(async url => ({ok: true, status: 200, json: async () =>
    url === '/api/ask' ? {job: 'j'} : url.startsWith('/api/job/')
      ? {status: 'done', result: {ok: true, summary: 'Done', candidates: []}}
      : {current: null}}));
  await vm.runInContext('send()', app.context);
  const request = app.calls.find(([url]) => url === '/api/ask');
  assert.ok(request);
  assert.equal(JSON.parse(request[1].body).start, null);
  assert.equal(JSON.parse(request[1].body).intent, 'route');
  assert.equal(app.nodes.get('go').disabled, false);
});

test('a missing job stops polling and releases controls', async () => {
  const app = await browser();
  app.nodes.get('ask').value = 'edit my ride';
  app.response(async url => url === '/api/ask'
    ? {ok: true, status: 200, json: async () => ({job: 'missing'})}
    : {ok: false, status: 404, json: async () => ({error: 'no such job'})});
  await vm.runInContext('send()', app.context);
  assert.equal(app.calls.filter(([url]) => url.includes('/api/job/')).length, 1);
  assert.equal(app.nodes.get('go').disabled, false);
  assert.equal(app.nodes.get('banner').textContent, 'no such job');
});

test('all API reads carry session credentials and invite retry uses the new code', async () => {
  const app = await browser();
  let first = true;
  app.response(async () => {
    if (first) {first = false; return {ok: false, status: 401};}
    return {ok: true, status: 200};
  });
  await vm.runInContext("apiFetch('/api/gpx?path=owned')", app.context);
  assert.equal(app.calls[1][1].headers['X-Session-Id'], 'test-session');
  assert.equal(app.calls[1][1].headers['X-Invite-Code'], 'new-invite');
  assert.equal(app.values.get('rg_invite'), 'new-invite');
});

// Live 10-14: a rejected API key failed every plain-English request while the
// page gave no hint until a request had been typed and waited on.
function healthSays(health) {
  return async url => url === '/api/health'
    ? {ok: false, status: 503, json: async () => health}
    : {ok: true, status: 200, json: async () => ({current: null, candidates: []})};
}

test('a rejected API key is flagged as soon as the page loads', async () => {
  const app = await browser(healthSays({ok: false, brouter: 'up', parser: 'key_rejected'}));
  const banner = app.context.document.getElementById('banner');
  assert.match(banner.textContent, /Plain-English requests won't work/);
  assert.match(banner.textContent, /API key was rejected/);
  assert.equal(banner.className, 'warn');
});

test('a stopped routing engine is flagged as soon as the page loads', async () => {
  const app = await browser(healthSays({ok: false, brouter: 'down', parser: 'ok'}));
  assert.match(app.context.document.getElementById('banner').textContent, /routing engine \(BRouter\) is down/);
});

test('a healthy server leaves the banner empty', async () => {
  const app = await browser(healthSays({ok: true, brouter: 'up', parser: 'ok'}));
  assert.equal(app.context.document.getElementById('banner').textContent, '');
});
