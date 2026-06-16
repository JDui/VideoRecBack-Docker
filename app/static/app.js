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
const timelineDateChip = document.querySelector("[data-timeline-date-chip]");
const inlinePlayerTitle = document.querySelector("[data-inline-player-title]");
const inlineSettings = document.querySelector("[data-inline-settings]");
const scanForm = document.querySelector("[data-scan-form]");
const scanButton = document.querySelector("[data-scan-button]");
const scanLabel = document.querySelector("[data-scan-label]");
const RETURN_STATE_KEY = "videorecback-return-state";
let inlineFrameClearTimer = null;
let restoredReturnState = null;

const timelineCache = (() => {
  try {
    return JSON.parse(timelineRoot?.dataset.timelineCache || "{}");
  } catch {
    return {};
  }
})();

const currentTimelineSection = () => {
  const sections = [...document.querySelectorAll("[data-timeline-section]")];
  if (!sections.length || !libraryPane) return "";
  const paneRect = libraryPane.getBoundingClientRect();
  const anchorY = paneRect.top + Math.min(160, paneRect.height * 0.28);
  let active = sections[0];
  for (const section of sections) {
    if (section.getBoundingClientRect().top <= anchorY) active = section;
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

const saveReturnState = () => {
  const position = capturePanePosition();
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

const restoreReturnState = () => {
  const state = readReturnState();
  if (!state || !libraryPane) return;
  const expectedUrl = `${window.location.pathname}${window.location.search}`;
  if (state.url && state.url !== expectedUrl) return;
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

for (const card of document.querySelectorAll("[data-settings-url]")) {
  let timer = null;
  let longPressed = false;
  const openSettings = (event) => {
    event.preventDefault();
    window.location.href = card.dataset.settingsUrl;
  };

  card.addEventListener("contextmenu", openSettings);
  card.addEventListener("click", (event) => {
    if (longPressed) {
      event.preventDefault();
      longPressed = false;
      return;
    }
    const panePosition = capturePanePosition();
    saveReturnState();
    if (!window.matchMedia(DESKTOP_QUERY).matches || !shell || !frame) return;
    event.preventDefault();
    if (inlineFrameClearTimer) window.clearTimeout(inlineFrameClearTimer);
    shell.classList.add("player-open");
    restorePanePosition(panePosition);
    const url = new URL(card.href, window.location.origin);
    url.searchParams.set("embed", "1");
    frame.src = url.toString();
    if (inlinePlayerTitle) {
      inlinePlayerTitle.textContent = card.getAttribute("aria-label")?.replace(/^播放\s*/, "") || "";
    }
    if (inlineSettings) {
      inlineSettings.href = card.dataset.settingsUrl || "#";
      inlineSettings.hidden = !card.dataset.settingsUrl;
    }
    window.requestAnimationFrame(() => restorePanePosition(panePosition));
  });
  card.addEventListener("touchstart", () => {
    longPressed = false;
    timer = window.setTimeout(() => {
      longPressed = true;
      window.location.href = card.dataset.settingsUrl;
    }, LONG_PRESS_MS);
  }, { passive: true });
  for (const eventName of ["touchend", "touchmove", "touchcancel"]) {
    card.addEventListener(eventName, () => {
      if (timer) window.clearTimeout(timer);
      timer = null;
    }, { passive: true });
  }
}

closePlayer?.addEventListener("click", () => {
  const panePosition = capturePanePosition();
  shell?.classList.remove("player-open");
  restorePanePosition(panePosition);
  if (frame) {
    inlineFrameClearTimer = window.setTimeout(() => {
      frame.src = "about:blank";
      inlineFrameClearTimer = null;
    }, 180);
  }
  if (inlinePlayerTitle) inlinePlayerTitle.textContent = "";
  if (inlineSettings) inlineSettings.hidden = true;
  window.requestAnimationFrame(() => {
    restorePanePosition(panePosition);
  });
});

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
    const width = Math.max(360, Math.min(rect.width * 0.62, event.clientX - rect.left));
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
  const sectionById = new Map(sections.map((section) => [section.id, section]));
  const groupMetaByAnchor = new Map((Array.isArray(timelineCache.groups) ? timelineCache.groups : []).map((group) => [group.anchor, group]));
  let timelineJumping = false;
  let timelineJumpTimer = null;
  let timelineFrame = 0;

  for (const section of sections) {
    section.tabIndex = -1;
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
  const matchingSection = (predicate) => sections.find((section) => predicate(sectionMeta(section)));
  const sectionForTimelineId = (rawId) => {
    const id = String(rawId || "").replace(/^#/, "");
    if (!id) return null;
    if (sectionById.has(id)) return sectionById.get(id);
    const dayMatch = id.match(/^timeline-(\d{4})-(\d{2})-(\d{2})$/);
    if (dayMatch) {
      const [, year, month, day] = dayMatch;
      return matchingSection((meta) => String(meta.year) === year && two(meta.month) === month && two(meta.day) === day) ||
        matchingSection((meta) => String(meta.year) === year && two(meta.month) === month) ||
        matchingSection((meta) => String(meta.year) === year);
    }
    const monthMatch = id.match(/^timeline-(\d{4})-(\d{2})$/);
    if (monthMatch) {
      const [, year, month] = monthMatch;
      return matchingSection((meta) => String(meta.year) === year && two(meta.month) === month) ||
        matchingSection((meta) => String(meta.year) === year);
    }
    const yearMatch = id.match(/^timeline-(\d{4})$/);
    if (yearMatch) return matchingSection((meta) => String(meta.year) === yearMatch[1]);
    return null;
  };
  const sectionForMark = (mark) => {
    const targetSection = sectionForTimelineId(mark.dataset.targetAnchor);
    if (targetSection) return targetSection;
    return sectionForTimelineId(mark.getAttribute("href") || "");
  };
  const topForSection = (section) => {
    const paneRect = libraryPane.getBoundingClientRect();
    const sectionRect = section.getBoundingClientRect();
    return Math.max(0, libraryPane.scrollTop + sectionRect.top - paneRect.top - 12);
  };
  const activeSection = () => {
    const paneRect = libraryPane.getBoundingClientRect();
    const anchorY = paneRect.top + Math.min(120, paneRect.height * 0.24);
    let active = sections[0];
    for (const section of sections) {
      if (section.getBoundingClientRect().top <= anchorY) active = section;
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
    libraryPane.scrollTop = topForSection(section);
    section.dataset.pageLoaded = "1";
    if (hash) {
      const method = replace ? "replaceState" : "pushState";
      window.history[method](null, "", hash);
    }
    window.requestAnimationFrame(() => {
      updateTimelineCurrent(section);
      timelineJumpTimer = window.setTimeout(() => {
        timelineJumping = false;
        timelineRoot?.classList.remove("is-jumping");
      }, 80);
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
    const position = {
      scrollTop: Number(restoredReturnState.scrollTop || 0),
      sectionId: restoredReturnState.activeSection || "",
      sectionOffset: Number(restoredReturnState.sectionOffset || 0),
    };
    if (position.sectionId) restorePanePosition(position);
    else jumpToSavedOffset(position.scrollTop);
    timelineJumpTimer = window.setTimeout(() => {
      timelineJumping = false;
      timelineRoot?.classList.remove("is-jumping");
      updateTimelineCurrent();
    }, 100);
  };
  const visibleMark = (mark, meta) => {
    if (mark.dataset.kind === "year") return true;
    if (mark.dataset.kind === "month") {
      return Math.abs(monthIndex(mark.dataset.year, mark.dataset.month) - monthIndex(meta.year, meta.month)) <= 2;
    }
    if (mark.dataset.kind === "day") {
      return Math.abs(dayIndex(mark.dataset.year, mark.dataset.month, mark.dataset.day) - dayIndex(meta.year, meta.month, meta.day)) <= 3;
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
  const updateTimelineCurrent = (forcedSection = null) => {
    if (!sections.length) return;
    const currentSection = forcedSection || activeSection();
    const meta = sectionMeta(currentSection);
    applyTimelineScale(currentSection);
    const mark = currentMarkForSection(currentSection);
    timelineRoot?.setAttribute("data-current-page", currentSection.id);
    for (const section of sections) {
      section.classList.toggle("is-current-section", section === currentSection);
    }
    for (const candidate of marks) {
      candidate.classList.toggle("is-current", candidate === mark);
    }
    if (timelineDateChip) {
      timelineDateChip.textContent = meta.label || currentSection.querySelector("h2")?.textContent || "";
      timelineDateChip.hidden = false;
    }
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
    window.setTimeout(() => {
      restoredReturnState = null;
    }, 260);
  } else {
    const initialSection = sectionForTimelineId(window.location.hash);
    if (initialSection) window.requestAnimationFrame(() => jumpToSection(initialSection, window.location.hash, true));
    else updateTimelineCurrent();
  }
}
