const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "../app/static/intranet.js"), "utf8");
const flush = () => new Promise((resolve) => setImmediate(resolve));

function start({ preflight = true, auto = true, enabled = true, url, fetchImpl,
  session = { cookie: "", storage: new Map() }, cookiesBlocked = false, storageBlocked = false } = {}) {
  const current = new URL(url || "https://public.example/?source=bookmark#memories");
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
    sessionStorage: {
      getItem(key) { if (storageBlocked) throw new Error("Storage blocked"); return session.storage.get(key); },
      setItem(key, value) { if (storageBlocked) throw new Error("Storage blocked"); session.storage.set(key, value); },
    },
    setTimeout(callback, delay) { timers.set(++nextTimer, { callback, delay }); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
    setInterval() { intervals += 1; },
    requestAnimationFrame(callback) { callback(); },
    addEventListener(type, callback) { listeners.set(type, callback); },
    open(target) { popup = { url: new URL(target), close() {} }; return popup; },
  };
  const document = {
    get cookie() { return cookiesBlocked ? "" : session.cookie; },
    set cookie(value) { if (!cookiesBlocked) session.cookie = value.split(";")[0]; },
    body: { classList: { contains: (name) => name === "home-page" && current.pathname === "/" && !current.searchParams.has("view") }, dataset: {
      intranetEnabled: enabled ? "1" : "0",
      intranetAutoRedirectEnabled: auto ? "1" : "0",
      intranetPreflight: preflight ? "1" : "0",
      intranetSkipProbe: current.searchParams.get("_vbr_skip_intranet") === "1" ? "1" : "0",
      intranetRedirectHost: "192.168.1.10",
      intranetRedirectPort: "8080",
      intranetRedirectProtocol: "http",
    } },
    querySelector: (selector) => !preflight && selector === "[data-intranet-jump]" ? button : null,
    addEventListener(type, callback) { listeners.set(type, callback); },
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
    navigations, images, requests, button, listeners, window, session, timers,
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

test("the first home probe redirects and preserves the full URL", async () => {
  const state = start();
  state.images[0].onload();
  await flush();
  assert.equal(state.navigations.length, 1);
  assert.equal(state.navigations[0].method, "replace");
  assert.equal(state.navigations[0].url.href,
    "http://192.168.1.10:8080/?source=bookmark#memories");
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
  assert.equal(fallback.searchParams.get("source"), "bookmark");
  assert.equal(fallback.hash, "#memories");
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
  const state = start({ preflight: false, url: "https://public.example/settings" });
  assert.equal(state.requests.length, 0);
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

test("direct visits to other pages never probe, even in a fresh session", async () => {
  for (const path of ["/library", "/?view=timeline", "/settings", "/video/1", "/video/1/play?embed=1"]) {
    const state = start({ preflight: false, url: `https://public.example${path}` });
    await flush();
    assert.equal(state.requests.length, 0, path);
    assert.equal(state.images.length, 0, path);
    assert.equal(state.navigations.length, 0, path);
    assert.equal(state.session.cookie, "", "Other pages must not consume the first home attempt");
  }
});

test("a timed-out preflight is remembered through fallback, navigation and home refresh", async () => {
  const first = start();
  first.expire(1500);
  for (const path of ["/?_vbr_skip_intranet=1", "/library", "/settings", "/video/1/play", "/"]) {
    const state = start({ preflight: false, session: first.session, url: `https://public.example${path}` });
    await flush();
    assert.equal(state.requests.length, 0, path);
    assert.equal(state.images.length, 0, path);
    assert.equal(state.navigations.length, 0, path);
  }
});

test("failed home detection has no timer, online or visibility retries and can be retried manually", async () => {
  const state = start({ preflight: false, auto: false, fetchImpl: async () => { throw new Error("Offline"); } });
  state.images[0].onerror();
  await flush();
  assert.equal(state.requests.length, 1);
  assert.equal(state.timers.size, 0);
  assert.equal(state.intervals, 0);
  assert.equal(state.listeners.has("online"), false);
  assert.equal(state.listeners.has("visibilitychange"), false);
  const returning = start({ preflight: false, auto: false, session: state.session });
  assert.equal(returning.requests.length, 0);
  returning.listeners.get("button:click")();
  assert.equal(returning.popup.url.pathname, "/intranet/probe");
  const fresh = start({ preflight: false, auto: false });
  assert.equal(fresh.requests.length, 1, "A new browser session may detect again");
});

test("either session cookie or sessionStorage alone prevents repeated automatic probes", () => {
  for (const options of [{ cookiesBlocked: true }, { storageBlocked: true }]) {
    const first = start(options);
    const returning = start({ ...options, session: first.session });
    assert.equal(returning.requests.length, 0);
    assert.equal(returning.images.length, 0);
    assert.equal(returning.navigations[0].url.searchParams.get("_vbr_skip_intranet"), "1");
  }
});

test("explicitly continuing on the public home skips detection even without prior session state", () => {
  const state = start({ preflight: false, url: "https://public.example/?_vbr_skip_intranet=1" });
  assert.equal(state.requests.length, 0);
  const returning = start({ preflight: false, session: state.session });
  assert.equal(returning.requests.length, 0);
});
