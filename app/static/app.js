const LONG_PRESS_MS = 520;
const DESKTOP_QUERY = "(orientation: landscape)";
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
const playerModal = document.querySelector("[data-player-modal]");
const overlayFrame = document.querySelector("[data-overlay-player-frame]");
const overlayPlayerTitle = document.querySelector("[data-overlay-player-title]");
const overlaySettings = document.querySelector("[data-overlay-settings]");
const overlayFavorite = document.querySelector("[data-overlay-favorite]");
const closeOverlayPlayer = document.querySelector("[data-close-overlay-player]");
const scanForm = document.querySelector("[data-scan-form]");
const scanButton = document.querySelector("[data-scan-button]");
const scanLabel = document.querySelector("[data-scan-label]");
const favoriteContextMenu = document.querySelector("[data-favorite-context-menu]");
const RETURN_STATE_KEY = "videorecback-return-state";
const RETURNING_FROM_PLAYER_KEY = "videorecback-returning-from-player";
let inlineFrameClearTimer = null;
let restoredReturnState = null;
let pendingReturnPosition = null;
let favoriteContextTarget = null;
let inlinePlayerCard = null;
let overlayPlayerCard = null;

const timelineCache = (() => {
  try {
    return JSON.parse(timelineRoot?.dataset.timelineCache || "{}");
  } catch {
    return {};
  }
})();

const currentTimelineSection = () => {
  const points = [...document.querySelectorAll("[data-timeline-point]")];
  if (!points.length || !libraryPane) return "";
  const paneRect = libraryPane.getBoundingClientRect();
  const anchorY = paneRect.top + Math.min(320, paneRect.height * 0.36);
  let active = points[0];
  for (const point of points) {
    if (point.getBoundingClientRect().top <= anchorY) active = point;
    else break;
  }
  return active.id;
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
  const section = position.sectionId ? document.getElementById(position.sectionId) : null;
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
  if (!consumeReturningFromPlayer()) return;
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

if (previewSize) {
  const savedSize = localStorage.getItem("videorecback-card-size") || previewSize.value;
  previewSize.value = savedSize;
  document.documentElement.style.setProperty("--card-size", `${savedSize}px`);
  previewSize.addEventListener("input", () => {
    document.documentElement.style.setProperty("--card-size", `${previewSize.value}px`);
    localStorage.setItem("videorecback-card-size", previewSize.value);
  });
}

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
  if (card === overlayPlayerCard) setFavoriteButtonState(overlayFavorite, favorite);
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
bindFavoriteControl(overlayFavorite, () => overlayPlayerCard);

const playerUrlForCard = (card) => {
  const url = new URL(card.href, window.location.origin);
  url.searchParams.set("embed", "1");
  return url.toString();
};

const titleForCard = (card) => card.getAttribute("aria-label")?.replace(/^播放\s*/, "") || "";

const openInlinePlayer = (card, panePosition) => {
  if (!shell || !frame) return;
  if (inlineFrameClearTimer) window.clearTimeout(inlineFrameClearTimer);
  inlinePlayerCard = card;
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
};

const closeInlinePlayer = () => {
  const panePosition = capturePanePosition();
  shell?.classList.remove("player-open");
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

const openOverlayPlayer = (card, panePosition) => {
  if (!playerModal || !overlayFrame) return;
  overlayPlayerCard = card;
  playerModal.hidden = false;
  document.body.classList.add("player-modal-open");
  restorePanePosition(panePosition);
  overlayFrame.src = playerUrlForCard(card);
  if (overlayPlayerTitle) overlayPlayerTitle.textContent = titleForCard(card);
  if (overlaySettings) {
    overlaySettings.href = card.dataset.settingsUrl || "#";
    overlaySettings.hidden = !card.dataset.settingsUrl;
  }
  configureFavoriteButton(overlayFavorite, card);
  closeOverlayPlayer?.focus({ preventScroll: true });
};

const closeOverlay = () => {
  if (!playerModal) return;
  const panePosition = capturePanePosition();
  playerModal.hidden = true;
  document.body.classList.remove("player-modal-open");
  restorePanePosition(panePosition);
  if (overlayFrame) {
    window.setTimeout(() => {
      overlayFrame.src = "about:blank";
    }, 180);
  }
  overlayPlayerCard = null;
  if (overlayPlayerTitle) overlayPlayerTitle.textContent = "";
  if (overlaySettings) overlaySettings.hidden = true;
  if (overlayFavorite) overlayFavorite.hidden = true;
};

for (const card of document.querySelectorAll("[data-settings-url]")) {
  let timer = null;
  let longPressed = false;
  const usesFavoriteMenu = card.dataset.favoriteMenu === "1";
  const openSettings = (event) => {
    event.preventDefault();
    window.location.href = card.dataset.settingsUrl;
  };
  const openContextAction = (event) => {
    event.preventDefault();
    if (usesFavoriteMenu) {
      openFavoriteContextMenu(card, event);
      return;
    }
    openSettings(event);
  };

  card.addEventListener("contextmenu", openContextAction);
  card.addEventListener("pointerdown", () => {
    pendingReturnPosition = capturePanePosition();
  }, { passive: true });
  card.addEventListener("click", (event) => {
    if (longPressed) {
      event.preventDefault();
      longPressed = false;
      return;
    }
    const panePosition = pendingReturnPosition || capturePanePosition();
    pendingReturnPosition = null;
    saveReturnState(panePosition);
    event.preventDefault();
    if (window.matchMedia(DESKTOP_QUERY).matches && shell && frame) {
      openInlinePlayer(card, panePosition);
    } else {
      openOverlayPlayer(card, panePosition);
    }
  });
  card.addEventListener("touchstart", (event) => {
    longPressed = false;
    const point = eventPoint(event, card);
    timer = window.setTimeout(() => {
      longPressed = true;
      if (usesFavoriteMenu) {
        openFavoriteContextMenu(card, point);
      } else {
        window.location.href = card.dataset.settingsUrl;
      }
    }, LONG_PRESS_MS);
  }, { passive: true });
  for (const eventName of ["touchend", "touchmove", "touchcancel"]) {
    card.addEventListener(eventName, () => {
      if (timer) window.clearTimeout(timer);
      timer = null;
    }, { passive: true });
  }
}

closePlayer?.addEventListener("click", closeInlinePlayer);

inlineSettings?.addEventListener("click", (event) => {
  const href = inlineSettings.getAttribute("href");
  if (!href || href === "#") return;
  event.preventDefault();
  window.location.href = href;
});

overlaySettings?.addEventListener("click", (event) => {
  const href = overlaySettings.getAttribute("href");
  if (!href || href === "#") return;
  event.preventDefault();
  window.location.href = href;
});

closeOverlayPlayer?.addEventListener("click", closeOverlay);

playerModal?.addEventListener("wheel", (event) => {
  if (playerModal.hidden) return;
  event.preventDefault();
}, { passive: false });

playerModal?.addEventListener("touchmove", (event) => {
  if (playerModal.hidden) return;
  event.preventDefault();
}, { passive: false });

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (playerModal && !playerModal.hidden) {
    event.preventDefault();
    closeOverlay();
  }
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

if (timelineRail && libraryPane) {
  const marks = [...timelineRail.querySelectorAll(".timeline-jump-mark")];
  const sections = [...document.querySelectorAll("[data-timeline-section]")];
  const points = [...document.querySelectorAll("[data-timeline-point]")];
  const pointById = new Map(points.map((point) => [point.id, point]));
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
  const activeSection = () => {
    const paneRect = libraryPane.getBoundingClientRect();
    const anchorY = paneRect.top + Math.min(320, paneRect.height * 0.36);
    let active = points[0];
    for (const point of points) {
      if (point.getBoundingClientRect().top <= anchorY) active = point;
      else break;
    }
    return active;
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
    jumpToSavedOffset(Number(restoredReturnState.scrollTop || 0));
    timelineJumpTimer = window.setTimeout(() => {
      timelineJumping = false;
      timelineRoot?.classList.remove("is-jumping");
      updateTimelineCurrent();
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
    return marks.find((mark) => !mark.hidden && mark.dataset.period === dateKey) ||
      marks.find((mark) => !mark.hidden && mark.dataset.period === monthKey) ||
      marks.find((mark) => !mark.hidden && mark.dataset.period === yearKey);
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
    const meta = sectionMeta(currentSection);
    applyTimelineScale(currentSection);
    const mark = currentMarkForSection(currentSection);
    timelineRoot?.setAttribute("data-current-page", currentSection.id);
    for (const section of sections) {
      const isCurrentMonth = String(section.dataset.year) === String(meta.year) && two(section.dataset.month) === two(meta.month);
      section.classList.toggle("is-current-section", isCurrentMonth);
    }
    for (const point of points) {
      point.classList.toggle("is-current-point", point === currentSection);
    }
    for (const candidate of marks) {
      candidate.classList.toggle("is-current", candidate === mark);
    }
    keepMarkVisible(mark);
  };
  const requestTimelineUpdate = () => {
    if (timelineJumping || timelineFrame) return;
    timelineFrame = window.requestAnimationFrame(() => {
      timelineFrame = 0;
      updateTimelineCurrent();
    });
  };

  for (const mark of marks) {
    mark.addEventListener("click", (event) => {
      const href = mark.getAttribute("href") || "";
      if (!href.startsWith("#timeline-")) return;
      const section = sectionForMark(mark);
      if (!section) return;
      event.preventDefault();
      jumpToSection(section, mark.dataset.targetAnchor || href);
    });
  }

  libraryPane.addEventListener("scroll", requestTimelineUpdate, { passive: true });
  window.addEventListener("resize", () => updateTimelineCurrent());
  window.addEventListener("videorecback:timeline-layout", () => updateTimelineCurrent());
  window.addEventListener("hashchange", () => {
    if (restoredReturnState) return;
    const section = sectionForTimelineId(window.location.hash);
    if (section) jumpToSection(section, window.location.hash, true);
  });
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
  } else {
    const initialSection = sectionForTimelineId(window.location.hash);
    if (initialSection) window.requestAnimationFrame(() => jumpToSection(initialSection, window.location.hash, true));
    else updateTimelineCurrent();
  }
}
