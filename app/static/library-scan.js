(() => {
  const form = document.querySelector("[data-scan-form]");
  const button = form?.querySelector("[data-scan-button]");
  const label = form?.querySelector("[data-scan-label]");
  if (!form || !button || !label) return;
  let running = form.dataset.scanRunning === "1";
  let submitting = false;
  let polling = false;
  let timer;

  const render = message => {
    button.disabled = running || submitting;
    button.classList.toggle("is-scanning", running || submitting);
    button.setAttribute("aria-busy", String(running || submitting));
    label.textContent = message || (running ? "更新中" : "扫描");
  };
  const schedule = () => {
    clearTimeout(timer);
    if (running && !document.hidden) timer = setTimeout(poll, 1000);
  };
  const poll = async () => {
    if (polling || submitting || document.hidden || !running) return;
    polling = true;
    try {
      const response = await fetch("/scan/status", { cache: "no-store" });
      if (!response.ok) throw new Error("Scan status unavailable");
      const status = await response.json();
      running = Boolean(status.scanning);
      render(running ? (status.indexing ? "建立索引中" : `处理媒体 ${Number(status.pending_media || 0)}`) : "扫描完成");
      if (!running) window.dispatchEvent(new CustomEvent("videorecback:scan-complete"));
    } catch {
      render("更新中");
    } finally {
      polling = false;
      schedule();
    }
  };
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (submitting || running) return;
    submitting = true;
    render("更新中");
    button.removeAttribute("title");
    try {
      const response = await fetch(form.action, { method: "POST", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Scan unavailable");
      const status = await response.json();
      if (!status.scanning) throw new Error("Maintenance busy");
      running = true;
    } catch {
      button.title = "扫描未能开始，请稍后重试";
      label.textContent = "启动失败，重试";
    } finally {
      submitting = false;
      render(running ? "更新中" : "启动失败，重试");
      if (running) poll();
    }
  });
  document.addEventListener("visibilitychange", () => {
    clearTimeout(timer);
    if (!document.hidden) poll();
  });
  render();
  if (running) poll();
})();
