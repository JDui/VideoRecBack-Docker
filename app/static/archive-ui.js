(() => {
  const playerDisclosures = [...document.querySelectorAll('.player-disclosure')];
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !(event.target instanceof Element) || event.target.closest('.quality-menu')) return;
    const current = event.target.closest('.player-disclosure[open]') || playerDisclosures.find(item => item.open);
    if (!current) return;
    event.preventDefault();
    current.open = false;
    const trigger = current.matches('[data-playback-settings]') ? document.querySelector('[data-playback-toggle]') : current.querySelector('summary');
    trigger?.focus();
  });
  const filterForm = document.querySelector('[data-library-filters]');
  if (filterForm) {
    const from = filterForm.elements.date_from;
    const to = filterForm.elements.date_to;
    const error = filterForm.querySelector('[data-date-error]');
    const validate = () => {
      const invalid = Boolean(from.value && to.value && from.value > to.value);
      to.setCustomValidity(invalid ? '开始日期不能晚于结束日期。' : '');
      error.hidden = !invalid;
      return !invalid;
    };
    filterForm.addEventListener('input', validate);
    filterForm.addEventListener('submit', event => { if (!validate()) { event.preventDefault(); to.reportValidity(); } });
    validate();
  }
  const search = document.querySelector('[data-library-search]');
  let composing = false;
  search?.addEventListener('compositionstart', () => { composing = true; });
  search?.addEventListener('compositionend', () => { composing = false; });
  search?.addEventListener('submit', event => { if (composing) event.preventDefault(); });
  search?.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.isComposing || event.keyCode === 229)) event.preventDefault();
  });
  const info = document.querySelector('[data-card-info]');
  if (info) {
    try { info.checked = localStorage.getItem('videorecback-card-info') !== '0'; } catch {}
    const apply = () => document.querySelector('[data-timeline-gallery]')?.classList.toggle('show-card-info', info.checked);
    apply();
    info.addEventListener('change', () => { apply(); try { localStorage.setItem('videorecback-card-info', info.checked ? '1' : '0'); } catch {} });
  }
  const random = document.querySelector('[data-home-random]');
  const controls = document.querySelector('[data-random-controls]');
  if (random && controls) {
    const mobile = matchMedia('(max-width: 700px)');
    let index = 0;
    const previous = controls.querySelector('[data-random-previous]');
    const next = controls.querySelector('[data-random-next]');
    const position = controls.querySelector('[data-random-position]');
    const apply = () => {
      const cards = [...random.querySelectorAll(':scope > .home-video')];
      index = Math.max(0, Math.min(index, cards.length - 1));
      controls.hidden = !mobile.matches || cards.length < 2;
      random.classList.toggle('is-carousel', mobile.matches);
      cards.forEach((card, i) => { card.hidden = mobile.matches && i !== index; });
      previous.disabled = index <= 0;
      next.disabled = index >= cards.length - 1;
      position.textContent = `${index + 1} / ${cards.length}`;
    };
    const step = direction => {
      index += direction;
      apply();
      const card = random.querySelector(':scope > .home-video:not([hidden])');
      window.VideoRecBackMotion?.animate(card, [{ opacity: .3, transform: `translateX(${direction * 12}px)` }, { opacity: 1, transform: 'none' }], 260);
    };
    previous.addEventListener('click', () => step(-1));
    next.addEventListener('click', () => step(1));
    mobile.addEventListener('change', apply);
    new MutationObserver(() => { index = 0; apply(); }).observe(random, { childList: true });
    apply();
  }
})();
