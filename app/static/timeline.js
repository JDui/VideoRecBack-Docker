(() => {
  if (!timelineRoot?.hasAttribute("data-timeline-gallery") || !libraryPane) return;
  const position = timelineRoot.querySelector("[data-gallery-position]");
  const feedback = timelineRoot.querySelector("[data-gallery-feedback]");
  const scrubber = timelineRoot.querySelector("[data-gallery-scrubber]");
  const scrubDate = timelineRoot.querySelector("[data-gallery-scrub-date]");
  const scrubArea = scrubber.parentElement;
  const currentTick = timelineRoot.querySelector("[data-gallery-current-tick]");
  const tickContainer = timelineRoot.querySelector("[data-gallery-date-ticks]");
  const previousSentinel = timelineRoot.querySelector("[data-gallery-previous]");
  const yearMarks = [...timelineRail.querySelectorAll('[data-kind="year"]')];
  const dateIndex = JSON.parse(timelineRoot.querySelector("[data-gallery-date-index]").textContent);
  const dates = dateIndex.map(entry => entry.date);
  const dateEntries = new Map(dateIndex.map(entry => [entry.date, entry]));
  const favoriteStates = new Map();
  const confirmedFavorites = new Map();
  const dateTicks = new Map();
  const densityAxis = buildDensityAxis(dateIndex);
  let cards = [];
  let rows = [];
  let requestId = 0;
  let controller = null;
  let scrolling = false;
  let dragging = false;
  let pointerId = null;
  let hoveredDate = null;
  let hoveredValue = 0;
  let currentDate = dates[0];
  let pendingDate = null;
  let selectionId = 0;
  let restoring = true;
  let layoutFrame = 0;
  let saveTimer = 0;
  let hasPrevious = false;
  let previousPromise = null;
  let windowStartId = "";

  const { valueFor, dateFor } = densityAxis;
  const setCurrentValue = date => {
    currentDate = date;
    const value = valueFor(date);
    scrubber.setAttribute("aria-valuenow", String(value));
    currentTick.style.top = "calc(8px + (100% - 16px) * " + value / 1000 + ")";
    scrubber.setAttribute("aria-valuetext", date.replace(/-/g, "/"));
  };
  const showDate = (date, value = valueFor(date)) => {
    if (!date) return;
    const [year, month, day] = date.split("-").map(Number);
    const entry = dateEntries.get(date);
    scrubDate.textContent = year + "年" + month + "月" + day + "日\n" + entry.count + " 个视频" +
      (entry.favorite_count ? " · " + entry.favorite_count + " 个收藏" : "");
    const railHeight = scrubber.clientHeight - 16;
    scrubDate.style.top = Math.max(28, Math.min(scrubber.clientHeight - 28, 8 + value / 1000 * railHeight)) + "px";
  };
  const refreshFavoriteTicks = () => {
    dateEntries.forEach((entry, date) => dateTicks.get(date)?.classList.toggle("is-favorite", entry.favorite_count > 0));
    if (hoveredDate) showDate(hoveredDate, hoveredValue);
    else if (document.activeElement === scrubber) showDate(currentDate);
  };
  const syncFavoriteState = (id, date, favorite) => {
    const previous = favoriteStates.get(id);
    if (previous !== undefined && previous !== favorite) {
      const entry = dateEntries.get(date);
      if (entry) entry.favorite_count = Math.max(0, entry.favorite_count + (favorite ? 1 : -1));
    }
    favoriteStates.set(id, favorite);
  };
  const syncFavorite = link => syncFavoriteState(link.dataset.videoId, link.parentElement.dataset.galleryDate, link.dataset.favoriteState === "1");
  const maximumCount = dateIndex.reduce((maximum, entry) => Math.max(maximum, entry.count), 1);
  densityAxis.bands.forEach((band, index) => {
    const entry = dateIndex[index];
    const segment = document.createElement("span");
    segment.className = "timeline-density-segment";
    segment.style.top = band.start / 10 + "%";
    segment.style.height = (band.end - band.start) / 10 + "%";
    segment.style.opacity = String(0.18 + Math.sqrt(entry.count / maximumCount) * 0.55);
    tickContainer.append(segment);
    const tick = document.createElement("span");
    tick.className = "timeline-date-tick";
    tick.dataset.date = entry.date;
    tick.style.top = valueFor(entry.date) / 10 + "%";
    tickContainer.append(tick);
    dateTicks.set(entry.date, tick);
  });
  refreshFavoriteTicks();
  const cardForAnchor = hash => {
    const id = String(hash || "").replace(/^#/, "");
    if (!id) return null;
    if (id.startsWith("timeline-video-")) return document.getElementById(id);
    const group = document.getElementById(id);
    if (group) return group.querySelector(".timeline-video");
    const date = id.replace(/^timeline-/, "");
    return cards.find(card => card.dataset.galleryDate.startsWith(date)) || null;
  };
  const updatePosition = () => {
    if (!cards.length || !rows.length) return;
    const cutoff = libraryPane.getBoundingClientRect().top + position.parentElement.offsetHeight + 8;
    const row = rows.find(row => row[0].getBoundingClientRect().bottom > cutoff) || rows.at(-1);
    const card = row[0];
    const date = card.dataset.galleryDate;
    const [year, month, day] = date.split("-").map(Number);
    position.textContent = year + " / " + month + "月 / " + day + "日";
    timelineRoot.dataset.currentPage = card.id;
    if (!dragging) {
      setCurrentValue(date);
      if (!hoveredDate) showDate(date);
    }
    for (const mark of yearMarks) {
      const active = mark.dataset.targetAnchor?.includes("timeline-" + year);
      mark.classList.toggle("is-current", Boolean(active));
      if (active) mark.setAttribute("aria-current", "date");
      else mark.removeAttribute("aria-current");
    }
  };
  const bindCards = () => {
    cards = [...timelineStack.querySelectorAll(".timeline-video")];
    let previousYear = "";
    for (const card of cards) {
      const year = card.dataset.galleryDate.slice(0, 4);
      card.querySelector("[data-gallery-year]").hidden = !previousYear || year === previousYear;
      previousYear = year;
      const button = card.querySelector("[data-gallery-favorite]");
      const link = card.querySelector(".asset-card");
      if (confirmedFavorites.has(link.dataset.videoId)) {
        link.dataset.favoriteState = confirmedFavorites.get(link.dataset.videoId) ? "1" : "0";
      }
      syncFavorite(link);
      if (button.dataset.bound) continue;
      button.dataset.bound = "1";
      configureFavoriteButton(button, link);
      bindFavoriteControl(button, () => link);
    }
    refreshFavoriteTicks();
  };
  const layout = () => {
    bindCards();
    const width = timelineStack.getBoundingClientRect().width;
    if (!width) return;
    const gap = parseFloat(getComputedStyle(timelineStack).gap) || 10;
    const target = Math.min(width / (16 / 9), Number(previewSize?.value || 176) * (window.innerWidth <= 600 ? 0.57 : 1.25));
    rows = [];
    let row = [];
    let sum = 0;
    const ratio = card => Math.max(0.5, Math.min(2.4, Number(card.dataset.galleryRatio) || 16 / 9));
    const commit = full => {
      if (!row.length) return;
      const height = full ? (width - gap * (row.length - 1)) / sum : Math.min(target, (width - gap * (row.length - 1)) / sum);
      row.forEach(card => {
        card.style.width = Math.max(1, height * ratio(card) - 0.02) + "px";
        card.style.height = height + "px";
        card.style.aspectRatio = "auto";
      });
      rows.push(row);
      row = [];
      sum = 0;
    };
    cards.forEach(card => {
      if (card.id === windowStartId) commit(false);
      const nextSum = sum + ratio(card);
      if (row.length && nextSum * target + gap * row.length > width &&
          Math.abs(sum * target + gap * (row.length - 1) - width) < Math.abs(nextSum * target + gap * row.length - width)) commit(true);
      row.push(card);
      sum += ratio(card);
      if (sum * target + gap * (row.length - 1) >= width) commit(true);
    });
    commit(false);
    const lastRow = rows.at(-1);
    const bottomPadding = parseFloat(getComputedStyle(libraryPane).paddingBottom) || 0;
    const endSpace = windowStartId && lastRow ? Math.max(20,
      libraryPane.clientHeight - position.parentElement.offsetHeight - lastRow[0].offsetHeight - bottomPadding) : 20;
    timelineRoot.style.setProperty("--gallery-end-space", endSpace + "px");
    timelineRoot.style.setProperty("--rail-height", Math.max(120, libraryPane.clientHeight - 12) + "px");
    let lastLabelPosition = -Infinity;
    yearMarks.forEach(mark => {
      const date = mark.dataset.targetAnchor?.match(/timeline-(\d{4}-\d{2})/)?.[1];
      const target = dates.find(item => item.startsWith(date));
      const value = valueFor(target || dates[0]);
      const labelPosition = value / 1000 * (scrubber.clientHeight - 16);
      mark.hidden = labelPosition - lastLabelPosition < 36;
      if (!mark.hidden) lastLabelPosition = labelPosition;
      mark.style.top = "calc(8px + (100% - 16px) * " + value / 1000 + ")";
    });
    updatePosition();
  };
  const scheduleLayout = () => {
    if (layoutFrame) return;
    layoutFrame = requestAnimationFrame(() => { layoutFrame = 0; layout(); });
  };
  const scrollToCard = card => {
    if (!card) return;
    libraryPane.scrollTop += card.getBoundingClientRect().top - libraryPane.getBoundingClientRect().top - position.parentElement.offsetHeight;
    updatePosition();
    saveTimelinePosition();
  };
  const loadPrevious = (preservePosition = true, duringJump = false) => {
    if (previousPromise) return previousPromise;
    if (!hasPrevious || !cards.length || (!duringJump && timelineRoot.hasAttribute("aria-busy"))) return Promise.resolve(false);
    const windowId = requestId;
    previousPromise = (async () => {
      try {
        if (timelineBatchPromise) await timelineBatchPromise;
        if (windowId !== requestId) return false;
        const first = cards[0];
        const url = new URL(timelineRoot.dataset.batchUrl, location.origin);
        url.searchParams.set("direction", "newer");
        url.searchParams.set("cursor_mtime", first.dataset.galleryMtime);
        url.searchParams.set("cursor_id", first.dataset.galleryId);
        const response = await fetch(url, { signal: controller?.signal, headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error("Timeline newer request failed");
        const data = await response.json();
        if (windowId !== requestId) return false;
        const incoming = document.createElement("div");
        incoming.innerHTML = data.html;
        const existingIds = new Set(cards.map(card => card.id));
        const added = [...incoming.querySelectorAll(".timeline-video")].filter(card => !existingIds.has(card.id));
        hasPrevious = data.has_more && added.length > 0;
        if (!added.length) return false;
        const anchor = cardForAnchor(timelineRoot.dataset.currentPage) || cards[0];
        const anchorTop = anchor.getBoundingClientRect().top;
        let prefix = timelineStack.querySelector("[data-gallery-newer-prefix]");
        if (!prefix) {
          prefix = document.createElement("div");
          prefix.className = "timeline-newer-prefix";
          prefix.setAttribute("data-gallery-newer-prefix", "");
          timelineStack.prepend(prefix);
        }
        prefix.prepend(...added);
        registerRevealTargets(prefix);
        layout();
        if (preservePosition) libraryPane.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
        updatePosition();
        saveTimelinePosition();
        feedback.textContent = "";
        return true;
      } catch (error) {
        if (error.name !== "AbortError" && windowId === requestId) feedback.textContent = "更近的视频加载失败，上滑可重试";
        return false;
      }
    })();
    previousPromise.finally(() => { previousPromise = null; });
    return previousPromise;
  };
  const jump = async (date, hash = "", replace = false, force = false) => {
    if (!date) return;
    const currentRequest = ++requestId;
    controller?.abort();
    controller = new AbortController();
    const targetCard = () => hash.startsWith("#timeline-video-") ? cardForAnchor(hash) :
      cards.find(card => card.dataset.galleryDate === date) || cardForAnchor(hash);
    let card = !force && date === cards[0]?.dataset.galleryDate ? (targetCard() || cards[0]) : null;
    timelineRoot.setAttribute("aria-busy", "true");
    feedback.textContent = "正在定位…";
    try {
      if (!card) {
        if (timelineBatchPromise) await timelineBatchPromise;
        if (previousPromise) await previousPromise;
        if (currentRequest !== requestId) return;
        const url = new URL(timelineRoot.dataset.batchUrl, location.origin);
        url.searchParams.set("start_date", date);
        const response = await fetch(url, { signal: controller.signal, headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error("Timeline request failed");
        const data = await response.json();
        if (currentRequest !== requestId) return;
        const incoming = document.createElement("div");
        incoming.innerHTML = data.html;
        if (!incoming.querySelector(".timeline-video")) throw new Error("Timeline date unavailable");
        timelineStack.replaceChildren(...incoming.children);
        windowStartId = timelineStack.querySelector(".timeline-video").id;
        hasPrevious = Boolean(data.has_newer);
        timelineRoot.dataset.hasMore = data.has_more ? "1" : "0";
        timelineRoot.dataset.nextMtime = data.next_cursor ? String(data.next_cursor.mtime) : "";
        timelineRoot.dataset.nextId = data.next_cursor ? String(data.next_cursor.id) : "";
        timelineLoadSentinel.hidden = !data.has_more;
        registerRevealTargets(timelineStack);
        layout();
        await loadPrevious(false, true);
        if (currentRequest !== requestId) return;
        card = targetCard() || cards[0];
      }
      scrollToCard(card);
      if (hash) history[replace ? "replaceState" : "pushState"](null, "", hash);
      else history.replaceState(null, "", currentPageKey());
      feedback.textContent = "";
    } catch (error) {
      if (error.name !== "AbortError" && currentRequest === requestId) feedback.textContent = "定位失败，请重试";
    } finally {
      if (currentRequest === requestId) timelineRoot.removeAttribute("aria-busy");
    }
  };
  const valueAt = event => {
    const bounds = scrubber.getBoundingClientRect();
    return Math.max(0, Math.min(1000, (event.clientY - bounds.top - 8) / Math.max(1, bounds.height - 16) * 1000));
  };
  const previewAt = event => {
    const value = valueAt(event);
    hoveredValue = value;
    hoveredDate = dateFor(value);
    showDate(hoveredDate, value);
    scrubArea.classList.add("is-previewing");
  };
  const commitDate = async date => {
    const selection = ++selectionId;
    pendingDate = date;
    dragging = true;
    scrubArea.classList.add("is-dragging");
    await jump(date, "#timeline-" + date);
    if (selection !== selectionId) return;
    pendingDate = null;
    dragging = false;
    scrubArea.classList.remove("is-dragging");
    updatePosition();
  };
  scrubber.addEventListener("pointermove", previewAt);
  scrubber.addEventListener("pointerleave", () => {
    if (pointerId !== null) return;
    hoveredDate = null;
    scrubArea.classList.remove("is-previewing");
    updatePosition();
  });
  scrubber.addEventListener("pointerdown", event => {
    if (event.button !== 0 || pointerId !== null) return;
    event.preventDefault();
    pointerId = event.pointerId;
    scrubber.setPointerCapture(pointerId);
    scrubber.focus({ preventScroll: true });
    dragging = true;
    scrubArea.classList.add("is-dragging");
    previewAt(event);
  });
  scrubber.addEventListener("pointerup", event => {
    if (event.pointerId !== pointerId) return;
    const date = dateFor(valueAt(event));
    scrubber.releasePointerCapture(pointerId);
    pointerId = null;
    if (event.pointerType !== "mouse") {
      hoveredDate = null;
      scrubArea.classList.remove("is-previewing");
    }
    commitDate(date);
  });
  scrubber.addEventListener("pointercancel", () => {
    pointerId = null;
    dragging = false;
    hoveredDate = null;
    scrubArea.classList.remove("is-dragging", "is-previewing");
    updatePosition();
  });
  scrubber.addEventListener("focus", () => {
    if (!hoveredDate) showDate(currentDate);
  });
  scrubber.addEventListener("keydown", event => {
    const offsets = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1, PageDown: 10, PageUp: -10 };
    if (!(event.key in offsets) && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const current = dates.indexOf(pendingDate || currentDate);
    const index = event.key === "Home" ? 0 : event.key === "End" ? dates.length - 1 :
      Math.max(0, Math.min(dates.length - 1, current + offsets[event.key]));
    hoveredDate = null;
    scrubArea.classList.remove("is-previewing");
    showDate(dates[index]);
    commitDate(dates[index]);
  });
  yearMarks.forEach(mark => mark.addEventListener("click", event => {
    event.preventDefault();
    const date = mark.dataset.targetAnchor?.match(/timeline-(\d{4}-\d{2})/)?.[1];
    const target = dates.find(item => item.startsWith(date)) || dates[0];
    jump(target, mark.dataset.targetAnchor);
  }));
  timelineRoot.querySelector("[data-gallery-latest]").addEventListener("click", () => jump(dates[0], "", false));
  timelineLoadSentinel.addEventListener("click", () => loadNextTimelineBatch());
  libraryPane.addEventListener("scroll", () => {
    if (!scrolling) {
      scrolling = true;
      requestAnimationFrame(() => { scrolling = false; updatePosition(); });
    }
    if (!restoring) {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => saveTimelinePosition(), 120);
      if (libraryPane.scrollTop < 400) loadPrevious();
    }
  }, { passive: true });
  libraryPane.addEventListener("wheel", event => {
    if (event.deltaY < 0 && libraryPane.scrollTop < 400) loadPrevious();
  }, { passive: true });
  if (previousSentinel && "IntersectionObserver" in window) {
    new IntersectionObserver(entries => {
      if (!restoring && entries.some(entry => entry.isIntersecting)) loadPrevious();
    }, { root: libraryPane, rootMargin: "400px 0px 0px" }).observe(previousSentinel);
  }
  previewSize?.addEventListener("input", scheduleLayout);
  window.addEventListener("videorecback:timeline-layout", scheduleLayout);
  window.addEventListener("videorecback:timeline-batch", scheduleLayout);
  window.addEventListener("videorecback:timeline-favorite", event => {
    const { videoId, date, favorite } = event.detail;
    confirmedFavorites.set(videoId, favorite);
    syncFavoriteState(videoId, date, favorite);
    for (const card of cards) {
      const link = card.querySelector(".asset-card");
      if (link.dataset.videoId === videoId) link.dataset.favoriteState = favorite ? "1" : "0";
    }
    refreshFavoriteTicks();
  });
  window.addEventListener("resize", scheduleLayout);
  new ResizeObserver(scheduleLayout).observe(timelineStack);
  new MutationObserver(records => {
    for (const record of records) {
      const link = record.target;
      if (!link.matches(".asset-card") || !timelineStack.contains(link)) continue;
      syncFavorite(link);
      setFavoriteButtonState(link.parentElement.querySelector("[data-gallery-favorite]"), link.dataset.favoriteState === "1");
    }
    refreshFavoriteTicks();
  }).observe(timelineStack, { subtree: true, attributes: true, attributeFilter: ["data-favorite-state"] });
  window.addEventListener("hashchange", () => {
    const hash = location.hash;
    if (!hash) { jump(dates[0], "", true); return; }
    const card = cardForAnchor(hash);
    if (card) jump(card.dataset.galleryDate, hash, true);
    else {
      const prefix = hash.match(/timeline-(\d{4}(?:-\d{2})?(?:-\d{2})?)/)?.[1];
      const date = dates.find(date => date.startsWith(prefix || "!"));
      if (date) jump(date, hash, true);
    }
  });
  layout();
  (async () => {
    const saved = restoredReturnState || (isReloadNavigation() ? readTimelinePosition() : null);
    if (saved) {
      const savedId = saved.sectionId || saved.activeSection;
      const date = saved.timelineDate || savedId?.match(/timeline-(\d{4}-\d{2}-\d{2})/)?.[1];
      if (date && (date !== cards[0]?.dataset.galleryDate || !document.getElementById(savedId))) await jump(date, "", true, true);
      const card = cardForAnchor(savedId);
      restorePanePosition(card ? { ...saved, sectionId: card.id } : saved);
    } else if (location.hash) {
      const prefix = location.hash.match(/timeline-(\d{4}(?:-\d{2})?(?:-\d{2})?)/)?.[1];
      const date = dates.find(date => date.startsWith(prefix || "!"));
      if (date) await jump(date, location.hash, true);
    }
    restoring = false;
    updatePosition();
  })();
})();
