const intranetConfig = document.body?.dataset || {};

const FETCH_PROBE_TIMEOUT_MS = 8000;
const IMAGE_PROBE_TIMEOUT_MS = 1200;
const PREFLIGHT_TIMEOUT_MS = 1500;
const MANUAL_PROBE_TIMEOUT_MS = 6000;
const MANUAL_REACHABLE_TTL_MS = 60 * 1000;
const PROBE_INTERVAL_MS = 15000;
const PROBE_RETRY_DELAYS_MS = [350, 1200, 3000];
const jumpButton = document.querySelector("[data-intranet-jump]");
const LOCAL_ACCESS = "local";
const EXTERNAL_ACCESS = "external";
const DETECT_MODE = "detect";
const JUMP_MODE = "jump";
const PROBE_MESSAGE_TYPE = "videorecback:intranet-probe";

const configuredRedirectHost = () => (intranetConfig.intranetRedirectHost || "").trim();

const configuredRedirectPort = () => (intranetConfig.intranetRedirectPort || "").trim();

const configuredRedirectProtocol = () => {
  return intranetConfig.intranetRedirectProtocol === "https" ? "https:" : "http:";
};

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

const isLocalAccess = () => sameTarget();

const currentPageOrigin = () => {
  if (window.location.origin && window.location.origin !== "null") {
    return window.location.origin;
  }
  try {
    return new URL(document.referrer).origin;
  } catch {
    return "";
  }
};

const redirectToIntranet = (replace = false) => {
  const target = intranetOrigin();
  if (!target || sameTarget()) return false;
  target.pathname = window.location.pathname;
  target.search = window.location.search;
  target.hash = window.location.hash;
  if (replace) {
    window.location.replace(target.toString());
  } else {
    window.location.assign(target.toString());
  }
  return true;
};

const hideJumpButton = () => {
  if (!jumpButton || jumpButton.hidden) return;
  jumpButton.disabled = false;
  jumpButton.classList.remove("is-visible");
  jumpButton.classList.add("is-hiding");
  window.setTimeout(() => {
    if (jumpButton.classList.contains("is-visible")) return;
    jumpButton.hidden = true;
    jumpButton.classList.remove("is-hiding");
  }, 360);
};

const revealActionButton = () => {
  if (!jumpButton || isLocalAccess() || sameTarget()) return;
  jumpButton.hidden = false;
  jumpButton.classList.remove("is-hiding");
  if (jumpButton.classList.contains("is-visible")) return;
  window.requestAnimationFrame(() => {
    jumpButton.classList.add("is-visible");
  });
};

const showJumpButton = () => {
  if (intranetConfig.intranetAutoRedirectEnabled === "1" && shouldProbe()) {
    redirectToIntranet(true);
    return;
  }
  if (!jumpButton || isLocalAccess() || sameTarget()) return;
  jumpButton.dataset.mode = JUMP_MODE;
  jumpButton.textContent = "跳转内网";
  jumpButton.title = "内网服务可达，点击切换到内网地址";
  jumpButton.disabled = false;
  revealActionButton();
};

const showDetectButton = (label = "检测内网", title = "点击确认当前浏览器能否直连内网服务") => {
  if (!jumpButton || isLocalAccess() || sameTarget()) return;
  jumpButton.dataset.mode = DETECT_MODE;
  jumpButton.textContent = label;
  jumpButton.title = title;
  jumpButton.disabled = false;
  revealActionButton();
};

const showManualChecking = () => {
  if (!jumpButton) return;
  jumpButton.dataset.mode = DETECT_MODE;
  jumpButton.textContent = "正在检测";
  jumpButton.title = "正在通过内网地址确认连接";
  jumpButton.disabled = true;
  revealActionButton();
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
let manualProbeActive = false;
let manualProbeNonce = "";
let manualProbeWindow = null;
let manualProbeTimer = null;
let manualReachableUntil = 0;

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

const createProbeNonce = () => {
  if (window.crypto?.getRandomValues) {
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`.padEnd(16, "0");
};

const finishManualProbe = (reachable) => {
  if (!manualProbeActive) return;
  manualProbeActive = false;
  manualProbeNonce = "";
  if (manualProbeTimer) {
    window.clearTimeout(manualProbeTimer);
    manualProbeTimer = null;
  }
  try {
    manualProbeWindow?.close();
  } catch {}
  manualProbeWindow = null;
  if (reachable) {
    manualReachableUntil = Date.now() + MANUAL_REACHABLE_TTL_MS;
    retryIndex = 0;
    markProbeState("reachable");
    showJumpButton();
    return;
  }
  markProbeState("manual-unreachable");
  showDetectButton("重新检测", "未收到内网服务确认，点击重试");
};

const startManualProbe = () => {
  const target = intranetOrigin();
  if (!target || !shouldProbe() || manualProbeActive) return;
  manualProbeActive = true;
  manualProbeNonce = createProbeNonce();
  target.pathname = "/intranet/probe";
  target.search = "";
  target.searchParams.set("nonce", manualProbeNonce);
  const openerOrigin = currentPageOrigin();
  if (!openerOrigin) {
    manualProbeActive = false;
    manualProbeNonce = "";
    markProbeState("invalid-origin");
    showDetectButton("重新检测", "无法确认当前页面来源");
    return;
  }
  target.searchParams.set("opener_origin", openerOrigin);
  markProbeState("manual-checking");
  showManualChecking();
  manualProbeWindow = window.open(
    target.toString(),
    "videorecback-intranet-probe",
    "popup=yes,width=420,height=240"
  );
  if (!manualProbeWindow) {
    manualProbeActive = false;
    manualProbeNonce = "";
    markProbeState("popup-blocked");
    showDetectButton("重新检测", "浏览器阻止了探测窗口，请允许此网站打开弹窗后重试");
    return;
  }
  manualProbeTimer = window.setTimeout(() => finishManualProbe(false), MANUAL_PROBE_TIMEOUT_MS);
};

const handleProbeMessage = (event) => {
  const target = intranetOrigin();
  const payload = event.data;
  if (
    !manualProbeActive ||
    !target ||
    event.source !== manualProbeWindow ||
    event.origin !== target.origin ||
    payload?.type !== PROBE_MESSAGE_TYPE ||
    payload?.service !== "videorecback" ||
    payload?.nonce !== manualProbeNonce
  ) {
    return;
  }
  finishManualProbe(true);
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
  if (manualProbeActive) return;
  if (manualReachableUntil > Date.now()) {
    markProbeState("reachable");
    showJumpButton();
    return;
  }
  if (jumpButton?.dataset.mode !== JUMP_MODE) {
    showDetectButton();
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
    if (manualReachableUntil > Date.now()) {
      showJumpButton();
    } else {
      showDetectButton();
    }
    scheduleFastRetry();
  }
};

const runPreflight = () => {
  let finished = false;
  const finish = (reachable) => {
    if (finished) return;
    finished = true;
    window.clearTimeout(timeout);
    if (reachable && shouldProbe() && redirectToIntranet(true)) return;
    const fallback = new URL(window.location.href);
    fallback.searchParams.set("_vbr_skip_intranet", "1");
    window.location.replace(fallback.toString());
  };
  const timeout = window.setTimeout(() => finish(false), PREFLIGHT_TIMEOUT_MS);
  if (!shouldProbe()) {
    finish(false);
    return;
  }
  browserCanReachIntranet().then(finish, () => finish(false));
};

if (intranetConfig.intranetPreflight === "1") {
  runPreflight();
} else {
  jumpButton?.addEventListener("click", () => {
    if (jumpButton.dataset.mode === JUMP_MODE) {
      redirectToIntranet();
      return;
    }
    startManualProbe();
  });
  window.addEventListener("message", handleProbeMessage);
  refreshJumpButton();
  window.setInterval(refreshJumpButton, PROBE_INTERVAL_MS);
  window.addEventListener("online", refreshJumpButton);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshJumpButton();
  });
}
