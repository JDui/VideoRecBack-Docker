const LONG_PRESS_MS = 520;
const DESKTOP_QUERY = "(min-width: 980px) and (orientation: landscape)";
const shell = document.querySelector(".app-shell");
const frame = document.querySelector("[data-player-frame]");
const closePlayer = document.querySelector("[data-close-player]");
const resizer = document.querySelector("[data-resizer]");
const previewSize = document.querySelector("[data-preview-size]");
const libraryPane = document.querySelector(".library-pane");
const timelineRail = document.querySelector("[data-timeline-jump]");
const timelineDateChip = document.querySelector("[data-timeline-date-chip]");
const inlinePlayerTitle = document.querySelector("[data-inline-player-title]");
const inlineSettings = document.querySelector("[data-inline-settings]");
const scanForm = document.querySelector("[data-scan-form]");
const scanButton = document.querySelector("[data-scan-button]");
const scanLabel = document.querySelector("[data-scan-label]");
const RETURN_STATE_KEY = "videorecback-return-state";
let inlineFrameClearTimer = null;

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

const saveReturnState = () => {
  const state = {
    url: `${window.location.pathname}${window.location.search}`,
    hash: window.location.hash,
    scrollTop: libraryPane?.scrollTop || 0,
    view: new URLSearchParams(window.location.search).get("view") || "timeline",
    activeSection: currentTimelineSection(),
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
    saveReturnState();
    if (!window.matchMedia(DESKTOP_QUERY).matches || !shell || !frame) return;
    event.preventDefault();
    if (inlineFrameClearTimer) window.clearTimeout(inlineFrameClearTimer);
    shell.classList.add("player-open");
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
    window.requestAnimationFrame(() => window.dispatchEvent(new CustomEvent("videorecback:timeline-layout")));
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
  const previousTop = libraryPane?.scrollTop || 0;
  shell?.classList.remove("player-open");
  if (libraryPane) libraryPane.scrollTop = previousTop;
  if (frame) {
    inlineFrameClearTimer = window.setTimeout(() => {
      frame.src = "about:blank";
      inlineFrameClearTimer = null;
    }, 180);
  }
  if (inlinePlayerTitle) inlinePlayerTitle.textContent = "";
  if (inlineSettings) inlineSettings.hidden = true;
  window.requestAnimationFrame(() => {
    if (libraryPane) libraryPane.scrollTop = previousTop;
    window.dispatchEvent(new CustomEvent("videorecback:timeline-layout"));
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
  const sectionById = new Map(sections.map((section) => [section.id, section]));
  const sectionForTimelineId = (id) => {
    if (sectionById.has(id)) return sectionById.get(id);
    const yearMatch = id.match(/^timeline-(\d{4})$/);
    if (yearMatch) return sections.find((section) => section.dataset.year === yearMatch[1]);
    const monthMatch = id.match(/^timeline-(\d{4})-(\d{2})$/);
    if (monthMatch) return sections.find((section) => section.dataset.year === monthMatch[1] && String(section.dataset.month).padStart(2, "0") === monthMatch[2]);
    const dayMatch = id.match(/^timeline-(\d{4})-(\d{2})-(\d{2})$/);
    if (dayMatch) return sections.find((section) => section.dataset.year === dayMatch[1] && String(section.dataset.month).padStart(2, "0") === dayMatch[2] && String(section.dataset.day).padStart(2, "0") === dayMatch[3]);
    return null;
  };
  const sectionForMark = (mark) => {
    const targetAnchor = mark.dataset.targetAnchor || "";
    if (targetAnchor.startsWith("#")) {
      const targetSection = sectionForTimelineId(targetAnchor.slice(1));
      if (targetSection) return targetSection;
    }
    const href = mark.getAttribute("href") || "";
    const id = href.startsWith("#") ? href.slice(1) : "";
    return sectionForTimelineId(id);
  };
  const scrollToTimelineSection = (section) => {
    const paneRect = libraryPane.getBoundingClientRect();
    const sectionRect = section.getBoundingClientRect();
    libraryPane.scrollTo({
      top: libraryPane.scrollTop + sectionRect.top - paneRect.top - 12,
      behavior: "auto",
    });
    window.requestAnimationFrame(updateTimelineCurrent);
  };

  const dayIndex = (year, month, day) => Date.UTC(Number(year), Number(month) - 1, Number(day)) / 86400000;
  const activeSection = () => {
    const paneRect = libraryPane.getBoundingClientRect();
    const anchorY = paneRect.top + Math.min(160, paneRect.height * 0.28);
    let active = sections[0];
    for (const section of sections) {
      if (section.getBoundingClientRect().top <= anchorY) active = section;
      else break;
    }
    return active;
  };
  const sortedMarks = (kind) => marks.filter((mark) => mark.dataset.kind === kind);
  const applyTimelineScale = (section) => {
    if (!section) return;
    const capacity = Math.max(10, Math.floor((timelineRail.clientHeight - 36) / 24));
    const activeDay = dayIndex(section.dataset.year, section.dataset.month, section.dataset.day);
    const selected = new Set();
    const addRanked = (items, limit) => {
      for (const mark of items) {
        if (selected.size >= limit) break;
        selected.add(mark);
      }
    };
    const dayMarks = sortedMarks("day")
      .sort((left, right) => Math.abs(dayIndex(left.dataset.year, left.dataset.month, left.dataset.day) - activeDay) - Math.abs(dayIndex(right.dataset.year, right.dataset.month, right.dataset.day) - activeDay));
    addRanked(dayMarks, Math.max(3, Math.floor(capacity * 0.42)));

    const monthMarks = sortedMarks("month")
      .sort((left, right) => Math.abs(dayIndex(left.dataset.year, left.dataset.month, 1) - activeDay) - Math.abs(dayIndex(right.dataset.year, right.dataset.month, 1) - activeDay));
    addRanked(monthMarks, Math.max(selected.size, Math.floor(capacity * 0.78)));

    const yearMarks = sortedMarks("year")
      .sort((left, right) => Math.abs(Number(left.dataset.year) - Number(section.dataset.year)) - Math.abs(Number(right.dataset.year) - Number(section.dataset.year)));
    addRanked(yearMarks, capacity);

    for (const mark of marks) {
      const visible = selected.has(mark);
      mark.hidden = !visible;
      mark.classList.toggle("is-near", visible && mark.dataset.kind === "day");
    }
    for (const group of timelineRail.querySelectorAll(".timeline-jump-year")) {
      group.hidden = !group.querySelector(".timeline-jump-mark:not([hidden])");
    }
  };

  for (const mark of marks) {
    mark.addEventListener("click", (event) => {
      const href = mark.getAttribute("href") || "";
      if (!href.startsWith("#timeline-")) return;
      const section = sectionForMark(mark);
      if (!section) return;
      event.preventDefault();
      scrollToTimelineSection(section);
      window.history.pushState(null, "", mark.dataset.targetAnchor || href);
    });
  }

  const updateTimelineCurrent = () => {
    if (!sections.length) return;
    const currentSection = activeSection();
    applyTimelineScale(currentSection);
    const currentDateKey = `${currentSection.dataset.year}-${String(currentSection.dataset.month).padStart(2, "0")}-${String(currentSection.dataset.day).padStart(2, "0")}`;
    const mark = marks.find((candidate) => !candidate.hidden && candidate.dataset.period === currentDateKey) ||
      marks.find((candidate) => !candidate.hidden && candidate.dataset.kind === "month" && candidate.dataset.year === currentSection.dataset.year && candidate.dataset.month === currentSection.dataset.month) ||
      marks.find((candidate) => !candidate.hidden && candidate.dataset.kind === "year" && candidate.dataset.year === currentSection.dataset.year);
    for (const candidate of marks) {
      candidate.classList.toggle("is-current", candidate === mark);
    }
    if (timelineDateChip) {
      timelineDateChip.textContent = currentSection.dataset.label || currentSection.querySelector("h2")?.textContent || "";
      timelineDateChip.hidden = false;
    }
    mark?.scrollIntoView({ block: "nearest" });
  };

  libraryPane.addEventListener("scroll", updateTimelineCurrent, { passive: true });
  window.addEventListener("resize", updateTimelineCurrent);
  window.addEventListener("videorecback:timeline-layout", updateTimelineCurrent);
  const initialSection = sectionForTimelineId(window.location.hash.slice(1));
  if (initialSection) {
    window.requestAnimationFrame(() => scrollToTimelineSection(initialSection));
  }
  updateTimelineCurrent();
}
