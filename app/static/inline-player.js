(() => {
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  const active = new WeakMap();
  const transition = async (shell, change, closing = false) => {
    (active.get(shell) || []).forEach(animation => animation.cancel());
    const pane = shell.querySelector('.preview-pane');
    if (closing && !motion.matches) {
      const animation = pane.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateX(18px)' }], { duration: 150, easing: 'ease-out', fill: 'forwards' });
      active.set(shell, [animation]);
      try { await animation.finished; } catch { return false; }
      animation.cancel();
    }
    change();
    window.dispatchEvent(new CustomEvent('videorecback:timeline-layout-now'));
    pane.getBoundingClientRect();
    if (!motion.matches && shell.classList.contains('player-open')) {
      const animation = pane.animate([{ opacity: 0, transform: 'translateX(18px)' }, { opacity: 1, transform: 'none' }], { duration: 240, easing: 'cubic-bezier(.2,.8,.2,1)' });
      active.set(shell, [animation]);
    }
    const content = shell.querySelector('.library-pane, .home-content');
    if (content && !motion.matches) {
      const animation = content.animate([{ opacity: .35, transform: 'translateX(-4px)' }, { opacity: 1, transform: 'none' }], { duration: 220, easing: 'ease-out' });
      active.set(shell, [...(active.get(shell) || []), animation]);
    }
    return true;
  };
  window.VideoRecBackPlayer = { transition, isWide: () => innerWidth / Math.max(innerHeight, 1) > 4 / 3 };
  for (const shell of document.querySelectorAll('.app-shell, .home-player-shell')) {
    const frame = shell.querySelector('[data-player-frame]');
    frame?.addEventListener('load', () => {
      if (frame.getAttribute('src') !== 'about:blank') shell.classList.remove('player-loading');
    });
    const resizer = shell.querySelector('[data-resizer]');
    if (!resizer) continue;
    let resizing = false;
    const update = fraction => {
      const ratio = Math.max(.25, Math.min(.75, fraction));
      shell.style.setProperty('--player-width', `calc((100% - 8px) * ${ratio})`);
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
  }
})();
