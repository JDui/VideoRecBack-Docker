const intranetConfig = document.body?.dataset || {};

const PROBE_TIMEOUT_MS = 1600;
const PROBE_INTERVAL_MS = 30000;
const jumpButton = document.querySelector("[data-intranet-jump]");
const LOCAL_ACCESS = "local";
const EXTERNAL_ACCESS = "external";

const currentPort = () => window.location.port || (window.location.protocol === "https:" ? "443" : "80");

const configuredProbeHost = () => (intranetConfig.intranetProbeHost || "192.168.31.1").trim();

const configuredRedirectHost = () => (intranetConfig.intranetRedirectHost || configuredProbeHost()).trim();

const configuredRedirectPort = () => (intranetConfig.intranetRedirectPort || "").trim();

const isPrivateHost = (host) => {
  const match = String(host || "").trim().match(/^192\.168\.(\d{1,3})\.(\d{1,3})$/);
  if (!match) return false;
  return match.slice(1).every((part) => Number(part) >= 0 && Number(part) <= 255);
};

const isLocalAccess = () => isPrivateHost(window.location.hostname);

const markAccess = () => {
  document.body.dataset.intranetAccess = isLocalAccess() ? LOCAL_ACCESS : EXTERNAL_ACCESS;
};

const cacheKey = (host = configuredProbeHost(), port = configuredRedirectPort()) => {
  return `videorecback-intranet:${window.location.protocol}:${host}:${port || currentPort()}`;
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
  target.protocol = "http:";
  target.hostname = redirectHost;
  target.port = configuredRedirectPort();
  window.location.assign(target.toString());
};

const hideJumpButton = () => {
  if (!jumpButton || jumpButton.hidden) return;
  jumpButton.classList.remove("is-visible");
  jumpButton.classList.add("is-hiding");
  window.setTimeout(() => {
    if (jumpButton.classList.contains("is-visible")) return;
    jumpButton.hidden = true;
    jumpButton.classList.remove("is-hiding");
  }, 360);
};

const showJumpButton = () => {
  if (!jumpButton || isLocalAccess() || sameTarget()) return;
  if (jumpButton.classList.contains("is-visible")) return;
  jumpButton.hidden = false;
  jumpButton.classList.remove("is-hiding");
  window.requestAnimationFrame(() => {
    jumpButton.classList.add("is-visible");
  });
  if (jumpButton.dataset.bound === "1") return;
  jumpButton.dataset.bound = "1";
  jumpButton.addEventListener("click", redirectToIntranet);
};

const serverReachable = async () => {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(`/intranet/probe?_vbr_probe=${Date.now()}`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) return false;
    const payload = await response.json();
    return payload.ok === true;
  } catch {
    return false;
  } finally {
    window.clearTimeout(timeout);
  }
};

markAccess();

const refreshJumpButton = async () => {
  markAccess();
  if (isLocalAccess()) {
    hideJumpButton();
    writeCachedProbe(true);
    return;
  }
  if (intranetConfig.intranetEnabled !== "1" || sameTarget()) {
    hideJumpButton();
    return;
  }
  const reachable = await serverReachable();
  writeCachedProbe(reachable);
  if (reachable) {
    showJumpButton();
  } else {
    hideJumpButton();
  }
};

refreshJumpButton();
window.setInterval(refreshJumpButton, PROBE_INTERVAL_MS);
