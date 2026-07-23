const LONG_PRESS_MS = 520;
const WIDE_VIEWPORT_RATIO = 4 / 3;
const shell = document.querySelector(".app-shell");
const frame = document.querySelector("[data-player-frame]");
const closePlayer = document.querySelector("[data-close-player]");
const resizer = document.querySelector("[data-resizer]");
const previewSize = document.querySelector("[data-preview-size]");
const libraryPane = document.querySelector(".library-pane");
const timelineRoot = document.querySelector("[data-timeline-root]");
const timelineRail = document.querySelector("[data-timeline-jump]");
const inlinePlayerTitle = document.querySelector("[data-inline-player-title]");
const inlineSettings = document.querySelector("[data-inline-settings]");
const inlineFavorite = document.querySelector("[data-inline-favorite]");
const scanForm = document.querySelector("[data-scan-form]");
const scanButton = document.querySelector("[data-scan-button]");
const scanLabel = document.querySelector("[data-scan-label]");
const favoriteContextMenu = document.querySelector("[data-favorite-context-menu]");
const RETURN_STATE_KEY = "videorecback-return-state";
const RETURNING_FROM_PLAYER_KEY = "videorecback-returning-from-player";
const TIMELINE_POSITION_KEY = "videorecback-timeline-position";
let inlineFrameClearTimer = null;
let restoredReturnState = null;
let pendingReturnPosition = null;
let favoriteContextTarget = null;
let inlinePlayerCard = null;

const timelineCache = (() => {
  try {
    return JSON.parse(timelineRoot?.dataset.timelineCache || "{}");
  } catch {
    return {};
  }
})();

const currentTimelineSection = () => {
  return timelineRoot?.dataset.currentPage || document.querySelector("[data-timeline-point]")?.id || "";
};

const capturePanePosition = () => {
  const sectionId = currentTimelineSection();
  const section = sectionId ? document.getElementById(sectionId) : null;
  const paneRect = libraryPane?.getBoundingClientRect();
  const sectionOffset = section && paneRect ? section.getBoundingClientRect().top - paneRect.top : 0;
  return {
    scrollTop: libraryPane?.scrollTop || 0,
    sectionId,
    sectionOffset,
  };
};

const restorePanePosition = (position) => {
  if (!libraryPane || !position) return;
  const sectionId = position.sectionId || position.activeSection;
  const section = sectionId ? document.getElementById(sectionId) : null;
  if (!section) {
    libraryPane.scrollTop = Number(position.scrollTop || 0);
  } else {
    const paneRect = libraryPane.getBoundingClientRect();
    const sectionRect = section.getBoundingClientRect();
    libraryPane.scrollTop += sectionRect.top - paneRect.top - Number(position.sectionOffset || 0);
  }
  window.dispatchEvent(new CustomEvent("videorecback:timeline-layout"));
};

const saveReturnState = (position = capturePanePosition()) => {
  const state = {
    url: `${window.location.pathname}${window.location.search}`,
    hash: window.location.hash,
    scrollTop: position.scrollTop,
    view: new URLSearchParams(window.location.search).get("view") || "timeline",
    activeSection: position.sectionId,
    sectionOffset: position.sectionOffset,
    savedAt: Date.now(),
  };
  const value = JSON.stringify(state);
  sessionStorage.setItem(RETURN_STATE_KEY, value);
  localStorage.setItem(RETURN_STATE_KEY, value);
  return state;
};

const readReturnState = () => {
  for (const storage of [sessionStorage, localStorage]) {
    try {
      const state = JSON.parse(storage.getItem(RETURN_STATE_KEY) || "null");
      if (state && Date.now() - Number(state.savedAt || 0) < 24 * 60 * 60 * 1000) return state;
    } catch {
      storage.removeItem(RETURN_STATE_KEY);
    }
  }
  return null;
};

const currentPageKey = () => `${window.location.pathname}${window.location.search}`;

const readTimelinePosition = () => {
  try {
    const state = JSON.parse(sessionStorage.getItem(TIMELINE_POSITION_KEY) || "null");
    if (!state || state.url !== currentPageKey()) return null;
    if (Date.now() - Number(state.savedAt || 0) > 24 * 60 * 60 * 1000) return null;
    return state;
  } catch {
    sessionStorage.removeItem(TIMELINE_POSITION_KEY);
    return null;
  }
};

const saveTimelinePosition = (position = capturePanePosition()) => {
  if (!libraryPane || !timelineRoot) return;
  try {
    sessionStorage.setItem(
      TIMELINE_POSITION_KEY,
      JSON.stringify({
        url: currentPageKey(),
        scrollTop: position.scrollTop,
        sectionId: position.sectionId,
        sectionOffset: position.sectionOffset,
        savedAt: Date.now(),
      })
    );
  } catch {}
};

const isReloadNavigation = () => {
  const entry = performance.getEntriesByType?.("navigation")?.[0];
  return entry?.type === "reload";
};

const isBackForwardNavigation = () => {
  const entry = performance.getEntriesByType?.("navigation")?.[0];
  return entry?.type === "back_forward";
};

const consumeReturningFromPlayer = () => {
  let returning = false;
  for (const storage of [sessionStorage, localStorage]) {
    try {
      if (storage.getItem(RETURNING_FROM_PLAYER_KEY) === "1") returning = true;
      storage.removeItem(RETURNING_FROM_PLAYER_KEY);
    } catch {}
  }
  return returning;
};

const restoreReturnState = () => {
  if (!consumeReturningFromPlayer() && !isBackForwardNavigation()) return;
  const state = readReturnState();
  if (!state || !libraryPane) return;
  const expectedUrl = `${window.location.pathname}${window.location.search}`;
  if (state.url && state.url !== expectedUrl) return;
  if (state.hash && window.location.hash !== state.hash) {
    window.history.replaceState(null, "", `${expectedUrl}${state.hash}`);
  }
  restoredReturnState = state;
  const top = Number(state.scrollTop || 0);
  libraryPane.scrollTop = top;
  window.requestAnimationFrame(() => {
    libraryPane.scrollTop = top;
    window.dispatchEvent(new CustomEvent("videorecback:timeline-layout"));
  });
};

restoreReturnState();

scanForm?.addEventListener("submit", () => {
  scanButton?.classList.add("is-scanning");
  if (scanLabel) scanLabel.textContent = "更新中";
});

if (scanForm?.dataset.scanRunning === "1") {
  const pollScanStatus = async () => {
    try {
      const response = await fetch("/scan/status", { cache: "no-store" });
      if (!response.ok) throw new Error("Scan status request failed");
      const status = await response.json();
      if (!status.scanning) {
        const url = new URL(window.location.href);
        url.searchParams.delete("scan");
        window.location.replace(url.toString());
        return;
      }
      if (scanLabel) {
        scanLabel.textContent = status.indexing
          ? "建立索引中"
          : `处理媒体 ${Number(status.pending_media || 0)}`;
      }
    } catch {}
    window.setTimeout(pollScanStatus, 750);
  };
  window.setTimeout(pollScanStatus, 300);
}

if (previewSize) {
  const savedSize = localStorage.getItem("videorecback-card-size") || previewSize.value;
  previewSize.value = savedSize;
  document.documentElement.style.setProperty("--card-size", `${savedSize}px`);
  previewSize.addEventListener("input", () => {
    document.documentElement.style.setProperty("--card-size", `${previewSize.value}px`);
    localStorage.setItem("videorecback-card-size", previewSize.value);
  });
}

let revealObserver = null;

const registerRevealTargets = (root = document) => {
  if (!shell) return;
  const targets = [...root.querySelectorAll(".asset-item:not(.is-visible), .folder-tile:not(.is-visible)")];
  if (!targets.length) return;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reducedMotion || !("IntersectionObserver" in window)) {
    for (const target of targets) target.classList.add("is-visible");
    return;
  }
  shell.classList.add("is-reveal-ready");
  targets.forEach((target, index) => {
    target.style.setProperty("--reveal-delay", `${Math.min(index % 10, 9) * 18}ms`);
  });
  if (!revealObserver) {
    revealObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add("is-visible");
        revealObserver.unobserve(entry.target);
      }
    }, {
      root: libraryPane || null,
      rootMargin: "90px 0px",
      threshold: 0.08,
    });
  }
  for (const target of targets) revealObserver.observe(target);
};

registerRevealTargets();

const eventPoint = (event, fallbackElement = null) => {
  const touch = event?.touches?.[0] || event?.changedTouches?.[0];
  if (touch) return { x: touch.clientX, y: touch.clientY };
  if (Number.isFinite(event?.clientX) && Number.isFinite(event?.clientY)) {
    return { x: event.clientX, y: event.clientY };
  }
  const rect = fallbackElement?.getBoundingClientRect();
  return {
    x: rect ? rect.left + rect.width / 2 : window.innerWidth / 2,
    y: rect ? rect.top + rect.height / 2 : window.innerHeight / 2,
  };
};

const hideFavoriteContextMenu = () => {
  if (!favoriteContextMenu) return;
  favoriteContextMenu.hidden = true;
  favoriteContextTarget = null;
};

const openFavoriteContextMenu = (card, eventOrPoint) => {
  if (!favoriteContextMenu) return;
  const point = Number.isFinite(eventOrPoint?.x) && Number.isFinite(eventOrPoint?.y)
    ? eventOrPoint
    : eventPoint(eventOrPoint, card);
  favoriteContextTarget = card;
  favoriteContextMenu.hidden = false;
  const rect = favoriteContextMenu.getBoundingClientRect();
  const left = Math.max(8, Math.min(point.x, window.innerWidth - rect.width - 8));
  const top = Math.max(8, Math.min(point.y, window.innerHeight - rect.height - 8));
  favoriteContextMenu.style.left = `${left}px`;
  favoriteContextMenu.style.top = `${top}px`;
};

favoriteContextMenu?.addEventListener("click", (event) => {
  const targetElement = event.target instanceof Element ? event.target : null;
  const action = targetElement?.closest("[data-favorite-action]")?.dataset.favoriteAction;
  if (!action || !favoriteContextTarget) return;
  event.preventDefault();
  const target = favoriteContextTarget;
  hideFavoriteContextMenu();
  if (action === "timeline") {
    window.location.href = target.dataset.timelineUrl || "/?view=timeline";
  } else if (action === "settings") {
    window.location.href = target.dataset.settingsUrl || "#";
  }
});

document.addEventListener("click", (event) => {
  if (!favoriteContextMenu || favoriteContextMenu.hidden) return;
  const targetElement = event.target instanceof Element ? event.target : null;
  if (!targetElement) return;
  if (favoriteContextMenu.contains(targetElement) || targetElement.closest("[data-favorite-menu]")) return;
  hideFavoriteContextMenu();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") hideFavoriteContextMenu();
});

window.addEventListener("resize", hideFavoriteContextMenu);

const favoriteLabelFor = (button) => button?.querySelector("[data-favorite-label]");

const setFavoriteButtonState = (button, favorite) => {
  if (!button) return;
  button.dataset.favoriteState = favorite ? "1" : "0";
  button.classList.toggle("active", favorite);
  button.setAttribute("aria-pressed", favorite ? "true" : "false");
  const label = favoriteLabelFor(button);
  if (label) label.textContent = favorite ? "已收藏" : "收藏";
};

const configureFavoriteButton = (button, card) => {
  if (!button || !card) return;
  button.hidden = false;
  button.dataset.videoId = card.dataset.videoId || "";
  setFavoriteButtonState(button, card.dataset.favoriteState === "1");
};

const setCardFavoriteState = (card, favorite) => {
  if (!card) return;
  card.dataset.favoriteState = favorite ? "1" : "0";
  if (card === inlinePlayerCard) setFavoriteButtonState(inlineFavorite, favorite);
};

const bindFavoriteControl = (button, currentCard) => {
  button?.addEventListener("click", async () => {
    const card = currentCard();
    const videoId = button.dataset.videoId || card?.dataset.videoId;
    if (!videoId) return;
    const nextFavorite = button.dataset.favoriteState !== "1";
    button.disabled = true;
    try {
      const body = new URLSearchParams({ favorite: nextFavorite ? "1" : "0" });
      const response = await fetch(`/video/${encodeURIComponent(videoId)}/favorite`, {
        method: "POST",
        body,
      });
      if (!response.ok) throw new Error("Favorite request failed");
      const payload = await response.json();
      setCardFavoriteState(card, Boolean(payload.favorite));
    } catch {
      setFavoriteButtonState(button, !nextFavorite);
    } finally {
      button.disabled = false;
    }
  });
};

bindFavoriteControl(inlineFavorite, () => inlinePlayerCard);

const playerUrlForCard = (card) => {
  const url = new URL(card.href, window.location.origin);
  url.searchParams.set("embed", "1");
  return url.toString();
};

const playerPageUrlForCard = (card) => {
  const url = new URL(card.href, window.location.origin);
  url.searchParams.set("return", `${window.location.pathname}${window.location.search}${window.location.hash}`);
  return url.toString();
};

const isWideViewport = () => window.innerWidth / Math.max(window.innerHeight, 1) > WIDE_VIEWPORT_RATIO;

const titleForCard = (card) => card.getAttribute("aria-label")?.replace(/^播放\s*/, "") || "";

const setPlayerTargetCard = (card) => {
  for (const activeCard of document.querySelectorAll(".asset-card.is-player-target")) {
    activeCard.classList.remove("is-player-target");
  }
  card?.classList.add("is-player-target");
};

const openInlinePlayer = (card, panePosition) => {
  if (!shell || !frame) return;
  if (inlineFrameClearTimer) window.clearTimeout(inlineFrameClearTimer);
  inlinePlayerCard = card;
  setPlayerTargetCard(card);
  shell.classList.add("player-open");
  restorePanePosition(panePosition);
  frame.src = playerUrlForCard(card);
  if (inlinePlayerTitle) inlinePlayerTitle.textContent = titleForCard(card);
  if (inlineSettings) {
    inlineSettings.href = card.dataset.settingsUrl || "#";
    inlineSettings.hidden = !card.dataset.settingsUrl;
  }
  configureFavoriteButton(inlineFavorite, card);
  window.requestAnimationFrame(() => restorePanePosition(panePosition));
  window.setTimeout(() => restorePanePosition(panePosition), 0);
};

const closeInlinePlayer = () => {
  const panePosition = capturePanePosition();
  shell?.classList.remove("player-open");
  setPlayerTargetCard(null);
  restorePanePosition(panePosition);
  if (frame) {
    inlineFrameClearTimer = window.setTimeout(() => {
      frame.src = "about:blank";
      inlineFrameClearTimer = null;
    }, 180);
  }
  inlinePlayerCard = null;
  if (inlinePlayerTitle) inlinePlayerTitle.textContent = "";
  if (inlineSettings) inlineSettings.hidden = true;
  if (inlineFavorite) inlineFavorite.hidden = true;
  window.requestAnimationFrame(() => {
    restorePanePosition(panePosition);
  });
};

const openPlayerPage = (card) => {
  window.location.assign(playerPageUrlForCard(card));
};

let longPressTimer = null;
let longPressCard = null;
let longPressTriggered = false;

const eventCard = (event) => {
  const target = event.target instanceof Element ? event.target : null;
  return target?.closest("[data-settings-url]") || null;
};

document.addEventListener("contextmenu", (event) => {
  const card = eventCard(event);
  if (!card) return;
  event.preventDefault();
  if (card.dataset.favoriteMenu === "1") openFavoriteContextMenu(card, event);
  else window.location.href = card.dataset.settingsUrl;
});

document.addEventListener("pointerdown", (event) => {
  if (!eventCard(event)) return;
  pendingReturnPosition = capturePanePosition();
}, { passive: true });

document.addEventListener("click", (event) => {
  const card = eventCard(event);
  if (!card) return;
  if (longPressTriggered && card === longPressCard) {
    event.preventDefault();
    longPressTriggered = false;
    return;
  }
  const panePosition = pendingReturnPosition || capturePanePosition();
  pendingReturnPosition = null;
  saveReturnState(panePosition);
  event.preventDefault();
  if (isWideViewport() && shell && frame) openInlinePlayer(card, panePosition);
  else openPlayerPage(card);
});

document.addEventListener("touchstart", (event) => {
  const card = eventCard(event);
  if (!card) return;
  longPressCard = card;
  longPressTriggered = false;
  const point = eventPoint(event, card);
  longPressTimer = window.setTimeout(() => {
    longPressTriggered = true;
    if (card.dataset.favoriteMenu === "1") openFavoriteContextMenu(card, point);
    else window.location.href = card.dataset.settingsUrl;
  }, LONG_PRESS_MS);
}, { passive: true });

for (const eventName of ["touchend", "touchmove", "touchcancel"]) {
  document.addEventListener(eventName, () => {
    if (longPressTimer) window.clearTimeout(longPressTimer);
    longPressTimer = null;
  }, { passive: true });
}

closePlayer?.addEventListener("click", closeInlinePlayer);

inlineSettings?.addEventListener("click", (event) => {
  const href = inlineSettings.getAttribute("href");
  if (!href || href === "#") return;
  event.preventDefault();
  window.location.href = href;
});

if (resizer && shell) {
  let resizing = false;
  resizer.addEventListener("pointerdown", (event) => {
    resizing = true;
    resizer.setPointerCapture(event.pointerId);
  });
  resizer.addEventListener("pointermove", (event) => {
    if (!resizing) return;
    const rect = shell.getBoundingClientRect();
    const width = Math.max(360, Math.min(rect.width * 0.62, rect.right - event.clientX));
    shell.style.setProperty("--player-width", `${Math.round(width)}px`);
    window.dispatchEvent(new CustomEvent("videorecback:timeline-layout"));
  });
  for (const name of ["pointerup", "pointercancel"]) {
    resizer.addEventListener(name, () => {
      resizing = false;
      window.dispatchEvent(new CustomEvent("videorecback:timeline-layout"));
    });
  }
}

if (libraryPane && shell) {
  const syncScrolledState = () => {
    shell.classList.toggle("is-scrolled", libraryPane.scrollTop > 8);
  };
  libraryPane.addEventListener("scroll", syncScrolledState, { passive: true });
  syncScrolledState();
}

const timelineStack = document.querySelector("[data-timeline-stack]");
const timelineLoadSentinel = document.querySelector("[data-timeline-load]");
let timelineBatchPromise = null;

const refreshTimelineCounts = (section) => {
  for (const day of section.querySelectorAll(":scope > [data-timeline-day]")) {
    const dayCount = day.querySelectorAll(".asset-item").length;
    const label = day.querySelector(".timeline-day-divider small");
    if (label) label.textContent = `${dayCount} 个视频`;
  }
  const sectionCount = section.querySelectorAll(".asset-item").length;
  const label = section.querySelector(".timeline-section-head span");
  if (label) label.textContent = `${sectionCount} 个视频`;
};

const mergeTimelineSection = (incomingSection) => {
  const existingSection = document.getElementById(incomingSection.id);
  if (!existingSection) {
    timelineStack?.append(incomingSection);
    return incomingSection;
  }
  for (const incomingDay of [...incomingSection.querySelectorAll(":scope > [data-timeline-day]")]) {
    const existingDay = document.getElementById(incomingDay.id);
    if (!existingDay) {
      existingSection.append(incomingDay);
      continue;
    }
    const existingGrid = existingDay.querySelector(".timeline-asset-grid");
    const incomingItems = incomingDay.querySelectorAll(".asset-item");
    existingGrid?.append(...incomingItems);
  }
  refreshTimelineCounts(existingSection);
  return existingSection;
};

const loadNextTimelineBatch = () => {
  if (timelineBatchPromise) return timelineBatchPromise;
  if (!timelineRoot || !timelineStack || !timelineLoadSentinel) return Promise.resolve(false);
  if (timelineRoot.dataset.hasMore !== "1") return Promise.resolve(false);
  const nextMtime = timelineRoot.dataset.nextMtime;
  const nextId = timelineRoot.dataset.nextId;
  if (!nextMtime || !nextId) return Promise.resolve(false);
  timelineBatchPromise = (async () => {
    timelineLoadSentinel.textContent = "正在加载";
    try {
      const url = new URL(timelineRoot.dataset.batchUrl || "/timeline-batch", window.location.origin);
      url.searchParams.set("cursor_mtime", nextMtime);
      url.searchParams.set("cursor_id", nextId);
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Timeline batch request failed");
      const payload = await response.json();
      const container = document.createElement("div");
      container.innerHTML = payload.html || "";
      const changedSections = [];
      for (const section of [...container.querySelectorAll(":scope > [data-timeline-section]")]) {
        changedSections.push(mergeTimelineSection(section));
      }
      for (const section of changedSections) registerRevealTargets(section);
      timelineRoot.dataset.hasMore = payload.has_more ? "1" : "0";
      if (payload.next_cursor) {
        timelineRoot.dataset.nextMtime = String(payload.next_cursor.mtime);
        timelineRoot.dataset.nextId = String(payload.next_cursor.id);
      }
      timelineLoadSentinel.hidden = !payload.has_more;
      timelineLoadSentinel.textContent = payload.has_more ? "继续加载" : "";
      window.dispatchEvent(new CustomEvent("videorecback:timeline-batch"));
      return true;
    } catch {
      timelineLoadSentinel.textContent = "加载失败，滚动后重试";
      return false;
    }
  })();
  timelineBatchPromise.finally(() => {
    timelineBatchPromise = null;
  });
  return timelineBatchPromise;
};

if (timelineLoadSentinel && "IntersectionObserver" in window) {
  const timelineLoader = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) loadNextTimelineBatch();
  }, { root: libraryPane || null, rootMargin: "800px 0px" });
  timelineLoader.observe(timelineLoadSentinel);
}

if (timelineRail && libraryPane) {
  const marks = [...timelineRail.querySelectorAll(".timeline-jump-mark")];
  let sections = [...document.querySelectorAll("[data-timeline-section]")];
  let points = [...document.querySelectorAll("[data-timeline-point]")];
  let pointById = new Map(points.map((point) => [point.id, point]));
  let sectionByMonth = new Map();
  let pointOffsets = [];
  const markByPeriod = new Map(marks.map((mark) => [mark.dataset.period, mark]));
  const groupMetaByAnchor = new Map();
  for (const group of Array.isArray(timelineCache.groups) ? timelineCache.groups : []) {
    groupMetaByAnchor.set(group.anchor, group);
    for (const day of Array.isArray(group.days) ? group.days : []) {
      groupMetaByAnchor.set(day.anchor, day);
    }
  }
  let timelineJumping = false;
  let timelineJumpTimer = null;
  let timelineFrame = 0;
  let timelineSaveTimer = null;
  let currentPoint = null;
  let currentMonthSection = null;
  let currentRailMark = null;
  let scaledMonth = "";

  for (const section of sections) {
    section.tabIndex = -1;
  }
  for (const point of points) {
    point.tabIndex = -1;
  }

  const two = (value) => String(value).padStart(2, "0");
  const dayIndex = (year, month, day) => Date.UTC(Number(year), Number(month) - 1, Number(day)) / 86400000;
  const monthIndex = (year, month) => Number(year) * 12 + Number(month);
  const sectionMeta = (section) => groupMetaByAnchor.get(section.id) || {
    anchor: section.id,
    label: section.dataset.label || "",
    granularity: section.dataset.granularity || "day",
    year: Number(section.dataset.year || 0),
    month: Number(section.dataset.month || 1),
    day: Number(section.dataset.day || 1),
  };
  const matchingPoint = (predicate) => points.find((point) => predicate(sectionMeta(point)));
  const sectionForTimelineId = (rawId) => {
    const id = String(rawId || "").replace(/^#/, "");
    if (!id) return null;
    if (pointById.has(id)) return pointById.get(id);
    const dayMatch = id.match(/^timeline-(\d{4})-(\d{2})-(\d{2})$/);
    if (dayMatch) {
      const [, year, month, day] = dayMatch;
      return matchingPoint((meta) => String(meta.year) === year && two(meta.month) === month && two(meta.day) === day) ||
        matchingPoint((meta) => String(meta.year) === year && two(meta.month) === month) ||
        matchingPoint((meta) => String(meta.year) === year);
    }
    const monthMatch = id.match(/^timeline-(\d{4})-(\d{2})$/);
    if (monthMatch) {
      const [, year, month] = monthMatch;
      return matchingPoint((meta) => String(meta.year) === year && two(meta.month) === month) ||
        matchingPoint((meta) => String(meta.year) === year);
    }
    const yearMatch = id.match(/^timeline-(\d{4})$/);
    if (yearMatch) return matchingPoint((meta) => String(meta.year) === yearMatch[1]);
    return null;
  };
  const sectionForMark = (mark) => {
    const targetSection = sectionForTimelineId(mark.dataset.targetAnchor);
    if (targetSection) return targetSection;
    return sectionForTimelineId(mark.getAttribute("href") || "");
  };
  const offsetTopInPane = (section) => {
    let top = 0;
    let node = section;
    while (node && node !== libraryPane) {
      top += node.offsetTop || 0;
      node = node.offsetParent;
    }
    return top;
  };
  const topForSection = (section) => {
    return Math.max(0, offsetTopInPane(section) - 12);
  };
  const refreshTimelineCollections = () => {
    sections = [...document.querySelectorAll("[data-timeline-section]")];
    points = [...document.querySelectorAll("[data-timeline-point]")];
    pointById = new Map(points.map((point) => [point.id, point]));
    sectionByMonth = new Map(sections.map((section) => [
      `${section.dataset.year}-${String(section.dataset.month).padStart(2, "0")}`,
      section,
    ]));
    pointOffsets = points.map((point) => topForSection(point));
    for (const point of points) point.tabIndex = -1;
  };
  refreshTimelineCollections();
  const activeSection = () => {
    if (!points.length) return null;
    const anchor = libraryPane.scrollTop + Math.min(320, libraryPane.clientHeight * 0.36);
    let low = 0;
    let high = pointOffsets.length - 1;
    let match = 0;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (pointOffsets[middle] <= anchor) {
        match = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    return points[match];
  };
  const jumpToSection = (section, hash, replace = false) => {
    if (!section) return;
    if (timelineJumpTimer) window.clearTimeout(timelineJumpTimer);
    timelineJumping = true;
    timelineRoot?.classList.add("is-jumping");
    libraryPane.style.scrollBehavior = "auto";
    const align = () => {
      libraryPane.scrollTop = topForSection(section);
      const paneRect = libraryPane.getBoundingClientRect();
      const sectionRect = section.getBoundingClientRect();
      const delta = sectionRect.top - paneRect.top - 12;
      if (Number.isFinite(delta) && Math.abs(delta) > 1) {
        libraryPane.scrollTop += delta;
      }
    };
    align();
    section.dataset.pageLoaded = "1";
    if (hash) {
      const method = replace ? "replaceState" : "pushState";
      window.history[method](null, "", hash);
    }
    window.requestAnimationFrame(() => {
      align();
      window.requestAnimationFrame(() => {
        align();
        updateTimelineCurrent(section);
        saveTimelinePosition();
        timelineJumpTimer = window.setTimeout(() => {
          timelineJumping = false;
          timelineRoot?.classList.remove("is-jumping");
        }, 80);
      });
    });
  };
  const jumpToSavedOffset = (top) => {
    if (!Number.isFinite(top)) return;
    if (timelineJumpTimer) window.clearTimeout(timelineJumpTimer);
    timelineJumping = true;
    timelineRoot?.classList.add("is-jumping");
    libraryPane.style.scrollBehavior = "auto";
    libraryPane.scrollTop = Math.max(0, top);
    window.requestAnimationFrame(() => {
      updateTimelineCurrent();
      timelineJumpTimer = window.setTimeout(() => {
        timelineJumping = false;
        timelineRoot?.classList.remove("is-jumping");
      }, 80);
    });
  };
  const restoreSavedTimelinePosition = () => {
    if (!restoredReturnState) return;
    if (timelineJumpTimer) window.clearTimeout(timelineJumpTimer);
    timelineJumping = true;
    timelineRoot?.classList.add("is-jumping");
    restorePanePosition(restoredReturnState);
    timelineJumpTimer = window.setTimeout(() => {
      timelineJumping = false;
      timelineRoot?.classList.remove("is-jumping");
      updateTimelineCurrent();
      saveTimelinePosition();
    }, 100);
  };
  const restoreReloadTimelinePosition = (position) => {
    if (!position) return;
    if (timelineJumpTimer) window.clearTimeout(timelineJumpTimer);
    timelineJumping = true;
    timelineRoot?.classList.add("is-jumping");
    restorePanePosition(position);
    timelineJumpTimer = window.setTimeout(() => {
      timelineJumping = false;
      timelineRoot?.classList.remove("is-jumping");
      updateTimelineCurrent();
      saveTimelinePosition();
    }, 100);
  };
  const visibleMark = (mark, meta) => {
    if (mark.dataset.kind === "year") return true;
    if (mark.dataset.kind === "month") {
      return Number(mark.dataset.year) === Number(meta.year) ||
        Math.abs(monthIndex(mark.dataset.year, mark.dataset.month) - monthIndex(meta.year, meta.month)) <= 2;
    }
    if (mark.dataset.kind === "day") {
      return Number(mark.dataset.year) === Number(meta.year) && Number(mark.dataset.month) === Number(meta.month) ||
        Math.abs(dayIndex(mark.dataset.year, mark.dataset.month, mark.dataset.day) - dayIndex(meta.year, meta.month, meta.day)) <= 3;
    }
    return false;
  };
  const applyTimelineScale = (section) => {
    if (!section) return;
    const meta = sectionMeta(section);
    const scaleKey = `${meta.year}-${two(meta.month)}`;
    if (scaleKey === scaledMonth) return;
    scaledMonth = scaleKey;
    for (const mark of marks) {
      const visible = visibleMark(mark, meta);
      mark.hidden = !visible;
      mark.classList.toggle("is-density-day", visible && mark.dataset.kind === "day");
      mark.classList.toggle("is-density-month", visible && mark.dataset.kind === "month");
      mark.classList.toggle("is-density-year", visible && mark.dataset.kind === "year");
    }
    for (const group of timelineRail.querySelectorAll(".timeline-jump-year")) {
      group.hidden = !group.querySelector(".timeline-jump-mark:not([hidden])");
    }
  };
  const currentMarkForSection = (section) => {
    const meta = sectionMeta(section);
    const dateKey = `${meta.year}-${two(meta.month)}-${two(meta.day)}`;
    const monthKey = `${meta.year}-${two(meta.month)}`;
    const yearKey = `${meta.year}`;
    const candidates = [markByPeriod.get(dateKey), markByPeriod.get(monthKey), markByPeriod.get(yearKey)];
    return candidates.find((mark) => mark && !mark.hidden) || null;
  };
  const keepMarkVisible = (mark) => {
    if (!mark) return;
    const railRect = timelineRail.getBoundingClientRect();
    const markRect = mark.getBoundingClientRect();
    if (markRect.top < railRect.top) {
      timelineRail.scrollTop -= railRect.top - markRect.top + 8;
    } else if (markRect.bottom > railRect.bottom) {
      timelineRail.scrollTop += markRect.bottom - railRect.bottom + 8;
    }
  };
  const updateTimelineCurrent = (forcedSection = null) => {
    if (!sections.length) return;
    const currentSection = forcedSection || activeSection();
    if (!currentSection) return;
    const meta = sectionMeta(currentSection);
    applyTimelineScale(currentSection);
    const mark = currentMarkForSection(currentSection);
    timelineRoot?.setAttribute("data-current-page", currentSection.id);
    const monthSection = sectionByMonth.get(`${meta.year}-${two(meta.month)}`) || null;
    if (currentMonthSection !== monthSection) currentMonthSection?.classList.remove("is-current-section");
    monthSection?.classList.add("is-current-section");
    currentMonthSection = monthSection;
    if (currentPoint !== currentSection) currentPoint?.classList.remove("is-current-point");
    currentSection.classList.add("is-current-point");
    currentPoint = currentSection;
    if (currentRailMark !== mark) currentRailMark?.classList.remove("is-current");
    mark?.classList.add("is-current");
    currentRailMark = mark;
    keepMarkVisible(mark);
  };
  const requestTimelineUpdate = () => {
    if (timelineJumping || timelineFrame) return;
    timelineFrame = window.requestAnimationFrame(() => {
      timelineFrame = 0;
      updateTimelineCurrent();
    });
  };
  const scheduleTimelinePositionSave = () => {
    if (timelineJumping) return;
    if (timelineSaveTimer) window.clearTimeout(timelineSaveTimer);
    timelineSaveTimer = window.setTimeout(() => {
      timelineSaveTimer = null;
      saveTimelinePosition();
    }, 120);
  };

  for (const mark of marks) {
    mark.addEventListener("click", async (event) => {
      const href = mark.getAttribute("href") || "";
      if (!href.startsWith("#timeline-")) return;
      event.preventDefault();
      const targetId = String(mark.dataset.targetAnchor || href).replace(/^#/, "");
      let section = document.getElementById(targetId);
      let remainingLoads = 100;
      while (!section && timelineRoot?.dataset.hasMore === "1" && remainingLoads > 0) {
        const loaded = await loadNextTimelineBatch();
        if (!loaded) break;
        section = document.getElementById(targetId);
        remainingLoads -= 1;
      }
      section ||= sectionForMark(mark);
      if (!section) return;
      jumpToSection(section, mark.dataset.targetAnchor || href);
    });
  }

  libraryPane.addEventListener("scroll", () => {
    requestTimelineUpdate();
    scheduleTimelinePositionSave();
  }, { passive: true });
  const refreshTimelineLayout = () => {
    refreshTimelineCollections();
    scaledMonth = "";
    updateTimelineCurrent();
  };
  window.addEventListener("resize", refreshTimelineLayout);
  window.addEventListener("videorecback:timeline-layout", refreshTimelineLayout);
  window.addEventListener("videorecback:timeline-batch", refreshTimelineLayout);
  window.addEventListener("hashchange", () => {
    if (restoredReturnState) return;
    const section = sectionForTimelineId(window.location.hash);
    if (section) jumpToSection(section, window.location.hash, true);
  });
  const reloadTimelinePosition = !restoredReturnState && isReloadNavigation() ? readTimelinePosition() : null;
  if (restoredReturnState) {
    restoreSavedTimelinePosition();
    window.requestAnimationFrame(() => {
      restoreSavedTimelinePosition();
      window.requestAnimationFrame(restoreSavedTimelinePosition);
    });
    window.setTimeout(restoreSavedTimelinePosition, 180);
    window.setTimeout(restoreSavedTimelinePosition, 360);
    window.setTimeout(restoreSavedTimelinePosition, 700);
    window.setTimeout(() => {
      restoredReturnState = null;
    }, 820);
  } else if (reloadTimelinePosition) {
    if (window.location.hash) window.history.replaceState(null, "", currentPageKey());
    restoreReloadTimelinePosition(reloadTimelinePosition);
    window.requestAnimationFrame(() => {
      restoreReloadTimelinePosition(reloadTimelinePosition);
      window.requestAnimationFrame(() => restoreReloadTimelinePosition(reloadTimelinePosition));
    });
    window.setTimeout(() => restoreReloadTimelinePosition(reloadTimelinePosition), 180);
    window.setTimeout(() => restoreReloadTimelinePosition(reloadTimelinePosition), 360);
    window.setTimeout(() => restoreReloadTimelinePosition(reloadTimelinePosition), 700);
  } else {
    const initialSection = sectionForTimelineId(window.location.hash);
    if (initialSection) window.requestAnimationFrame(() => jumpToSection(initialSection, window.location.hash, true));
    else updateTimelineCurrent();
  }
}
