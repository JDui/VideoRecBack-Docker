const intranetConfig = document.body?.dataset || {};

const FETCH_PROBE_TIMEOUT_MS = 8000;
const IMAGE_PROBE_TIMEOUT_MS = 1200;
const PROBE_INTERVAL_MS = 15000;
const PROBE_RETRY_DELAYS_MS = [350, 1200, 3000];
const REACHABLE_CACHE_TTL_MS = 10 * 60 * 1000;
const jumpButton = document.querySelector("[data-intranet-jump]");
const LOCAL_ACCESS = "local";
const EXTERNAL_ACCESS = "external";

const configuredRedirectHost = () => (intranetConfig.intranetRedirectHost || "").trim();

const configuredRedirectPort = () => (intranetConfig.intranetRedirectPort || "").trim();

const configuredRedirectProtocol = () => {
  return intranetConfig.intranetRedirectProtocol === "https" ? "https:" : "http:";
};

const isPrivateHost = (host) => {
  const value = String(host || "").trim().toLowerCase();
  if (value === "localhost" || value === "::1" || value.endsWith(".local")) return true;
  const parts = value.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  return parts[0] === 10 || parts[0] === 127 ||
    (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) ||
    (parts[0] === 192 && parts[1] === 168);
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

const reachabilityCacheKey = () => `videorecback-intranet-reachable:${intranetOrigin()?.origin || ""}`;

const hasRecentReachability = () => {
  try {
    const checkedAt = Number(localStorage.getItem(reachabilityCacheKey()) || 0);
    return checkedAt > 0 && Date.now() - checkedAt < REACHABLE_CACHE_TTL_MS;
  } catch {
    return false;
  }
};

const rememberReachability = () => {
  try {
    localStorage.setItem(reachabilityCacheKey(), String(Date.now()));
  } catch {}
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

const fetchHealthProbe = async () => {
  const target = intranetOrigin();
  if (!target) return false;
  target.pathname = "/intranet/health";
  target.searchParams.set("_vbr_probe", String(Date.now()));
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), FETCH_PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(target.toString(), {
      cache: "no-store",
      mode: "cors",
      signal: controller.signal,
      targetAddressSpace: "local",
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

const imageHealthProbe = () => {
  const target = intranetOrigin();
  if (!target) return Promise.resolve(false);
  target.pathname = "/intranet/health.gif";
  target.searchParams.set("_vbr_probe", String(Date.now()));
  return new Promise((resolve) => {
    const image = new Image();
    const timeout = window.setTimeout(() => {
      image.src = "";
      resolve(false);
    }, IMAGE_PROBE_TIMEOUT_MS);
    image.onload = () => {
      window.clearTimeout(timeout);
      resolve(true);
    };
    image.onerror = () => {
      window.clearTimeout(timeout);
      resolve(false);
    };
    image.src = target.toString();
  });
};

const browserCanReachIntranet = async () => {
  const probes = [fetchHealthProbe(), imageHealthProbe()];
  return new Promise((resolve) => {
    let remaining = probes.length;
    for (const probe of probes) {
      probe.then((reachable) => {
        if (reachable) {
          resolve(true);
          return;
        }
        remaining -= 1;
        if (remaining === 0) resolve(false);
      });
    }
  });
};

markAccess();

let activeProbe = null;
let retryIndex = 0;
let retryTimer = null;

const scheduleFastRetry = () => {
  if (retryTimer || retryIndex >= PROBE_RETRY_DELAYS_MS.length) return;
  retryTimer = window.setTimeout(() => {
    retryTimer = null;
    retryIndex += 1;
    refreshJumpButton();
  }, PROBE_RETRY_DELAYS_MS[retryIndex]);
};

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
  showJumpButton();
  if (hasRecentReachability()) return;
  if (activeProbe) return;
  activeProbe = browserCanReachIntranet();
  const reachable = await activeProbe;
  activeProbe = null;
  if (reachable) {
    retryIndex = 0;
    rememberReachability();
    showJumpButton();
  } else {
    scheduleFastRetry();
  }
};

refreshJumpButton();
window.setInterval(refreshJumpButton, PROBE_INTERVAL_MS);
window.addEventListener("online", refreshJumpButton);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshJumpButton();
});
