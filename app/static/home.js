const randomGrid = document.querySelector('[data-home-random]');
const shuffleButton = document.querySelector('[data-home-shuffle]');
const feedback = document.querySelector('[data-home-feedback]');
const scanForm = document.querySelector('[data-home-scan-form]');
const scanButton = document.querySelector('[data-home-scan-button]');
const scanStatus = document.querySelector('[data-home-scan-status]');
let wasScanning = document.querySelector('[data-home-status]')?.dataset.scanning === '1';
let scanSubmitting = false;

shuffleButton?.addEventListener('click', async () => {
  const ids = [...new Set([...randomGrid.querySelectorAll('[data-home-video]')].map(item => item.dataset.homeVideo))];
  shuffleButton.disabled = true;
  shuffleButton.textContent = '正在拾回';
  randomGrid.setAttribute('aria-busy', 'true');
  feedback.textContent = '';
  try {
    const response = await fetch(`/home/random?exclude=${encodeURIComponent(ids.join(','))}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Random videos unavailable');
    const result = await response.json();
    randomGrid.innerHTML = result.html;
    feedback.textContent = '又拾回了几段时光。';
  } catch {
    feedback.textContent = '暂时未能拾回，稍后再试。';
  } finally {
    shuffleButton.disabled = false;
    shuffleButton.textContent = '再拾三段';
    randomGrid.removeAttribute('aria-busy');
  }
});

document.addEventListener('click', event => {
  if (!event.target.closest('[data-home-video]')) return;
  try {
    const state = JSON.stringify({ url: '/', hash: '', scrollTop: window.scrollY, view: 'home', savedAt: Date.now() });
    sessionStorage.setItem('videorecback-return-state', state);
    localStorage.setItem('videorecback-return-state', state);
    sessionStorage.setItem('videorecback-home-scroll', String(window.scrollY));
  } catch {}
});

try {
  if (sessionStorage.getItem('videorecback-returning-from-player')) {
    window.scrollTo(0, Number(sessionStorage.getItem('videorecback-home-scroll') || 0));
    sessionStorage.removeItem('videorecback-returning-from-player');
  }
} catch {}

const pollScan = async () => {
  if (document.hidden || scanSubmitting) return;
  try {
    const response = await fetch('/scan/status', { cache: 'no-store' });
    if (!response.ok) return;
    const state = await response.json();
    if (wasScanning && !state.scanning) {
      window.location.reload();
      return;
    }
    wasScanning = state.scanning;
    scanButton.disabled = state.scanning;
    scanButton.textContent = state.scanning ? '正在更新' : '更新影像库';
    scanStatus.textContent = state.indexing ? '正在扫描' : state.processing_media ? `正在准备 ${state.pending_media} 个封面` : '扫描空闲';
  } catch {}
};

scanForm?.addEventListener('submit', async event => {
  event.preventDefault();
  if (scanSubmitting || wasScanning) return;
  scanSubmitting = true;
  scanButton.disabled = true;
  scanButton.textContent = '正在更新';
  try {
    const response = await fetch(scanForm.action, { method: 'POST' });
    if (!response.ok) throw new Error('Scan unavailable');
    wasScanning = true;
    scanStatus.textContent = '正在更新';
  } catch {
    feedback.textContent = '更新未能开始，请稍后重试。';
    scanStatus.textContent = '更新未能开始';
    scanButton.disabled = false;
    scanButton.textContent = '更新影像库';
  } finally {
    scanSubmitting = false;
  }
  await pollScan();
});

pollScan();
window.setInterval(pollScan, 10000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) pollScan(); });
