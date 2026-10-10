const randomGrid = document.querySelector('[data-home-random]');
const shuffleButton = document.querySelector('[data-home-shuffle]');
const feedback = document.querySelector('[data-home-feedback]');
const scanForm = document.querySelector('[data-home-scan-form]');
const scanButton = document.querySelector('[data-home-scan-button]');
const scanStatus = document.querySelector('[data-home-scan-status]');
let wasScanning = document.querySelector('[data-home-status]')?.dataset.scanning === '1';
let scanSubmitting = false;
let homeRefreshPending = false;
const homeRandomStorageKey = 'videorecback-home-random';
const homePreserveRandomKey = 'videorecback-home-preserve-random';
const homeRandomIds = () => [...new Set([...randomGrid.querySelectorAll('[data-home-video]')].map(item => item.dataset.homeVideo))];
let homeRandomRequestId = 0;
const saveHomeRandom = () => {
  if (!randomGrid) return;
  try {
    sessionStorage.setItem(homeRandomStorageKey, JSON.stringify({ version: 3, ids: homeRandomIds() }));
  } catch {}
};
const replaceHomeRandom = html => {
  randomGrid.innerHTML = html;
  if (shuffleButton) shuffleButton.disabled = homeRandomIds().length === 0;
  saveHomeRandom();
};
const restoreHomeRandom = async (validateCurrent = false) => {
  if (!randomGrid) return;
  let ids = validateCurrent ? homeRandomIds() : null;
  try {
    const cached = JSON.parse(sessionStorage.getItem(homeRandomStorageKey) || 'null');
    if ([2, 3].includes(cached?.version) && Array.isArray(cached.ids) && cached.ids.length
        && cached.ids.length <= 3 && cached.ids.every(id => typeof id === 'string' && /^\d+$/.test(id))) ids = cached.ids;
  } catch {}
  if (!ids) return;
  const requestId = ++homeRandomRequestId;
  try {
    const response = await fetch(`/home/random?selected=${encodeURIComponent(ids.join(','))}`, { cache: 'no-store', signal: AbortSignal.timeout(5000) });
    if (!response.ok) return;
    const result = await response.json();
    if (requestId === homeRandomRequestId && typeof result.html === 'string') replaceHomeRandom(result.html);
  } catch {}
};

if (randomGrid) {
  let restoreRandom = true;
  try {
    const preserveRandom = sessionStorage.getItem(homePreserveRandomKey) === '1';
    sessionStorage.removeItem(homePreserveRandomKey);
    const reloaded = performance.getEntriesByType('navigation')[0]?.type === 'reload';
    restoreRandom = !reloaded || preserveRandom;
  } catch {}
  if (restoreRandom) {
    window.VideoRecBackLoading?.hold();
    restoreHomeRandom().finally(() => { saveHomeRandom(); window.VideoRecBackLoading?.release(); });
  }
  else saveHomeRandom();
}

const homeContextMenu = window.VideoRecBackActions.createMenu('data-home-context-menu');
const hideHomeContextMenu = () => homeContextMenu.hide();
const homeRandomCard = target => target instanceof Element ? target.closest('.home-video[data-home-timeline-url]') : null;
const openHomeContextMenu = (card, target, point) => {
  const origin = target.closest('a, button') || card.querySelector('[data-home-video]');
  homeContextMenu.open(origin, point, [
    { label: '跳转到时间线位置', icon: 'timeline', onSelect: () => window.VideoRecBackNavigate(card.dataset.homeTimelineUrl) },
    { label: '不再出现在随机中', icon: 'exclude', divider: true, onSelect: async () => {
      const result = await window.VideoRecBackActions.setParticipation(card, true);
      if (!result) return;
      const requestId = ++homeRandomRequestId;
      randomGrid.querySelector(`[data-home-card="${card.dataset.homeCard}"]`)?.remove();
      if (!homeRandomIds().length) randomGrid.innerHTML = '<p class="home-empty">暂无参与随机的视频，可在时间线右键菜单中恢复参与。</p>';
      saveHomeRandom();
      try {
        const response = await fetch(`/home/random?selected=${encodeURIComponent(homeRandomIds().join(','))}`, { cache: 'no-store' });
        if (!response.ok) throw new Error('Random videos unavailable');
        const refreshed = await response.json();
        if (requestId === homeRandomRequestId && typeof refreshed.html === 'string') replaceHomeRandom(refreshed.html);
      } catch {
        if (shuffleButton) shuffleButton.disabled = false;
        feedback.textContent = '已保存排除设置，补充随机视频失败，可点击换一组重试。';
      }
    } },
  ]);
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
let homeLongPressTimer;
let homeLongPressCard;
randomGrid?.addEventListener('touchstart', event => {
  clearTimeout(homeLongPressTimer);
  homeLongPressCard = null;
  const card = homeRandomCard(event.target);
  if (!card) return;
  const touch = event.touches[0];
  homeLongPressTimer = setTimeout(() => {
    homeLongPressCard = card;
    openHomeContextMenu(card, event.target, { x: touch.clientX, y: touch.clientY });
  }, 520);
}, { passive: true });
for (const name of ['touchend', 'touchmove', 'touchcancel']) {
  randomGrid?.addEventListener(name, () => clearTimeout(homeLongPressTimer), { passive: true });
}
randomGrid?.addEventListener('click', event => {
  if (homeRandomCard(event.target) !== homeLongPressCard) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  homeLongPressCard = null;
}, true);
window.addEventListener('pageshow', event => {
  if (!event.persisted) return;
  hideHomeContextMenu();
  restoreHomeRandom(true);
});

shuffleButton?.addEventListener('click', async () => {
  hideHomeContextMenu();
  const ids = homeRandomIds();
  const requestId = ++homeRandomRequestId;
  shuffleButton.disabled = true;
  shuffleButton.textContent = '加载中';
  randomGrid.style.minHeight = randomGrid.getBoundingClientRect().height + 'px';
  randomGrid.setAttribute('aria-busy', 'true');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const outgoing = reduced ? null : randomGrid.animate([{ opacity: 1 }, { opacity: .4 }], { duration: 140, fill: 'forwards' });
  feedback.textContent = '';
  try {
    const response = await fetch(`/home/random?exclude=${encodeURIComponent(ids.join(','))}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Random videos unavailable');
    const result = await response.json();
    if (typeof result.html !== 'string') throw new Error('Invalid random videos');
    const template = document.createElement('template');
    template.innerHTML = result.html;
    const preload = [...template.content.querySelectorAll('img')].map(image => {
      const loaded = new Image(); loaded.src = image.src;
      return loaded.decode().catch(() => {});
    });
    await Promise.race([Promise.all(preload), new Promise(resolve => setTimeout(resolve, 1200))]);
    await outgoing?.finished;
    if (requestId !== homeRandomRequestId) return;
    randomGrid.replaceChildren(template.content);
    outgoing?.cancel();
    if (!reduced) await Promise.all([...randomGrid.children].map((card, index) => card.animate(
      [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }],
      { duration: 220, delay: index * 25, fill: 'backwards', easing: 'ease-out' }
    ).finished));
    saveHomeRandom();
    feedback.textContent = '';
  } catch {
    feedback.textContent = '加载失败，请重试。';
  } finally {
    shuffleButton.disabled = homeRandomIds().length === 0;
    shuffleButton.textContent = '换一组';
    outgoing?.cancel();
    randomGrid.removeAttribute('aria-busy');
    randomGrid.style.removeProperty('min-height');
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
    if (wasScanning && !state.scanning) homeRefreshPending = true;
    if (homeRefreshPending && !document.querySelector('.home-player-shell.player-open')) {
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

window.addEventListener('videorecback:player-closed', pollScan);

window.addEventListener('videorecback:home-favorite', saveHomeRandom);
