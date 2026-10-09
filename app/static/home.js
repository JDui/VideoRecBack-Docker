const randomGrid = document.querySelector('[data-home-random]');
const shuffleButton = document.querySelector('[data-home-shuffle]');
const feedback = document.querySelector('[data-home-feedback]');
const scanForm = document.querySelector('[data-home-scan-form]');
const scanButton = document.querySelector('[data-home-scan-button]');
const scanStatus = document.querySelector('[data-home-scan-status]');
let wasScanning = document.querySelector('[data-home-status]')?.dataset.scanning === '1';
let scanSubmitting = false;
const homeRandomStorageKey = 'videorecback-home-random';
const homePreserveRandomKey = 'videorecback-home-preserve-random';
const homeRandomIds = () => [...new Set([...randomGrid.querySelectorAll('[data-home-video]')].map(item => item.dataset.homeVideo))];
const saveHomeRandom = () => {
  if (!randomGrid) return;
  try {
    sessionStorage.setItem(homeRandomStorageKey, JSON.stringify({ html: randomGrid.innerHTML, ids: homeRandomIds() }));
  } catch {}
};
const restoreHomeRandom = () => {
  if (!randomGrid) return false;
  try {
    const cached = JSON.parse(sessionStorage.getItem(homeRandomStorageKey) || 'null');
    if (typeof cached?.html !== 'string' || !Array.isArray(cached.ids)
        || cached.ids.length > 3 || !cached.ids.every(id => typeof id === 'string' && /^\d+$/.test(id))) return false;
    randomGrid.innerHTML = cached.html;
    if (shuffleButton) shuffleButton.disabled = cached.ids.length === 0;
    return true;
  } catch {
    return false;
  }
};

if (randomGrid) {
  let restoreRandom = true;
  try {
    const preserveRandom = sessionStorage.getItem(homePreserveRandomKey) === '1';
    sessionStorage.removeItem(homePreserveRandomKey);
    const reloaded = performance.getEntriesByType('navigation')[0]?.type === 'reload';
    restoreRandom = !reloaded || preserveRandom;
  } catch {}
  if (restoreRandom) restoreHomeRandom();
  saveHomeRandom();
}

let homeContextOrigin = null;
let homeContextCard = null;
const homeContextMenu = document.createElement('div');
homeContextMenu.className = 'favorite-context-menu';
homeContextMenu.dataset.homeContextMenu = '';
homeContextMenu.setAttribute('role', 'menu');
homeContextMenu.setAttribute('aria-label', '视频操作');
homeContextMenu.hidden = true;
const homeTimelineAction = document.createElement('button');
homeTimelineAction.type = 'button';
homeTimelineAction.setAttribute('role', 'menuitem');
homeTimelineAction.textContent = '跳转到时间线位置';
homeContextMenu.append(homeTimelineAction);
if (randomGrid) document.body.append(homeContextMenu);

const hideHomeContextMenu = (restoreFocus = true) => {
  if (homeContextMenu.hidden) return;
  homeContextMenu.hidden = true;
  if (restoreFocus && homeContextOrigin?.isConnected) homeContextOrigin.focus({ preventScroll: true });
  homeContextOrigin = null;
  homeContextCard = null;
};
const homeRandomCard = target => target instanceof Element ? target.closest('.home-video[data-home-timeline-url]') : null;
const openHomeContextMenu = (card, target, point) => {
  homeContextCard = card;
  homeContextOrigin = target instanceof Element ? target.closest('a, button') : null;
  if (!homeContextOrigin || !card.contains(homeContextOrigin)) homeContextOrigin = card.querySelector('[data-home-video]');
  const rect = homeContextOrigin.getBoundingClientRect();
  const x = point?.x ?? rect.left + Math.min(rect.width / 2, 100);
  const y = point?.y ?? rect.bottom;
  homeContextMenu.hidden = false;
  const menuRect = homeContextMenu.getBoundingClientRect();
  homeContextMenu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menuRect.width - 8))}px`;
  homeContextMenu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menuRect.height - 8))}px`;
  homeTimelineAction.focus({ preventScroll: true });
};

randomGrid?.addEventListener('contextmenu', event => {
  const card = homeRandomCard(event.target);
  if (!card || !randomGrid.contains(card)) return;
  event.preventDefault();
  openHomeContextMenu(card, event.target, event.clientX || event.clientY ? { x: event.clientX, y: event.clientY } : null);
});
randomGrid?.addEventListener('keydown', event => {
  if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) return;
  const card = homeRandomCard(event.target);
  if (!card || !randomGrid.contains(card)) return;
  event.preventDefault();
  openHomeContextMenu(card, event.target);
});
homeTimelineAction.addEventListener('click', event => {
  event.preventDefault();
  const url = homeContextCard?.dataset.homeTimelineUrl;
  hideHomeContextMenu(false);
  if (url) window.location.assign(url);
});
for (const name of ['pointerdown', 'click']) {
  document.addEventListener(name, event => {
    if (!(event.target instanceof Node) || homeContextMenu.contains(event.target)) return;
    hideHomeContextMenu();
  });
}
document.addEventListener('keydown', event => {
  if (homeContextMenu.hidden) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    hideHomeContextMenu();
  } else if (event.key === 'Tab') {
    hideHomeContextMenu();
  } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    homeTimelineAction.focus({ preventScroll: true });
  }
});
document.addEventListener('scroll', () => hideHomeContextMenu(), true);
window.addEventListener('resize', () => hideHomeContextMenu());
window.addEventListener('pageshow', event => {
  if (!event.persisted) return;
  hideHomeContextMenu(false);
  restoreHomeRandom();
});

shuffleButton?.addEventListener('click', async () => {
  hideHomeContextMenu();
  const ids = homeRandomIds();
  shuffleButton.disabled = true;
  shuffleButton.textContent = '加载中';
  randomGrid.setAttribute('aria-busy', 'true');
  feedback.textContent = '';
  try {
    const response = await fetch(`/home/random?exclude=${encodeURIComponent(ids.join(','))}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Random videos unavailable');
    const result = await response.json();
    if (typeof result.html !== 'string') throw new Error('Invalid random videos');
    randomGrid.innerHTML = result.html;
    saveHomeRandom();
    feedback.textContent = '';
  } catch {
    feedback.textContent = '加载失败，请重试。';
  } finally {
    shuffleButton.disabled = false;
    shuffleButton.textContent = '换一组';
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
      saveHomeRandom();
      try { sessionStorage.setItem(homePreserveRandomKey, '1'); } catch {}
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
    const response = await fetch(scanForm.action, { method: 'POST', headers: { Accept: 'application/json' } });
    if (!response.ok || !(await response.json()).scanning) throw new Error('Scan unavailable');
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
