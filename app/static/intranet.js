const intranetConfig = document.body?.dataset || {};

const PROBE_TIMEOUT_MS = 1600;
const PROBE_INTERVAL_MS = 30000;
const jumpButton = document.querySelector("[data-intranet-jump]");
const LOCAL_ACCESS = "local";
const EXTERNAL_ACCESS = "external";

const configuredRedirectHost = () => (intranetConfig.intranetRedirectHost || "").trim();

const configuredRedirectPort = () => (intranetConfig.intranetRedirectPort || "").trim();

const configuredRedirectProtocol = () => {
  return intranetConfig.intranetRedirectProtocol === "https" ? "https:" : "http:";
};

const isPrivateHost = (host) => {
  const match = String(host || "").trim().match(/^192\.168\.(\d{1,3})\.(\d{1,3})$/);
  if (!match) return false;
  return match.slice(1).every((part) => Number(part) >= 0 && Number(part) <= 255);
};

const isLocalAccess = () => isPrivateHost(window.location.hostname);

const markAccess = () => {
  document.body.dataset.intranetAccess = isLocalAccess() ? LOCAL_ACCESS : EXTERNAL_ACCESS;
};

const intranetOrigin = () => {
  const redirectHost = configuredRedirectHost();
  if (!redirectHost) return null;
  try {
    const host = redirectHost.includes(":") && !redirectHost.startsWith("[")
      ? `[${redirectHost}]`
      : redirectHost;
    const target = new URL(`${configuredRedirectProtocol()}//${host}`);
    target.port = configuredRedirectPort();
    return target;
  } catch {
    return null;
  }
};

const sameTarget = () => {
  const target = intranetOrigin();
  return target?.origin === window.location.origin;
};

const redirectToIntranet = () => {
  const target = intranetOrigin();
  if (!target || sameTarget()) return;
  target.pathname = window.location.pathname;
  target.search = window.location.search;
  target.hash = window.location.hash;
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

const browserCanReachIntranet = async () => {
  const target = intranetOrigin();
  if (!target) return false;
  target.pathname = "/intranet/health";
  target.searchParams.set("_vbr_probe", String(Date.now()));
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(target.toString(), {
      cache: "no-store",
      mode: "cors",
      signal: controller.signal,
    });
    if (!response.ok) return false;
    const payload = await response.json();
    return payload.ok === true && payload.service === "videorecback";
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
    return;
  }
  if (intranetConfig.intranetEnabled !== "1" || sameTarget()) {
    hideJumpButton();
    return;
  }
  const reachable = await browserCanReachIntranet();
  if (reachable) {
    showJumpButton();
  } else {
    hideJumpButton();
  }
};

refreshJumpButton();
window.setInterval(refreshJumpButton, PROBE_INTERVAL_MS);
window.addEventListener("online", refreshJumpButton);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshJumpButton();
});
