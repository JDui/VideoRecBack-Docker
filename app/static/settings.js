const panel = document.querySelector("[data-connectivity-test]");

const hdrStatus = document.querySelector('[data-hdr-status]');
const hdrButton = document.querySelector('[data-hdr-recheck]');
const pollHdrStatus = async () => {
  if (!hdrStatus?.isConnected || hdrStatus.dataset.state !== 'running') return;
  try {
    const response = await fetch('/settings/hdr-status', { cache: 'no-store' });
    if (!response.ok) throw new Error('HDR status unavailable');
    const result = await response.json();
    if (!hdrStatus.isConnected) return;
    hdrStatus.dataset.state = result.status;
    if (result.status === 'complete') {
      hdrStatus.textContent = `HDR 标准校验完成：成功 ${result.checked} 个，失败 ${result.errors} 个。${result.errors ? '失败项保留原标签，可稍后重试。' : ''}`;
    } else if (result.status === 'failed' || result.status === 'idle') {
      hdrStatus.textContent = 'HDR 标准校验中断，请稍后重试。';
    }
    hdrButton.disabled = result.status === 'running';
  } catch {
    hdrStatus.textContent = '暂时无法获取校验状态，正在重试。';
  }
  if (hdrStatus.dataset.state === 'running') setTimeout(pollHdrStatus, 2000);
};
if (hdrStatus?.dataset.state === 'running') setTimeout(pollHdrStatus, 1000);

const portraitStatus = document.querySelector('[data-portrait-status]');
const portraitButton = document.querySelector('[data-portrait-refresh]');
const pollPortraitStatus = async () => {
  if (!portraitStatus?.isConnected || portraitStatus.dataset.state !== 'running') return;
  try {
    const response = await fetch('/settings/portrait-thumbnail-status', { cache: 'no-store' });
    if (!response.ok) throw new Error('Portrait thumbnail status unavailable');
    const result = await response.json();
    if (!portraitStatus.isConnected) return;
    portraitStatus.dataset.state = result.status;
    portraitButton.disabled = result.status === 'running';
    if (result.status === 'running' || result.status === 'complete') {
      portraitStatus.textContent = `${result.status === 'complete' ? '竖屏缩略图重载完成' : '正在检查视频方向'}：检查 ${result.checked} 个，重建 ${result.rebuilt} 个，失败 ${result.errors} 个。`;
    } else {
      portraitStatus.textContent = '竖屏缩略图重载中断，请稍后重试。';
    }
  } catch {
    portraitStatus.textContent = '暂时无法获取重载状态，正在重试。';
  }
  if (portraitStatus.dataset.state === 'running') setTimeout(pollPortraitStatus, 2000);
};
if (portraitStatus?.dataset.state === 'running') setTimeout(pollPortraitStatus, 1000);

const preferencesForm = document.querySelector('#preferences-form');
const saveStatus = document.querySelector('[data-settings-save-status]');
const saveButton = document.querySelector('[data-settings-save]');
const sectionLinks = [...document.querySelectorAll('.preferences-nav a[href^="#"]')];
const preferenceSections = [...document.querySelectorAll('.preference-section')];

let sectionPositions = [];
let activeSectionId = null;
let navigationFrame = 0;
let measureSections = true;
const syncSectionNavigation = () => {
  navigationFrame = 0;
  if (measureSections) {
    sectionPositions = preferenceSections.map(section => ({ id: section.id, top: section.getBoundingClientRect().top + window.scrollY }));
    measureSections = false;
  }
  const threshold = window.scrollY + (window.innerWidth <= 700 ? 120 : 100);
  let current = sectionPositions[0];
  for (const section of sectionPositions) {
    if (section.top <= threshold) current = section;
  }
  if (current?.id === activeSectionId) return;
  activeSectionId = current?.id;
  for (const link of sectionLinks) {
    const active = link.hash === `#${activeSectionId}`;
    if (active && !link.hasAttribute('aria-current')) link.setAttribute('aria-current', 'location');
    else if (!active && link.hasAttribute('aria-current')) link.removeAttribute('aria-current');
  }
};
const scheduleSectionNavigation = () => {
  if (!navigationFrame) navigationFrame = requestAnimationFrame(syncSectionNavigation);
};
const invalidateSectionPositions = () => {
  measureSections = true;
  scheduleSectionNavigation();
};
window.addEventListener('scroll', scheduleSectionNavigation, { passive: true });
window.addEventListener('resize', invalidateSectionPositions, { passive: true });
new ResizeObserver(invalidateSectionPositions).observe(document.querySelector('.preferences-content'));
window.addEventListener('pageshow', invalidateSectionPositions);
syncSectionNavigation();

preferencesForm?.addEventListener('input', () => { saveStatus.textContent = '已修改，保存后生效。'; });
preferencesForm?.addEventListener('submit', () => {
  saveButton.disabled = true;
  saveButton.textContent = '正在保存';
  saveStatus.textContent = '正在保存设置。';
});
window.addEventListener('pageshow', () => {
  if (!saveButton) return;
  saveButton.disabled = false;
  saveButton.textContent = '保存设置';
});

if (panel) {
  const startButton = panel.querySelector("[data-connectivity-start]");
  const statusValue = panel.querySelector("[data-connectivity-status]");
  const latencyValue = panel.querySelector("[data-connectivity-latency]");
  const speedValue = panel.querySelector("[data-connectivity-speed]");
  const downloadSize = 2 * 1024 * 1024;

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
