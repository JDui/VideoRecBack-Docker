const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/static/player.js'), 'utf8').replace(/^import [^\n]+\n/, '');
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.listeners = new Map();
    this.hidden = false;
    this.style = { setProperty() {} };
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  addEventListener(name, callback, options = {}) {
    const entries = this.listeners.get(name) || [];
    entries.push({ callback, once: options.once });
    this.listeners.set(name, entries);
  }
  emit(name, event = {}) {
    for (const entry of [...(this.listeners.get(name) || [])]) {
      if (entry.once) this.listeners.set(name, this.listeners.get(name).filter(item => item !== entry));
      entry.callback(event);
    }
  }
  setAttribute() {}
  removeAttribute(name) { if (name === 'src') this.src = ''; }
  closest() { return null; }
}

function fixture({ quality = 'original', bitDepth = 10, playError, info = { hdr: false }, infoPromise, frameCallback = true } = {}) {
  let now = 1000;
  let timerId = 0;
  let frameId = 0;
  const timers = new Map();
  const frames = new Map();
  const requests = [];
  const instances = [];
  const video = new Element({ mediaBase: '/media/1' });
  Object.assign(video, { paused: true, ended: false, currentTime: 0, duration: 120,
    volume: .2, playbackRate: 1, defaultPlaybackRate: 1, buffered: { length: 0 }, playCalls: 0, loads: 0,
    canPlayType: () => '',
    load() { this.loads++; this.currentTime = 0; this.error = null; },
    pause() { this.paused = true; this.emit('pause'); },
    play() { this.playCalls++; return playError ? Promise.reject(playError) : new Promise(() => {}); },
  });
  if (frameCallback) {
    video.requestVideoFrameCallback = cb => { frames.set(++frameId, cb); return frameId; };
    video.cancelVideoFrameCallback = id => frames.delete(id);
  }
  const shell = new Element({ videoType: 'flat', videoBitDepth: String(bitDepth), duration: '120', defaultQuality: quality });
  const overlay = new Element();
  const button = new Element();
  const qualityValue = new Element();
  const qualityButtons = ['original', 'ultra', 'low', 'high'].map(qualityOption => new Element({ qualityOption }));
  const elements = new Map([
    ['.player-shell', shell], ['[data-transcode-overlay]', overlay],
    ['[data-flat-play]', button], ['[data-quality-value]', qualityValue],
  ]);
  const document = new Element();
  Object.assign(document, { hidden: false, activeElement: null,
    querySelector: selector => elements.get(selector) || null,
    querySelectorAll: selector => selector === '[data-quality-option]' ? qualityButtons : [],
    getElementById: id => id === 'videoPlayer' ? video : null,
  });
  class Hls {
    static isSupported() { return true; }
    static Events = { MEDIA_ATTACHED: 'attached', MANIFEST_PARSED: 'manifest', ERROR: 'error' };
    static ErrorTypes = { NETWORK_ERROR: 'network', MEDIA_ERROR: 'media' };
    constructor() { this.handlers = {}; this.destroyed = false; instances.push(this); }
    on(name, cb) { this.handlers[name] = cb; }
    attachMedia() { this.handlers.attached(); }
    loadSource(url) { this.url = url; }
    destroy() { this.destroyed = true; }
    startLoad() {}
    recoverMediaError() {}
  }
  const window = new Element();
  Object.assign(window, { Hls, MediaSource: { isTypeSupported: () => true },
    setTimeout(cb, delay) { timers.set(++timerId, { cb, at: now + delay }); return timerId; },
    clearTimeout: id => timers.delete(id), setInterval: () => ++timerId, clearInterval() {},
    requestAnimationFrame() {},
  });
  const context = vm.createContext({ window, document, Element, URLSearchParams, URL, Blob, Date: { now: () => now },
    navigator: { sendBeacon: url => { requests.push(url); return true; } },
    createPlayerAudio: () => ({ resume() {}, suspend() {}, close() {}, getVolume: () => video.volume }),
    fetch: async url => {
      requests.push(url);
      return url.endsWith('/playback-info') && infoPromise ? infoPromise : { ok: true, json: async () => info };
    },
  });
  vm.runInContext(source, context);
  return { video, overlay, document, window, qualityButtons, qualityValue, instances, requests,
    click: () => button.emit('click'),
    frame() { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(cb => cb()); },
    async advance(ms) {
      const end = now + ms;
      while (true) {
        const next = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at; timers.delete(next[0]); next[1].cb(); await flush();
      }
      now = end; await flush();
    },
  };
}

test('first original play calls play immediately instead of waiting for canplay', () => {
  const f = fixture(); f.click();
  assert.equal(f.video.src, '/media/1');
  assert.equal(f.video.playCalls, 1);
  assert.equal(f.overlay.hidden, false);
});

test('10bit startup stuck while paused and without any media event falls back once', async () => {
  const f = fixture(); f.click();
  assert.equal(f.video.paused, true);
  await f.advance(15000);
  assert.equal(f.video.dataset.currentQuality, 'ultra');
  assert.equal(f.instances.length, 1);
  assert.equal(f.qualityValue.textContent, '超清');
  await f.advance(90000);
  assert.equal(f.instances.length, 1, 'Timeout must not create an endless transcode loop');
  assert.match(f.overlay.textContent, /启动超时/);
});

test('play rejection triggers 10bit fallback without requiring an error event', async () => {
  const f = fixture({ playError: { name: 'NotSupportedError' } }); f.click(); await flush();
  assert.equal(f.video.dataset.currentQuality, 'ultra');
  assert.equal(f.instances.length, 1);
});

test('autoplay denial asks for another click instead of changing quality', async () => {
  const f = fixture({ playError: { name: 'NotAllowedError' } }); f.click(); await flush();
  await f.advance(20000);
  assert.equal(f.video.dataset.currentQuality, 'original');
  assert.match(f.overlay.textContent, /再次点击播放/);
  f.click(); assert.equal(f.video.playCalls, 2);
});

test('a rendered first frame cancels fallback; canplay alone does not', async () => {
  const f = fixture(); f.click(); f.video.emit('canplay');
  await f.advance(14000); f.frame(); await f.advance(2000);
  assert.equal(f.video.dataset.currentQuality, 'original');
  assert.equal(f.overlay.hidden, true);
  const stalled = fixture(); stalled.click(); stalled.video.emit('canplay');
  await stalled.advance(15000);
  assert.equal(stalled.video.dataset.currentQuality, 'ultra');
});

test('cancelling startup keeps a late canplay or timer from restarting playback', async () => {
  const f = fixture(); f.click(); f.click(); f.video.emit('canplay');
  f.video.error = { code: 3 }; f.video.emit('error');
  await f.advance(20000);
  assert.equal(f.video.playCalls, 1);
  assert.equal(f.video.dataset.currentQuality, 'original');
  assert.equal(f.overlay.hidden, true);
  assert.equal(f.instances.length, 0);
});

test('switching quality while still paused preserves the pending play request', async () => {
  const f = fixture(); f.click(); f.qualityButtons[2].emit('click'); await flush();
  f.instances[0].handlers.manifest();
  assert.equal(f.video.dataset.currentQuality, 'low');
  assert.equal(f.video.playCalls, 2);
  await f.advance(15000);
  assert.equal(f.instances.length, 1);
});

test('HDR first-segment preparation is not restarted or stopped by short stall timers', async () => {
  const f = fixture({ quality: 'ultra', info: { hdr: true, format: 'HLG' } });
  f.click(); await flush(); f.instances[0].handlers.manifest();
  f.video.paused = false; f.video.emit('waiting'); f.video.emit('stalled');
  // An old pause event from load() can arrive while first-segment loading is pending.
  f.video.emit('pause');
  await f.advance(75000);
  assert.equal(f.instances.length, 1);
  assert.equal(f.instances[0].destroyed, false);
  assert.equal(f.requests.some(url => url.endsWith('/stop')), false);
  f.frame(); await f.advance(20000);
  assert.equal(f.overlay.hidden, true);
});

test('an old format request cannot restart playback after its startup deadline', async () => {
  let resolve;
  const infoPromise = new Promise(r => { resolve = r; });
  const f = fixture({ quality: 'ultra', infoPromise }); f.click();
  await f.advance(90000);
  resolve({ ok: true, json: async () => ({ hdr: false }) }); await flush();
  assert.equal(f.instances.length, 0);
  assert.match(f.overlay.textContent, /启动超时/);
});

test('8bit startup does not silently switch quality and old browsers clear on playing', async () => {
  const normal = fixture({ bitDepth: 8, frameCallback: false }); normal.click(); normal.video.emit('playing');
  await normal.advance(16000);
  assert.equal(normal.overlay.hidden, true);
  const stalled = fixture({ bitDepth: 8 }); stalled.click(); await stalled.advance(15000);
  assert.equal(stalled.video.dataset.currentQuality, 'original');
  assert.equal(stalled.instances.length, 0);
});
