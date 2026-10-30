const intranetConfig = document.body?.dataset || {};

const FETCH_PROBE_TIMEOUT_MS = 1500;
const IMAGE_PROBE_TIMEOUT_MS = 1200;
const PREFLIGHT_TIMEOUT_MS = 1500;
const AUTO_PROBE_SESSION_KEY = "vbr_intranet_checked";
const jumpButton = document.querySelector("[data-intranet-jump]");
const LOCAL_ACCESS = "local";
const EXTERNAL_ACCESS = "external";
const DETECT_MODE = "detect";
const JUMP_MODE = "jump";

const configuredRedirectHost = () => (intranetConfig.intranetRedirectHost || "").trim();

const configuredRedirectPort = () => (intranetConfig.intranetRedirectPort || "").trim();

const configuredRedirectProtocol = () => {
  return intranetConfig.intranetRedirectProtocol === "https" ? "https:" : "http:";
};

const markAccess = () => {
  document.body.dataset.intranetAccess = isLocalAccess() ? LOCAL_ACCESS : EXTERNAL_ACCESS;
  const status = document.querySelector('[data-intranet-current-status]');
  const hint = document.querySelector('[data-intranet-external-hint]');
  const enabled = intranetConfig.intranetEnabled === '1' && Boolean(configuredRedirectHost());
  if (status) status.textContent = isLocalAccess() ? '当前已通过内网访问。' : enabled ? '当前通过其他地址访问；自动检测仅在当前窗口首次进入首页时进行；未自动切换时，可点击顶部“连接内网”。' : '内网直连检测未启用或目标地址尚未配置；修改后请保存设置。';
  if (hint) hint.hidden = !enabled || isLocalAccess();
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

const redirectToIntranet = (replace = false) => {
  const target = intranetOrigin();
  if (!target || sameTarget()) return false;
  target.pathname = window.location.pathname;
  target.search = window.location.search;
  target.searchParams.delete("_vbr_skip_intranet");
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

const showConnectButton = () => {
  if (!jumpButton || isLocalAccess() || sameTarget()) return;
  jumpButton.dataset.mode = DETECT_MODE;
  jumpButton.textContent = "连接内网";
  jumpButton.title = "在当前窗口连接内网地址；若无法访问，可使用返回键回到当前页面";
  jumpButton.disabled = false;
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

let manualNavigationStarted = false;

const shouldProbe = () => {
  return intranetConfig.intranetEnabled === "1" &&
    !isLocalAccess() &&
    !sameTarget() &&
    intranetOrigin() !== null;
};

const automaticProbeAttempted = () => {
  try {
    // A cookie from another tab must not suppress this window's first attempt.
    return window.sessionStorage.getItem(AUTO_PROBE_SESSION_KEY) === "1";
  } catch {}
  try {
    return document.cookie.split(";").some((cookie) => cookie.trim() === `${AUTO_PROBE_SESSION_KEY}=1`);
  } catch { return false; }
};

const rememberAutomaticProbe = () => {
  // The session cookie also lets the server skip the preflight page on return.
  // sessionStorage covers browsers that reject cookies, without persisting failures forever.
  try { window.sessionStorage.setItem(AUTO_PROBE_SESSION_KEY, "1"); } catch {}
  try { document.cookie = `${AUTO_PROBE_SESSION_KEY}=1; Path=/; SameSite=Lax`; } catch {}
};

const claimAutomaticProbe = () => {
  const isHome = intranetConfig.intranetPreflight === "1" || document.body.classList.contains("home-page");
  if (!isHome || !shouldProbe() || automaticProbeAttempted()) return false;
  // Record the attempt before making requests, including failures and timeouts.
  rememberAutomaticProbe();
  return true;
};

const initializeJumpButton = async () => {
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
  showConnectButton();
  if (!claimAutomaticProbe()) {
    markProbeState("idle");
    return;
  }
  markProbeState("checking");
  const reachable = await browserCanReachIntranet();
  // Do not replace a user-initiated navigation with a late automatic result.
  if (manualNavigationStarted) return;
  if (reachable && shouldProbe()) {
    markProbeState("reachable");
    showJumpButton();
  } else {
    markProbeState("unreachable");
    showConnectButton();
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
  if (!claimAutomaticProbe()) {
    finish(false);
    return;
  }
  browserCanReachIntranet().then(finish, () => finish(false));
};

if (intranetConfig.intranetPreflight === "1") {
  runPreflight();
} else {
  if (intranetConfig.intranetSkipProbe === "1") rememberAutomaticProbe();
  jumpButton?.addEventListener("click", () => {
    if (!shouldProbe()) return;
    manualNavigationStarted = true;
    rememberAutomaticProbe();
    // Top-level navigation works without a popup/opener handshake and preserves Back.
    redirectToIntranet();
  });
  initializeJumpButton();
}
