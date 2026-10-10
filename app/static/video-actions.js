(() => {
  const icons = {
    timeline: '<path d="M5 4v16M9 7h10M9 12h7M9 17h10"/><circle cx="5" cy="7" r="1"/><circle cx="5" cy="17" r="1"/>',
    settings: '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
    exclude: '<circle cx="12" cy="12" r="8"/><path d="m6.5 6.5 11 11"/>',
    restore: '<path d="M4 9a8 8 0 1 1 1 9M4 4v5h5"/>',
  };
  const confirmed = new Map();
  const pending = new Set();
  const status = document.createElement('p');
  status.className = 'video-action-status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.hidden = true;
  document.body.append(status);
  let statusTimer;
  const announce = message => {
    clearTimeout(statusTimer);
    status.textContent = message;
    status.hidden = false;
    statusTimer = setTimeout(() => { status.hidden = true; }, 5000);
  };
  const participationFor = card => confirmed.get(card.dataset.videoId || card.dataset.homeCard) || {
    exclude_random: card.dataset.excludeRandom === '1',
    exclude_memories: card.dataset.excludeMemories === '1',
  };
  const setParticipation = async (card, excludeRandom, excludeMemories) => {
    const id = card.dataset.videoId || card.dataset.homeCard;
    if (pending.has(id)) return null;
    pending.add(id);
    try {
      const body = new URLSearchParams({ exclude_random: excludeRandom ? '1' : '0' });
      if (excludeMemories !== undefined) body.set('exclude_memories', excludeMemories ? '1' : '0');
      const response = await fetch(`/video/${encodeURIComponent(id)}/participation`, { method: 'POST', body });
      if (!response.ok) throw new Error('Participation update failed');
      const result = await response.json();
      if (!result.ok) throw new Error('Participation update failed');
      confirmed.set(id, result);
      for (const item of document.querySelectorAll('[data-video-id], [data-home-card]')) {
        if ((item.dataset.videoId || item.dataset.homeCard) !== id) continue;
        item.dataset.excludeRandom = result.exclude_random ? '1' : '0';
        item.dataset.excludeMemories = result.exclude_memories ? '1' : '0';
      }
      try { sessionStorage.removeItem('videorecback-home-random'); } catch {}
      announce(excludeMemories === undefined ? '已从随机视频中排除' :
        excludeRandom ? '已排除随机视频与每日回忆' : '已恢复参与随机视频与每日回忆');
      return result;
    } catch {
      announce('设置未能保存，请重试。');
      return null;
    } finally {
      pending.delete(id);
    }
  };
  const menus = new Set();
  const createMenu = attribute => {
    const menu = document.querySelector(`[${attribute}]`) || document.createElement('div');
    menu.className = 'video-context-menu';
    menu.setAttribute(attribute, '');
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', '视频操作');
    menu.hidden = true;
    document.body.append(menu);
    let origin;
    const hide = (restoreFocus = false) => {
      if (menu.hidden) return;
      menu.hidden = true;
      if (restoreFocus && origin?.isConnected) origin.focus({ preventScroll: true });
      origin = null;
    };
    const open = (target, point, items) => {
      for (const other of menus) other.hide();
      origin = target;
      menu.replaceChildren();
      for (const item of items) {
        const button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('role', 'menuitem');
        if (item.divider) button.classList.add('has-divider');
        button.innerHTML = `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[item.icon] || ''}</svg>`;
        const label = document.createElement('span');
        label.textContent = item.label;
        button.append(label);
        button.addEventListener('click', () => { hide(true); item.onSelect(); });
        menu.append(button);
      }
      const rect = target.getBoundingClientRect();
      const x = point?.x ?? rect.left + Math.min(rect.width / 2, 100);
      const y = point?.y ?? rect.bottom;
      menu.hidden = false;
      const bounds = menu.getBoundingClientRect();
      menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - bounds.width - 8))}px`;
      menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - bounds.height - 8))}px`;
      menu.querySelector('button')?.focus({ preventScroll: true });
    };
    menu.addEventListener('keydown', event => {
      const buttons = [...menu.querySelectorAll('button')];
      const index = buttons.indexOf(document.activeElement);
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 :
          (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
        buttons[next]?.focus({ preventScroll: true });
      } else if (event.key === 'Escape') {
        event.preventDefault();
        hide(true);
      } else if (event.key === 'Tab') {
        hide(true);
      }
    });
    document.addEventListener('pointerdown', event => { if (!menu.contains(event.target)) hide(); });
    document.addEventListener('scroll', () => hide(), true);
    window.addEventListener('resize', () => hide());
    window.addEventListener('pagehide', () => hide());
    const api = { open, hide };
    menus.add(api);
    return api;
  };
  window.VideoRecBackActions = { createMenu, participationFor, setParticipation, announce };
})();
