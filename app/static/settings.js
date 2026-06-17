const panel = document.querySelector("[data-connectivity-test]");

if (panel) {
  const startButton = panel.querySelector("[data-connectivity-start]");
  const statusValue = panel.querySelector("[data-connectivity-status]");
  const latencyValue = panel.querySelector("[data-connectivity-latency]");
  const speedValue = panel.querySelector("[data-connectivity-speed]");
  const downloadSize = 16 * 1024 * 1024;

  const setStatus = (message) => {
    if (statusValue) statusValue.textContent = message;
  };

  const measureLatency = async () => {
    const samples = [];
    for (let index = 0; index < 4; index += 1) {
      const startedAt = performance.now();
      const response = await fetch(`/settings/connectivity-test/ping?t=${Date.now()}-${index}`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Ping failed");
      await response.json();
      samples.push(performance.now() - startedAt);
    }
    samples.sort((left, right) => left - right);
    return samples[Math.floor(samples.length / 2)];
  };

  const measureDownload = async () => {
    const startedAt = performance.now();
    const response = await fetch(`/settings/connectivity-test/download?size=${downloadSize}&t=${Date.now()}`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error("Download failed");
    if (!response.body) {
      const blob = await response.blob();
      return { bytes: blob.size, seconds: (performance.now() - startedAt) / 1000 };
    }
    const reader = response.body.getReader();
    let bytes = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
    }
    return { bytes, seconds: (performance.now() - startedAt) / 1000 };
  };

  startButton?.addEventListener("click", async () => {
    startButton.disabled = true;
    setStatus("正在测试延迟...");
    try {
      const latencyMs = await measureLatency();
      if (latencyValue) latencyValue.textContent = `延迟 ${Math.round(latencyMs)} ms`;
      setStatus("正在测试下载速度...");
      const { bytes, seconds } = await measureDownload();
      const mbps = seconds > 0 ? (bytes * 8) / seconds / 1_000_000 : 0;
      if (speedValue) speedValue.textContent = `下载 ${mbps.toFixed(1)} Mbps`;
      setStatus(`测试完成，已下载 ${(bytes / 1024 / 1024).toFixed(1)} MB。`);
    } catch {
      setStatus("测试失败，请确认当前服务可访问。");
    } finally {
      startButton.disabled = false;
    }
  });
}
