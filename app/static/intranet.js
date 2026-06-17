const intranetConfig = document.body?.dataset || {};

if (intranetConfig.intranetEnabled === "1") {
  const probeHost = (intranetConfig.intranetProbeHost || "192.168.31.1").trim();
  const redirectHost = (intranetConfig.intranetRedirectHost || "").trim();
  const redirectPort = (intranetConfig.intranetRedirectPort || "").trim();

  const sameTarget = () => {
    if (!redirectHost) return true;
    const currentPort = window.location.port || (window.location.protocol === "https:" ? "443" : "80");
    const targetPort = redirectPort || currentPort;
    return window.location.hostname === redirectHost && currentPort === targetPort;
  };

  const redirectToIntranet = () => {
    if (!redirectHost || sameTarget()) return;
    const target = new URL(window.location.href);
    target.hostname = redirectHost;
    if (redirectPort) target.port = redirectPort;
    window.location.replace(target.toString());
  };

  fetch(`/settings/intranet-keepalive/probe?host=${encodeURIComponent(probeHost)}&t=${Date.now()}`, {
    cache: "no-store",
  })
    .then((response) => response.ok ? response.json() : { ok: false })
    .then((payload) => {
      if (payload?.ok) redirectToIntranet();
    })
    .catch(() => {});
}
