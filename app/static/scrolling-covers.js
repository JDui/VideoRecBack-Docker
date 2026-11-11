(() => {
  const selector = '[data-scrolling-cover]';
  const enabled = document.body.dataset.scrollingCoverEnabled === '1';
  const normalizeCycle = value => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(1200, Math.min(20000, number)) : 6000;
  };
  const cycleMs = normalizeCycle(document.body.dataset.scrollingCoverCycleMs ?? 6000);
  const covers = new Map();
  let suspended = false;
  let mutationObserver;

  // Older portrait caches can still be a 2x2 collage. Inspect the cached image
  // before applying the current layout so existing covers need no regeneration.
  const resolveLayout = (wrapper, image) => {
    const columns = Number(wrapper.dataset.coverColumns) || 2;
    const rows = Number(wrapper.dataset.coverRows) || 2;
    // A current cache uses the generator's layout. Tall phone strips can have
    // the same overall ratio as an old 16:9 grid, so dimensions alone cannot
    // distinguish those images.
    if (wrapper.dataset.coverLayoutCurrent === '1') return [columns, rows];
    const sourceRatio = Number(wrapper.dataset.coverSourceRatio);
    if (!(sourceRatio > 0 && sourceRatio < 1)) return [columns, rows];
    const imageRatio = image.naturalWidth / image.naturalHeight;
    if (Math.abs(imageRatio / (16 / 9) - 1) < 0.02) return [2, 2];
    const candidates = [[columns, rows], [2, 2], [3, 1], [4, 1]];
    candidates.sort((left, right) =>
      Math.abs(Math.log(imageRatio / (sourceRatio * left[0] / left[1]))) -
      Math.abs(Math.log(imageRatio / (sourceRatio * right[0] / right[1]))));
    return candidates[0];
  };

  const stop = cover => {
    clearTimeout(cover.timer);
    cover.timer = 0;
  };
  const draw = cover => {
    const width = cover.wrapper.clientWidth;
    const height = cover.wrapper.clientHeight;
    const tileWidth = cover.sprite.naturalWidth / cover.columns;
    const tileHeight = cover.sprite.naturalHeight / cover.rows;
    if (!width || !height || !tileWidth || !tileHeight) return false;
    const scale = Math.min(width / tileWidth, height / tileHeight);
    const frameWidth = tileWidth * scale;
    const frameHeight = tileHeight * scale;
    const style = cover.frame.style;
    style.width = `${frameWidth}px`;
    style.height = `${frameHeight}px`;
    style.backgroundSize = `${frameWidth * cover.columns}px ${frameHeight * cover.rows}px`;
    style.backgroundPosition = `${-(cover.index % cover.columns) * frameWidth}px ${-Math.floor(cover.index / cover.columns) * frameHeight}px`;
    return true;
  };
  const canPlay = cover => cover.enabled && cover.loaded && cover.visible &&
    cover.wrapper.isConnected && !document.hidden && !suspended;
  const schedule = cover => {
    stop(cover);
    if (!canPlay(cover)) {
      cover.nextFrameAt = 0;
      return;
    }
    const frameMs = cover.cycleMs / (cover.columns * cover.rows);
    const now = performance.now();
    if (!cover.nextFrameAt) cover.nextFrameAt = now + frameMs;
    cover.timer = setTimeout(() => {
      cover.timer = 0;
      if (!canPlay(cover)) {
        cover.nextFrameAt = 0;
        return;
      }
      // Keep the whole loop on one clock rather than accumulating painting
      // and timer delays once per frame.
      const frames = Math.max(1, Math.floor((performance.now() - cover.nextFrameAt) / frameMs) + 1);
      cover.index = (cover.index + frames) % (cover.columns * cover.rows);
      cover.nextFrameAt += frames * frameMs;
      draw(cover);
      schedule(cover);
    }, Math.max(0, cover.nextFrameAt - now));
  };
  const requestSequence = cover => {
    const source = cover.wrapper.dataset.coverSequenceSrc;
    if (!source || cover.sequence || !cover.enabled || !cover.visible ||
        !cover.wrapper.isConnected || document.hidden || suspended) return;
    const sequence = new Image();
    cover.sequence = sequence;
    sequence.onload = () => {
      if (covers.get(cover.wrapper) !== cover || !cover.wrapper.isConnected) return;
      if (!(sequence.naturalWidth > 0 && sequence.naturalHeight > 0)) return;
      cover.sequenceLoaded = true;
      cover.sprite = sequence;
      cover.columns = 4;
      cover.rows = 4;
      cover.loaded = true;
      cover.index = 0;
      cover.nextFrameAt = 0;
      sync(cover);
    };
    // A missing dedicated cache is normal. Keep the main cover playing.
    sequence.onerror = () => {};
    sequence.src = source;
  };
  const sync = cover => {
    stop(cover);
    requestSequence(cover);
    if (!cover.enabled || !cover.loaded) {
      cover.wrapper.classList.remove('is-scrolling-cover');
      return;
    }
    if (!cover.layer) {
      cover.layer = document.createElement('span');
      cover.layer.className = 'scrolling-cover-layer';
      cover.layer.setAttribute('aria-hidden', 'true');
      cover.frame = document.createElement('span');
      cover.frame.className = 'scrolling-cover-frame';
      cover.layer.append(cover.frame);
      cover.wrapper.insertBefore(cover.layer, cover.image);
    }
    cover.frame.style.backgroundImage = `url(${JSON.stringify(cover.sprite.currentSrc || cover.sprite.src)})`;
    if (draw(cover)) cover.wrapper.classList.add('is-scrolling-cover');
    schedule(cover);
  };
  const visibilityObserver = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    for (const entry of entries) {
      const cover = covers.get(entry.target);
      if (!cover) continue;
      cover.visible = entry.isIntersecting;
      requestSequence(cover);
      schedule(cover);
    }
  }) : null;
  const sizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(entries => {
    for (const entry of entries) {
      const cover = covers.get(entry.target);
      if (cover?.enabled && cover.loaded) sync(cover);
    }
  }) : null;

  const remove = cover => {
    stop(cover);
    visibilityObserver?.unobserve(cover.wrapper);
    sizeObserver?.unobserve(cover.wrapper);
    cover.image.removeEventListener('load', cover.onLoad);
    cover.image.removeEventListener('error', cover.onError);
    if (cover.sequence) cover.sequence.onload = cover.sequence.onerror = null;
    cover.layer?.remove();
    cover.wrapper.classList.remove('is-scrolling-cover');
    covers.delete(cover.wrapper);
  };
  const add = wrapper => {
    if (covers.has(wrapper) || (!enabled && !wrapper.hasAttribute('data-cover-preview'))) return;
    const image = wrapper.querySelector('img');
    if (!image) return;
    const cover = {
      wrapper, image, sprite: image, enabled, cycleMs, columns: 2, rows: 2,
      index: 0, timer: 0, nextFrameAt: 0, loaded: false, visible: !visibilityObserver,
      sequence: null, sequenceLoaded: false,
    };
    cover.onLoad = () => {
      if (!cover.sequenceLoaded) {
        cover.loaded = image.naturalWidth > 0 && image.naturalHeight > 0;
        [cover.columns, cover.rows] = resolveLayout(wrapper, image);
        cover.index = 0;
        cover.nextFrameAt = 0;
      }
      sync(cover);
    };
    cover.onError = () => {
      if (!cover.sequenceLoaded) cover.loaded = false;
      sync(cover);
    };
    covers.set(wrapper, cover);
    image.addEventListener('load', cover.onLoad);
    image.addEventListener('error', cover.onError);
    visibilityObserver?.observe(wrapper);
    sizeObserver?.observe(wrapper);
    if (image.complete) cover.onLoad();
  };
  const refresh = (root = document) => {
    if (root.matches?.(selector)) add(root);
    root.querySelectorAll?.(selector).forEach(add);
    for (const cover of covers.values()) if (!cover.wrapper.isConnected) remove(cover);
  };
  window.VideoRecBackScrollingCovers = {
    refresh,
    setPreview(wrapper, options) {
      add(wrapper);
      const cover = covers.get(wrapper);
      if (!cover || !wrapper.hasAttribute('data-cover-preview')) return;
      cover.enabled = Boolean(options.enabled);
      cover.cycleMs = normalizeCycle(options.cycleMs);
      cover.index = 0;
      cover.nextFrameAt = 0;
      sync(cover);
    },
  };

  refresh();
  // Only child additions/removals matter; image sizing and player classes must
  // not cause further observer passes.
  if (enabled || covers.size) {
    mutationObserver = new MutationObserver(records => {
      for (const record of records) for (const node of record.addedNodes) refresh(node);
      for (const cover of covers.values()) if (!cover.wrapper.isConnected) remove(cover);
    });
    mutationObserver.observe(document.body, { childList: true, subtree: true });
  }
  const resume = cover => {
    requestSequence(cover);
    schedule(cover);
  };
  document.addEventListener('visibilitychange', () => covers.forEach(resume));
  window.addEventListener('pagehide', () => {
    suspended = true;
    covers.forEach(cover => {
      stop(cover);
      cover.nextFrameAt = 0;
    });
    mutationObserver?.disconnect();
  });
  window.addEventListener('pageshow', () => {
    suspended = false;
    refresh();
    mutationObserver?.observe(document.body, { childList: true, subtree: true });
    covers.forEach(resume);
  });
})();
