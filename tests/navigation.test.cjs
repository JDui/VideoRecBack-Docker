const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname, '../app/static/navigation.js'), 'utf8');

function setup() {
  const listeners = new Map(), windowListeners = new Map(), frames = [], shown = [], destinations = [];
  const location = { href: 'http://local/', origin: 'http://local', pathname: '/', search: '', assign: target => destinations.push(target), replace: target => destinations.push(['replace', target]) };
  const window = { VideoRecBackLoading: { show: (...args) => shown.push(args) }, dispatchEvent() {}, addEventListener: (name, listener) => windowListeners.set(name, listener) };
  const document = { addEventListener: (name, listener) => listeners.set(name, listener), querySelectorAll: () => [] };
  vm.runInNewContext(source, { window, document, location, URL, CustomEvent: class {}, requestAnimationFrame: callback => frames.push(callback) });
  const click = (href, extras = {}) => {
    const attrs = new Map();
    const link = { href, target: '', hasAttribute: () => false, setAttribute: (name, value) => attrs.set(name, value) };
    const event = { button: 0, target: { closest: () => link }, preventDefault: () => event.defaultPrevented = true, ...extras };
    listeners.get('click')(event);
    return { event, attrs };
  };
  return { click, frames, shown, destinations, window, windowListeners };
}

test('navigation paints target loading feedback before leaving, deduplicates clicks and resets on back', () => {
  const state = setup();
  const { event, attrs } = state.click('http://local/library');
  assert.equal(event.defaultPrevented, true);
  assert.equal(attrs.get('aria-busy'), 'true');
  assert.deepEqual(state.shown, [['http://local/library', true]]);
  assert.deepEqual(state.destinations, []);
  state.click('http://local/settings');
  assert.equal(state.frames.length, 1);
  state.frames.shift()();
  assert.deepEqual(state.destinations, []);
  state.frames.shift()();
  assert.deepEqual(state.destinations, ['http://local/library']);
  state.windowListeners.get('pageshow')();
  state.click('http://local/settings');
  assert.equal(state.shown.length, 2);
});

test('modified clicks, external links, same-page anchors and prevented player clicks retain their behavior', () => {
  const state = setup();
  for (const [url, options] of [['http://local/library', { ctrlKey: true }], ['https://external.test/', {}], ['http://local/#anchor', {}], ['http://local/video/1/play', { defaultPrevented: true }]]) {
    state.click(url, options);
  }
  assert.deepEqual(state.shown, []);
  assert.equal(state.frames.length, 0);
});


test('player return can replace its history entry while retaining immediate loading feedback', () => {
  const state = setup();
  state.window.VideoRecBackNavigate('http://local/library', { replace: true });
  assert.equal(state.shown.length, 1);
  state.frames.shift()(); state.frames.shift()();
  assert.deepEqual(state.destinations, [['replace', 'http://local/library']]);
});
