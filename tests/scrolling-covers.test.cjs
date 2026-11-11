const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const source = readFileSync(resolve(__dirname, '../app/static/scrolling-covers.js'), 'utf8');

function fixture({ columns = 2, rows = 2, width = 1024, height = 576, sourceRatio, enabled = true, currentLayout = false } = {}) {
  const timers = new Map();
  const windowEvents = {};
  const documentEvents = {};
  let timerId = 0;
  let intersection;
  let mutation;
  const imageEvents = {};
  const image = {
    naturalWidth: width, naturalHeight: height, complete: true, src: '/cover.webp',
    addEventListener: (type, callback) => { imageEvents[type] = callback; },
    removeEventListener: type => { delete imageEvents[type]; },
  };
  const classes = new Set();
  const children = [];
  const wrapper = {
    dataset: { coverColumns: String(columns), coverRows: String(rows), coverLayoutCurrent: currentLayout ? '1' : '0', ...(sourceRatio ? { coverSourceRatio: String(sourceRatio) } : {}) },
    classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
    clientWidth: 320, clientHeight: 180, isConnected: true,
    hasAttribute: name => name === 'data-cover-preview',
    querySelector: () => image,
    querySelectorAll: () => [],
    matches: () => true,
    insertBefore: child => children.push(child),
  };
  const document = {
    hidden: false,
    body: { dataset: { scrollingCoverEnabled: enabled ? '1' : '0', scrollingCoverIntervalMs: '1500' } },
    querySelectorAll: () => [wrapper],
    createElement: () => ({ style: {}, children: [], setAttribute() {}, append(child) { this.children.push(child); }, remove() {} }),
    addEventListener: (type, callback) => { documentEvents[type] = callback; },
  };
  const window = { addEventListener: (type, callback) => { windowEvents[type] = callback; } };
  vm.runInNewContext(source, {
    document, window,
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
    clearTimeout: id => timers.delete(id),
    IntersectionObserver: class {
      constructor(callback) { intersection = callback; }
      observe() {} unobserve() {}
    },
    ResizeObserver: class { observe() {} unobserve() {} },
    MutationObserver: class {
      constructor(callback) { mutation = callback; }
      observe() {} disconnect() {}
    },
  });
  return {
    wrapper, document, window, timers, classes, image, imageEvents,
    frame: () => children[0]?.children[0],
    visible(value) { intersection([{ target: wrapper, isIntersecting: value }]); },
    tick() {
      assert.equal(timers.size, 1);
      const [id, timer] = timers.entries().next().value;
      timers.delete(id);
      timer.callback();
    },
    hide(value) { document.hidden = value; documentEvents.visibilitychange(); },
    page(type) { windowEvents[type](); },
    remove() { wrapper.isConnected = false; mutation([{ addedNodes: [] }]); },
  };
}

test('four-frame collage plays in row order and loops, pausing outside the viewport and hidden tabs', () => {
  const player = fixture();
  assert.equal(player.timers.size, 0);
  player.visible(true);
  assert.equal(player.frame().style.backgroundPosition, '0px 0px');
  player.tick();
  assert.equal(player.frame().style.backgroundPosition, '-320px 0px');
  player.tick();
  assert.equal(player.frame().style.backgroundPosition, '0px -180px');
  player.tick();
  assert.equal(player.frame().style.backgroundPosition, '-320px -180px');
  player.tick();
  assert.equal(player.frame().style.backgroundPosition, '0px 0px');
  player.visible(false);
  assert.equal(player.timers.size, 0);
  player.visible(true);
  player.hide(true);
  assert.equal(player.timers.size, 0);
  player.hide(false);
  assert.equal(player.timers.size, 1);
  player.page('pagehide');
  assert.equal(player.timers.size, 0);
  player.page('pageshow');
  assert.equal(player.timers.size, 1);
  player.remove();
  assert.equal(player.timers.size, 0);
});

test('portrait strips retain complete frames and loop at three or four frames', () => {
  for (const [columns, width, ratio] of [[3, 486, 9 / 16], [4, 864, 3 / 4]]) {
    const player = fixture({ columns, rows: 1, width, height: 288, sourceRatio: ratio });
    player.visible(true);
    assert.equal(player.frame().style.height, '180px');
    assert.ok(Math.abs(parseFloat(player.frame().style.width) / 180 - ratio) < 0.001);
    for (let index = 1; index < columns; index++) {
      player.tick();
      assert.ok(parseFloat(player.frame().style.backgroundPosition) < 0);
    }
    player.tick();
    assert.equal(player.frame().style.backgroundPosition, '0px 0px');
  }
});

test('preview responds immediately to speed and enable changes, and restores the collage when disabled', () => {
  const player = fixture({ enabled: false });
  player.visible(true);
  assert.equal(player.classes.has('is-scrolling-cover'), false);
  const api = player.window.VideoRecBackScrollingCovers;
  api.setPreview(player.wrapper, { enabled: true, intervalMs: 700 });
  assert.equal(player.classes.has('is-scrolling-cover'), true);
  assert.equal([...player.timers.values()][0].delay, 700);
  player.tick();
  api.setPreview(player.wrapper, { enabled: true, intervalMs: 2400 });
  assert.equal([...player.timers.values()][0].delay, 2400);
  assert.equal(player.frame().style.backgroundPosition, '0px 0px');
  api.setPreview(player.wrapper, { enabled: false, intervalMs: 2400 });
  assert.equal(player.classes.has('is-scrolling-cover'), false);
  assert.equal(player.timers.size, 0);
});

test('legacy portrait covers use their cached 2x2 layout; failed images remain unchanged', () => {
  const player = fixture({ columns: 3, rows: 1, sourceRatio: 9 / 16 });
  player.visible(true);
  player.tick();
  player.tick();
  assert.equal(player.frame().style.backgroundPosition, '0px -180px');
  player.imageEvents.error();
  assert.equal(player.classes.has('is-scrolling-cover'), false);
  assert.equal(player.timers.size, 0);
});

test('current tall-phone strips near 16:9 retain four horizontal frames', () => {
  for (const [width, height, sourceRatio] of [[520, 288, 1080 / 2400], [512, 288, 4 / 9]]) {
    const player = fixture({ columns: 4, rows: 1, width, height, sourceRatio, currentLayout: true });
    player.visible(true);
    for (let index = 0; index < 4; index++) {
      assert.equal(player.frame().style.backgroundPosition.split(' ')[1], '0px');
      player.tick();
    }
    assert.equal(player.frame().style.backgroundPosition, '0px 0px');
  }
});
