const intranetConfig = document.body?.dataset || {};

const CHECKED_PARAM = "vbr_intranet_checked";
const HOST_PARAM = "vbr_intranet_host";
const PORT_PARAM = "vbr_intranet_port";
const PROBE_TIMEOUT_MS = 1600;

const cleanHostForUrl = (host) => {
  const text = String(host || "").trim();
  if (!text) return "";
  return text.includes(":") && !text.startsWith("[") ? `[${text}]` : text;
};

const currentPort = () => window.location.port || (window.location.protocol === "https:" ? "443" : "80");

const configuredProbeHost = () => (intranetConfig.intranetProbeHost || "192.168.31.1").trim();

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

const consumeTransferredProbe = () => {
  const url = new URL(window.location.href);
  if (url.searchParams.get(CHECKED_PARAM) !== "1") return false;
  const host = url.searchParams.get(HOST_PARAM) || configuredProbeHost();
  const port = url.searchParams.get(PORT_PARAM) || configuredRedirectPort();
  writeCachedProbe(true, host, port);
  url.searchParams.delete(CHECKED_PARAM);
  url.searchParams.delete(HOST_PARAM);
  url.searchParams.delete(PORT_PARAM);
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  return true;
};

const sameTarget = () => {
  const probeHost = configuredProbeHost();
  const targetPort = configuredRedirectPort() || currentPort();
  return window.location.hostname === probeHost && currentPort() === targetPort;
};

const redirectToIntranet = () => {
  const probeHost = configuredProbeHost();
  if (!probeHost || sameTarget()) return;
  const target = new URL(window.location.href);
  target.hostname = probeHost;
  target.port = configuredRedirectPort() || window.location.port;
  target.searchParams.set(CHECKED_PARAM, "1");
  target.searchParams.set(HOST_PARAM, probeHost);
  target.searchParams.set(PORT_PARAM, configuredRedirectPort() || currentPort());
  window.location.replace(target.toString());
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
  const transferred = consumeTransferredProbe();
  const cached = readCachedProbe();
  if (transferred || cached?.checked) {
    if ((transferred || cached?.isIntranet) && !sameTarget()) redirectToIntranet();
  } else if (sameTarget()) {
    writeCachedProbe(true);
  } else {
    browserReachable().then((isIntranet) => {
      writeCachedProbe(isIntranet);
      if (isIntranet) redirectToIntranet();
    });
  }
}
