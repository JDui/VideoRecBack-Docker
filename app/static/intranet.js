const intranetConfig = document.body?.dataset || {};

const FETCH_PROBE_TIMEOUT_MS = 8000;
const IMAGE_PROBE_TIMEOUT_MS = 1200;
const PROBE_INTERVAL_MS = 15000;
const PROBE_RETRY_DELAYS_MS = [350, 1200, 3000];
const jumpButton = document.querySelector("[data-intranet-jump]");
const LOCAL_ACCESS = "local";
const EXTERNAL_ACCESS = "external";

const configuredRedirectHost = () => (intranetConfig.intranetRedirectHost || "").trim();

const configuredRedirectPort = () => (intranetConfig.intranetRedirectPort || "").trim();

const configuredRedirectProtocol = () => {
  return intranetConfig.intranetRedirectProtocol === "https" ? "https:" : "http:";
};

const normalizedHost = (host) => {
  return String(host || "").trim().toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
};

const isPrivateIpv4 = (host) => {
  const parts = host.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  return parts[0] === 10 ||
    parts[0] === 127 ||
    (parts[0] === 100 && parts[1] >= 64 && parts[1] <= 127) ||
    (parts[0] === 169 && parts[1] === 254) ||
    (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) ||
    (parts[0] === 192 && parts[1] === 168);
};

const isPrivateIpv6 = (host) => {
  const value = host.split("%", 1)[0];
  if (!value.includes(":")) return false;
  if (value === "::1") return true;
  if (value.startsWith("::ffff:")) return isPrivateIpv4(value.slice(7));
  const firstGroup = Number.parseInt(value.split(":", 1)[0], 16);
  return Number.isInteger(firstGroup) &&
    ((firstGroup >= 0xfc00 && firstGroup <= 0xfdff) ||
      (firstGroup >= 0xfe80 && firstGroup <= 0xfebf));
};

const isPrivateHost = (host) => {
  const value = normalizedHost(host);
  if (!value) return false;
  if (
    value === "localhost" ||
    value.endsWith(".localhost") ||
    value.endsWith(".local") ||
    value.endsWith(".lan") ||
    value.endsWith(".home.arpa") ||
    !value.includes(".") && !value.includes(":")
  ) {
    return true;
  }
  return isPrivateIpv4(value) || isPrivateIpv6(value);
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

const markProbeState = (state) => {
  document.body.dataset.intranetProbe = state;
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
      credentials: "omit",
      mode: "cors",
      referrerPolicy: "no-referrer",
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

const shouldProbe = () => {
  return intranetConfig.intranetEnabled === "1" &&
    !isLocalAccess() &&
    !sameTarget() &&
    intranetOrigin() !== null;
};

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
    markProbeState("local");
    return;
  }
  if (!shouldProbe()) {
    hideJumpButton();
    markProbeState("disabled");
    return;
  }
  if (activeProbe) return;
  markProbeState("checking");
  activeProbe = browserCanReachIntranet();
  const reachable = await activeProbe;
  activeProbe = null;
  if (reachable && shouldProbe()) {
    retryIndex = 0;
    markProbeState("reachable");
    showJumpButton();
  } else {
    markProbeState("unreachable");
    hideJumpButton();
    scheduleFastRetry();
  }
};

refreshJumpButton();
window.setInterval(refreshJumpButton, PROBE_INTERVAL_MS);
window.addEventListener("online", refreshJumpButton);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshJumpButton();
});
