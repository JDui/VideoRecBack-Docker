(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const running = new Set();
  const active = new WeakMap();
  let departure;
  const tokens = getComputedStyle(document.documentElement);
  const easing = name => tokens.getPropertyValue(`--ease-${name}`).trim();
  const animate = (element, frames, duration, curve = 'enter', options = {}) => {
    if (!element || reduced.matches) return null;
    active.get(element)?.cancel();
    const animation = element.animate(frames, { duration, easing: easing(curve), ...options });
    active.set(element, animation);
    running.add(animation);
    const release = () => { running.delete(animation); if (active.get(element) === animation) active.delete(element); };
    animation.finished.then(release, release);
    return animation;
  };
  const cancel = () => {
    for (const animation of running) animation.cancel();
    departure?.cancel();
    departure = null;
  };
  const reveal = () => {
    animate(document.querySelector('.page'), [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], 360);
    animate(document.querySelector('.timeline-rail-host'), [{ opacity: 0 }, { opacity: 1 }], 280);
    [...document.querySelectorAll('.home-section, .preference-section, .detail-panel')].slice(0, 6).forEach((section, index) => {
      animate(section, [{ opacity: .4, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], 320, 'enter', { delay: 40 + index * 25, fill: 'backwards' });
    });
  };
  const leave = async () => {
    const page = document.querySelector('.page');
    if (!page || reduced.matches) return;
    const current = getComputedStyle(page);
    const rail = document.querySelector('.timeline-rail-host');
    if (rail) animate(rail, [{ opacity: getComputedStyle(rail).opacity }, { opacity: 0 }], 130, 'exit', { fill: 'forwards' });
    const animation = animate(page, [{ opacity: current.opacity, transform: current.transform }, { opacity: .82, transform: 'translateY(-2px)' }], 130, 'exit', { fill: 'forwards' });
    departure = animation;
    try { await animation?.finished; } catch {}
  };
  document.querySelectorAll('.preferences-nav a[href^="#"]').forEach(link => {
    link.addEventListener('click', () => {
      const section = document.getElementById(link.hash.slice(1));
      animate(section, [{ opacity: .5, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], 280);
    });
  });
  window.VideoRecBackMotion = { animate, leave };
  window.addEventListener('videorecback:ready', reveal, { once: true });
  window.addEventListener('pageshow', event => { if (event.persisted) {
    cancel();
    document.querySelector('.player-shell.is-closing')?.classList.remove('is-closing');
    reveal();
  } });
  reduced.addEventListener('change', () => { if (reduced.matches) cancel(); });
})();
