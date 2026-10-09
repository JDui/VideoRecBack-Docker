(() => {
  const shell = document.querySelector('.home-player-shell');
  if (!shell) return;
  const frame = shell.querySelector('[data-player-frame]');
  const content = shell.querySelector('.home-content');
  const title = shell.querySelector('[data-inline-player-title]');
  const settings = shell.querySelector('[data-inline-settings]');
  const favorite = shell.querySelector('[data-inline-favorite]');
  const renderFavorite = value => {
    favorite.dataset.favoriteState = value ? '1' : '0';
    favorite.classList.toggle('active', value);
    favorite.setAttribute('aria-pressed', String(value));
    favorite.setAttribute('aria-label', value ? '取消收藏' : '收藏视频');
    favorite.querySelector('[data-favorite-label]').textContent = value ? '已收藏' : '收藏';
  };
  favorite.addEventListener('click', async () => {
    const id = favorite.dataset.videoId;
    const next = favorite.dataset.favoriteState !== '1';
    favorite.disabled = true;
    try {
      const response = await fetch(`/video/${id}/favorite`, { method: 'POST', body: new URLSearchParams({ favorite: next ? '1' : '0' }) });
      if (!response.ok) throw new Error('Favorite unavailable');
      const result = await response.json();
      document.querySelectorAll(`[data-home-card="${id}"]`).forEach(card => card.dataset.favoriteState = result.favorite ? '1' : '0');
      if (favorite.dataset.videoId === id) renderFavorite(Boolean(result.favorite));
      window.dispatchEvent(new CustomEvent('videorecback:home-favorite'));
    } catch {
      if (favorite.dataset.videoId === id) favorite.title = '收藏未能更新，请重试';
    } finally { favorite.disabled = false; }
  });
  let savedScroll = 0;
  let clearTimer;
  const close = async () => {
    if (await window.VideoRecBackPlayer.transition(shell, () => {
      shell.classList.remove('player-open', 'player-loading');
      document.body.classList.remove('home-player-open');
    }, true) === false) return;
    window.scrollTo(0, savedScroll);
    clearTimer = setTimeout(() => frame.src = 'about:blank', 0);
    window.dispatchEvent(new CustomEvent('videorecback:player-closed'));
  };
  document.addEventListener('click', event => {
    const link = event.target.closest('[data-home-video]');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!window.VideoRecBackPlayer.isWide()) return;
    event.preventDefault();
    clearTimeout(clearTimer);
    const wasOpen = shell.classList.contains('player-open');
    const card = link.closest('.home-video');
    const cardOffset = card.getBoundingClientRect().top - content.getBoundingClientRect().top;
    if (!wasOpen) savedScroll = window.scrollY;
    title.textContent = link.closest('.home-video').querySelector('.home-video-title').textContent;
    settings.href = `/video/${link.dataset.homeVideo}`;
    settings.hidden = false;
    favorite.hidden = false;
    favorite.dataset.videoId = link.dataset.homeVideo;
    favorite.removeAttribute('title');
    renderFavorite(card.dataset.favoriteState === '1');
    shell.classList.add('player-loading');
    window.VideoRecBackPlayer.transition(shell, () => {
      shell.classList.add('player-open');
      document.body.classList.add('home-player-open');
      if (!wasOpen) {
        content.scrollTop = 0;
        content.scrollTop = card.getBoundingClientRect().top - content.getBoundingClientRect().top - cardOffset;
      }
    });
    const url = new URL(link.href);
    url.searchParams.set('embed', '1');
    frame.src = url.href;
  });
  shell.querySelector('[data-close-player]').addEventListener('click', close);
  window.addEventListener('resize', () => { if (shell.classList.contains('player-open') && !window.VideoRecBackPlayer.isWide()) close(); });
})();
