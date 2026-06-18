const intranetConfig = document.body?.dataset || {};

const PROBE_TIMEOUT_MS = 1600;
const jumpButton = document.querySelector("[data-intranet-jump]");

const cleanHostForUrl = (host) => {
  const text = String(host || "").trim();
  if (!text) return "";
  return text.includes(":") && !text.startsWith("[") ? `[${text}]` : text;
};

const currentPort = () => window.location.port || (window.location.protocol === "https:" ? "443" : "80");

const configuredProbeHost = () => (intranetConfig.intranetProbeHost || "192.168.31.1").trim();

const configuredRedirectHost = () => (intranetConfig.intranetRedirectHost || configuredProbeHost()).trim();

const configuredRedirectPort = () => (intranetConfig.intranetRedirectPort || "").trim();

const cacheKey = (host = configuredProbeHost(), port = configuredRedirectPort()) => {
  return `videorecback-intranet:${window.location.protocol}:${host}:${port || currentPort()}`;
};

const readCachedProbe = () => {
  try {
    return JSON.parse(sessionStorage.getItem(cacheKey()) || "null");
  } catch {
    sessionStorage.removeItem(cacheKey());
    return null;
  }
};

const writeCachedProbe = (isIntranet, host = configuredProbeHost(), port = configuredRedirectPort()) => {
  try {
    sessionStorage.setItem(
      cacheKey(host, port),
      JSON.stringify({
        checked: true,
        isIntranet: Boolean(isIntranet),
        host,
        port: port || currentPort(),
        checkedAt: Date.now(),
      })
    );
  } catch {}
};

const sameTarget = () => {
  const redirectHost = configuredRedirectHost();
  const targetPort = configuredRedirectPort() || currentPort();
  return window.location.hostname === redirectHost && currentPort() === targetPort;
};

const redirectToIntranet = () => {
  const redirectHost = configuredRedirectHost();
  if (!redirectHost || sameTarget()) return;
  const target = new URL(window.location.href);
  target.hostname = redirectHost;
  target.port = configuredRedirectPort() || window.location.port;
  window.location.assign(target.toString());
};

const showJumpButton = () => {
  if (!jumpButton || sameTarget()) return;
  jumpButton.hidden = false;
  jumpButton.addEventListener("click", redirectToIntranet, { once: true });
};

const probeUrl = () => {
  const host = cleanHostForUrl(configuredProbeHost());
  if (!host) return "";
  const url = new URL(`${window.location.protocol}//${host}/`);
  url.searchParams.set("_vbr_probe", String(Date.now()));
  return url.toString();
};

const browserReachable = async () => {
  const url = probeUrl();
  if (!url) return false;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    await fetch(url, {
      cache: "no-store",
      credentials: "omit",
      mode: "no-cors",
      signal: controller.signal,
    });
    return true;
  } catch {
    return false;
  } finally {
    window.clearTimeout(timeout);
  }
};

if (intranetConfig.intranetEnabled === "1") {
  const cached = readCachedProbe();
  if (cached?.checked) {
    if (cached.isIntranet) showJumpButton();
  } else if (sameTarget()) {
    writeCachedProbe(true);
  } else {
    browserReachable().then((isIntranet) => {
      writeCachedProbe(isIntranet);
      if (isIntranet) showJumpButton();
    });
  }
}
