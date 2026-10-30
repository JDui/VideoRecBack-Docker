(() => {
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  const active = new WeakMap();
  const easing = getComputedStyle(document.documentElement).getPropertyValue("--ease-layout").trim();
  const transition = async (shell, change, closing = false) => {
    (active.get(shell) || []).forEach(animation => animation.cancel());
    const pane = shell.querySelector('.preview-pane');
    if (closing && !motion.matches) {
      const animation = pane.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateX(18px)' }], { duration: 150, easing, fill: 'forwards' });
      active.set(shell, [animation]);
      try { await animation.finished; } catch { return false; }
      animation.cancel();
    }
    change();
    window.dispatchEvent(new CustomEvent('videorecback:timeline-layout-now'));
    pane.getBoundingClientRect();
    if (!motion.matches && shell.classList.contains('player-open')) {
      const animation = pane.animate([{ opacity: 0, transform: 'translateX(18px)' }, { opacity: 1, transform: 'none' }], { duration: 240, easing });
      active.set(shell, [animation]);
    }
    const content = shell.querySelector('.library-pane, .home-content');
    if (content && !motion.matches) {
      const animation = content.animate([{ opacity: .35, transform: 'translateX(-4px)' }, { opacity: 1, transform: 'none' }], { duration: 220, easing });
      active.set(shell, [...(active.get(shell) || []), animation]);
    }
    return true;
  };
  motion.addEventListener('change', () => {
    if (!motion.matches) return;
    for (const shell of document.querySelectorAll('.app-shell, .home-player-shell')) {
      (active.get(shell) || []).forEach(animation => { if (animation.playState === 'running') animation.finish(); });
    }
  });
  // A landscape phone still needs the full player: aspect ratio alone is insufficient.
  window.VideoRecBackPlayer = {
    transition,
    isWide: () => innerWidth >= 1000 && innerHeight >= 600 && innerWidth / Math.max(innerHeight, 1) > 4 / 3,
    mountHeader: (frame, playerDocument) => {
      const pane = frame?.closest('[data-inline-player]');
      if (!pane || frame.contentDocument !== playerDocument) return false;
      const menus = playerDocument.querySelectorAll('[data-quality-menu], [data-download-menu]');
      const metadata = playerDocument.querySelector('[data-player-metadata]');
      pane.querySelector('[data-inline-media-actions]').replaceChildren(...menus);
      const info = pane.querySelector('[data-inline-player-metadata]');
      info.replaceChildren(...(metadata ? [metadata] : []));
      info.title = metadata ? [...metadata.children].map(item => item.title || item.textContent.trim()).join(' · ') : '';
      pane.querySelector('[data-inline-player-notices]').replaceChildren(
        ...playerDocument.querySelectorAll('[data-download-status], [data-hdr-playback-notice], [data-download-frame]'));
      return true;
    },
  };
  for (const shell of document.querySelectorAll('.app-shell, .home-player-shell')) {
    const frame = shell.querySelector('[data-player-frame]');
    const pane = shell.querySelector('[data-inline-player]');
    // A new target must not retain the previous video's menus or download URLs.
    if (frame) new MutationObserver(() => {
      pane.querySelector('[data-inline-media-actions]').replaceChildren();
      pane.querySelector('[data-inline-player-metadata]').replaceChildren();
      pane.querySelector('[data-inline-player-notices]').replaceChildren();
    }).observe(frame, { attributes: true, attributeFilter: ['src'] });
    const menus = () => [...pane.querySelectorAll('.quality-menu')];
    document.addEventListener('click', event => {
      for (const menu of menus()) if (!menu.contains(event.target)) menu.open = false;
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      const menu = menus().find(item => item.open);
      if (menu) { menu.open = false; menu.querySelector('summary')?.focus(); }
    });
    frame?.addEventListener('load', () => {
      if (frame.getAttribute('src') !== 'about:blank') shell.classList.remove('player-loading');
    });
    const resizer = shell.querySelector('[data-resizer]');
    if (!resizer) continue;
    let resizing = false;
    const update = fraction => {
      const available = Math.max(1, shell.clientWidth - 8);
      const minimum = Math.max(.25, 420 / available);
      const maximum = Math.max(minimum, Math.min(.75, 1 - 300 / available));
      const ratio = Math.max(minimum, Math.min(maximum, fraction));
      shell.style.setProperty('--player-width', `calc((100% - 8px) * ${ratio})`);
      resizer.setAttribute('aria-valuemin', String(Math.ceil(minimum * 100)));
      resizer.setAttribute('aria-valuemax', String(Math.floor(maximum * 100)));
      resizer.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
      window.dispatchEvent(new CustomEvent('videorecback:timeline-layout-now'));
    };
    resizer.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      resizing = true;
      shell.classList.add('is-resizing');
      resizer.setPointerCapture(event.pointerId);
    });
    resizer.addEventListener('pointermove', event => {
      if (!resizing) return;
      const rect = shell.getBoundingClientRect();
      update((rect.right - event.clientX) / Math.max(1, rect.width - 8));
    });
    const finish = () => { resizing = false; shell.classList.remove('is-resizing'); };
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => resizer.addEventListener(name, finish));
    resizer.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
      event.preventDefault();
      const fraction = shell.querySelector('.preview-pane').getBoundingClientRect().width / Math.max(1, shell.clientWidth - 8);
      update(event.key === 'Home' ? 2 / 3 : fraction + (event.key === 'ArrowLeft' ? .025 : -.025));
    });
    resizer.addEventListener('dblclick', () => update(2 / 3));
    new ResizeObserver(() => {
      if (!shell.classList.contains('player-open')) return;
      const available = Math.max(1, shell.clientWidth - 8);
      const ratio = shell.querySelector('.preview-pane').getBoundingClientRect().width / available;
      if (ratio * available < 420 || (1 - ratio) * available < 300) update(ratio);
    }).observe(shell);
  }
})();
