const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(require.resolve('../app/static/library-scan.js'), 'utf8');
const tick = () => new Promise(setImmediate);

function setup(responses, initiallyRunning = false) {
  const events = {}, requests = [], completed = [], timers = new Map(), classes = new Set();
  const label = { textContent: '' };
  const button = { disabled: false, classList: { toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); } }, setAttribute() {}, removeAttribute() {} };
  const form = { action: '/scan', dataset: { scanRunning: initiallyRunning ? '1' : '0' }, querySelector: selector => selector.includes('button') ? button : label, addEventListener: (name, handler) => events[name] = handler };
  const document = { hidden: false, querySelector: () => form, addEventListener: (name, handler) => events[name] = handler };
  const context = { document, window: { dispatchEvent: event => completed.push(event.type) }, CustomEvent: class { constructor(type) { this.type = type; } }, fetch: async (url, options) => {
    requests.push({ url, options });
    const response = responses.shift();
    if (response instanceof Error) throw response;
    return { ok: response.ok !== false, json: async () => response };
  }, setTimeout: callback => { const id = {}; timers.set(id, callback); return id; }, clearTimeout: id => timers.delete(id) };
  vm.runInNewContext(source, context);
  return { events, requests, completed, timers, button, label, document, submit: () => events.submit({ preventDefault() {} }) };
}

test('scan stays asynchronous, prevents duplicate submissions and reports completion once', async () => {
  const ui = setup([{ scanning: true }, { scanning: true, indexing: true }, { scanning: false }]);
  await ui.submit();
  await tick();
  assert.equal(ui.requests[0].options.headers.Accept, 'application/json');
  assert.equal(ui.button.disabled, true);
  assert.equal(ui.label.textContent, '建立索引中');
  await ui.submit();
  assert.equal(ui.requests.filter(request => request.url === '/scan').length, 1);
  await [...ui.timers.values()][0]();
  assert.deepEqual(ui.completed, ['videorecback:scan-complete']);
  assert.equal(ui.button.disabled, false);
  assert.equal(ui.timers.size, 0);
});

test('failed submission can retry and transient polling failure does not report completion', async () => {
  const ui = setup([{ ok: false }, { scanning: true }, new Error('offline'), { scanning: false }]);
  await ui.submit();
  assert.equal(ui.button.disabled, false);
  assert.match(ui.label.textContent, /失败/);
  await ui.submit();
  await tick();
  assert.equal(ui.button.disabled, true);
  assert.equal(ui.completed.length, 0);
  await [...ui.timers.values()][0]();
  assert.equal(ui.completed.length, 1);
});

test('hidden page pauses polling and resumes the existing scan when visible', async () => {
  const ui = setup([{ scanning: true, indexing: false, pending_media: 3 }, { scanning: false }], true);
  await tick();
  assert.equal(ui.label.textContent, '处理媒体 3');
  ui.document.hidden = true;
  ui.events.visibilitychange();
  assert.equal(ui.timers.size, 0);
  ui.document.hidden = false;
  ui.events.visibilitychange();
  await tick();
  assert.equal(ui.completed.length, 1);
});
