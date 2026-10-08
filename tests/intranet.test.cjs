const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "../app/static/intranet.js"), "utf8");
const flush = () => new Promise((resolve) => setImmediate(resolve));

function start({ preflight = true, auto = true, enabled = true, url, fetchImpl } = {}) {
  const current = new URL(url || "https://public.example/library?view=timeline&q=family#timeline-2026-01-01");
  const navigations = [];
  const timers = new Map();
  const listeners = new Map();
  const images = [];
  const requests = [];
  let nextTimer = 0;
  let intervals = 0;
  let popup;
  const button = {
    dataset: {}, hidden: false,
    classList: { add() {}, remove() {}, contains: () => true },
    addEventListener(type, callback) { listeners.set(`button:${type}`, callback); },
  };
  const location = {
    href: current.href, origin: current.origin, pathname: current.pathname,
    search: current.search, hash: current.hash,
    replace(target) { navigations.push({ method: "replace", url: new URL(target) }); },
    assign(target) { navigations.push({ method: "assign", url: new URL(target) }); },
  };
  const window = {
    location,
    setTimeout(callback, delay) { timers.set(++nextTimer, { callback, delay }); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
    setInterval() { intervals += 1; },
    requestAnimationFrame(callback) { callback(); },
    addEventListener(type, callback) { listeners.set(type, callback); },
    open(target) { popup = { url: new URL(target), close() {} }; return popup; },
  };
  const document = {
    body: { dataset: {
      intranetEnabled: enabled ? "1" : "0",
      intranetAutoRedirectEnabled: auto ? "1" : "0",
      intranetPreflight: preflight ? "1" : "0",
      intranetRedirectHost: "192.168.1.10",
      intranetRedirectPort: "8080",
      intranetRedirectProtocol: "http",
    } },
    querySelector: () => preflight ? null : button,
    addEventListener() {},
  };
  const context = vm.createContext({
    window, document, URL, AbortController, Uint8Array,
    Image: class { constructor() { images.push(this); } },
    fetch(target, options) {
      requests.push({ target, options });
      return fetchImpl ? fetchImpl() : new Promise(() => {});
    },
  });
  vm.runInContext(script, context);
  return {
    navigations, images, requests, button, listeners, window,
    get intervals() { return intervals; },
    get popup() { return popup; },
    expire(delay) {
      const entry = [...timers].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, `Expected a ${delay} ms timer`);
      timers.delete(entry[0]);
      entry[1].callback();
    },
  };
}

const healthResponse = (payload = { ok: true, service: "videorecback" }) => ({
  ok: true, json: async () => payload,
});

test("a fast image probe redirects before starting page polling and preserves the full URL", async () => {
  const state = start();
  state.images[0].onload();
  await flush();
  assert.equal(state.navigations.length, 1);
  assert.equal(state.navigations[0].method, "replace");
  assert.equal(state.navigations[0].url.href,
    "http://192.168.1.10:8080/library?view=timeline&q=family#timeline-2026-01-01");
  assert.equal(state.intervals, 0);
});

test("a verified service response redirects even if the image probe has not finished", async () => {
  const state = start({ fetchImpl: async () => healthResponse() });
  await flush();
  assert.equal(state.navigations[0].url.origin, "http://192.168.1.10:8080");
});

test("failed probes and an unrelated service return to the original page", async () => {
  const state = start({ fetchImpl: async () => healthResponse({ ok: true, service: "other" }) });
  state.images[0].onerror();
  await flush();
  const fallback = state.navigations[0].url;
  assert.equal(fallback.origin, "https://public.example");
  assert.equal(fallback.searchParams.get("_vbr_skip_intranet"), "1");
  assert.equal(fallback.searchParams.get("view"), "timeline");
  assert.equal(fallback.searchParams.get("q"), "family");
  assert.equal(fallback.hash, "#timeline-2026-01-01");
});

test("the preflight timeout falls back once and ignores a late successful response", async () => {
  let resolveFetch;
  const state = start({ fetchImpl: () => new Promise((resolve) => { resolveFetch = resolve; }) });
  state.expire(1500);
  resolveFetch(healthResponse());
  state.images[0].onload();
  await flush();
  assert.equal(state.navigations.length, 1);
  assert.equal(state.navigations[0].url.origin, "https://public.example");
});

test("auto redirect remains off by default and the existing jump button still works", async () => {
  const state = start({ preflight: false, auto: false, fetchImpl: async () => healthResponse() });
  await flush();
  assert.equal(state.navigations.length, 0);
  assert.equal(state.button.textContent, "跳转内网");
  state.listeners.get("button:click")();
  assert.equal(state.navigations[0].method, "assign");
});

test("disabled detection and a matching local origin do not probe or redirect", async () => {
  for (const options of [{ enabled: false }, { url: "http://192.168.1.10:8080/settings" }]) {
    const state = start({ preflight: false, ...options });
    await flush();
    assert.equal(state.requests.length, 0);
    assert.equal(state.navigations.length, 0);
  }
});

test("manual verification redirects automatically only after validating the handshake", async () => {
  const state = start({ preflight: false });
  state.listeners.get("button:click")();
  const message = {
    source: state.popup, origin: "http://192.168.1.10:8080",
    data: { type: "videorecback:intranet-probe", service: "videorecback", nonce: "wrong" },
  };
  state.listeners.get("message")(message);
  assert.equal(state.navigations.length, 0);
  message.data.nonce = state.popup.url.searchParams.get("nonce");
  state.listeners.get("message")(message);
  assert.equal(state.navigations.length, 1);
  assert.equal(state.navigations[0].method, "replace");
});
