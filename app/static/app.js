const LONG_PRESS_MS = 520;
const DESKTOP_QUERY = "(min-width: 980px) and (orientation: landscape)";
const shell = document.querySelector(".app-shell");
const frame = document.querySelector("[data-player-frame]");
const closePlayer = document.querySelector("[data-close-player]");
const resizer = document.querySelector("[data-resizer]");
const previewSize = document.querySelector("[data-preview-size]");
const timelineLabelForm = document.querySelector("[data-timeline-label-form]");
const libraryPane = document.querySelector(".library-pane");
const timelineRail = document.querySelector(".timeline-rail");
const timelineCurrent = document.querySelector("[data-timeline-current]");
const inlinePlayerTitle = document.querySelector("[data-inline-player-title]");
const inlineSettings = document.querySelector("[data-inline-settings]");
const scanForm = document.querySelector("[data-scan-form]");
const scanButton = document.querySelector("[data-scan-button]");
const scanLabel = document.querySelector("[data-scan-label]");

scanForm?.addEventListener("submit", () => {
  scanButton?.classList.add("is-scanning");
  if (scanLabel) scanLabel.textContent = "扫描中";
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
    if (!window.matchMedia(DESKTOP_QUERY).matches || !shell || !frame) return;
    event.preventDefault();
    shell.classList.add("player-open");
    frame.src = `${card.href}?embed=1`;
    if (inlinePlayerTitle) {
      inlinePlayerTitle.textContent = card.getAttribute("aria-label")?.replace(/^播放\s*/, "") || "";
    }
    if (inlineSettings) {
      inlineSettings.href = card.dataset.settingsUrl || "#";
      inlineSettings.hidden = !card.dataset.settingsUrl;
    }
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
  shell?.classList.remove("player-open");
  if (frame) frame.src = "about:blank";
  if (inlinePlayerTitle) inlinePlayerTitle.textContent = "";
  if (inlineSettings) inlineSettings.hidden = true;
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
  });
  for (const name of ["pointerup", "pointercancel"]) {
    resizer.addEventListener(name, () => {
      resizing = false;
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

if (timelineRail && timelineCurrent && libraryPane) {
  const marks = [...timelineRail.querySelectorAll(".timeline-mark")];
  const markById = new Map(
    marks
      .map((mark) => {
        const href = mark.getAttribute("href") || "";
        return href.startsWith("#timeline-") ? [href.slice(1), mark] : null;
      })
      .filter(Boolean)
  );
  const sections = [...document.querySelectorAll(".asset-section[id^='timeline-']")];
  const sectionById = new Map(sections.map((section) => [section.id, section]));
  const sectionForMark = (mark) => {
    const href = mark.getAttribute("href") || "";
    const id = href.startsWith("#") ? href.slice(1) : "";
    if (sectionById.has(id)) return sectionById.get(id);
    if (id.length === "timeline-2026-01".length) {
      return sections.find((section) => section.id.startsWith(id));
    }
    if (id.includes("-q")) {
      return sections.find((section) => (
        section.dataset.year === mark.dataset.year &&
        section.dataset.quarter === mark.dataset.quarter
      ));
    }
    if (id.includes("-h")) {
      const half = id.endsWith("-h2") ? "2" : "1";
      return sections.find((section) => {
        const month = Number(section.dataset.month || 0);
        return section.dataset.year === mark.dataset.year && (half === "2" ? month >= 7 : month <= 6);
      });
    }
    return null;
  };
  const markForSection = (section) => {
    const exact = markById.get(section.id);
    if (exact) return exact;
    const monthMark = markById.get(section.id.slice(0, "timeline-2026-01".length));
    if (monthMark) return monthMark;
    const quarterMark = marks.find((mark) => (
      mark.dataset.year === section.dataset.year &&
      mark.dataset.quarter === section.dataset.quarter &&
      (mark.getAttribute("href") || "").includes("-q")
    ));
    if (quarterMark) return quarterMark;
    return marks.find((mark) => {
      const href = mark.getAttribute("href") || "";
      const month = Number(section.dataset.month || 0);
      return mark.dataset.year === section.dataset.year &&
        ((href.endsWith("-h1") && month <= 6) || (href.endsWith("-h2") && month >= 7));
    });
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

  for (const mark of marks) {
    mark.addEventListener("click", (event) => {
      if (mark.classList.contains("timeline-mark--empty")) {
        event.preventDefault();
        return;
      }
      const href = mark.getAttribute("href") || "";
      if (!href.startsWith("#timeline-")) return;
      const section = sectionForMark(mark);
      if (!section) return;
      event.preventDefault();
      scrollToTimelineSection(section);
      window.history.pushState(null, "", href);
    });
  }

  const updateTimelineCurrent = () => {
    if (!sections.length) return;
    const paneRect = libraryPane.getBoundingClientRect();
    const anchorY = paneRect.top + Math.min(160, paneRect.height * 0.28);
    let activeSection = sections[0];

    for (const section of sections) {
      if (section.getBoundingClientRect().top <= anchorY) {
        activeSection = section;
      } else {
        break;
      }
    }

    const mark = markForSection(activeSection);
    if (!mark) return;
    for (const candidate of marks) {
      candidate.classList.toggle("is-current", candidate === mark);
    }
    const railRect = timelineRail.getBoundingClientRect();
    const markRect = mark.getBoundingClientRect();
    timelineRail.style.setProperty(
      "--timeline-current-top",
      `${markRect.top - railRect.top + markRect.height / 2}px`
    );
    timelineCurrent.classList.add("is-visible");
  };

  libraryPane.addEventListener("scroll", updateTimelineCurrent, { passive: true });
  window.addEventListener("resize", updateTimelineCurrent);
  const initialMark = marks.find((mark) => mark.getAttribute("href") === window.location.hash);
  const initialSection = sectionById.get(window.location.hash.slice(1)) || (initialMark ? sectionForMark(initialMark) : null);
  if (initialSection) {
    window.requestAnimationFrame(() => scrollToTimelineSection(initialSection));
  }
  updateTimelineCurrent();
}

if (timelineLabelForm) {
  const yearInput = timelineLabelForm.querySelector("[data-label-year]");
  const quarterInput = timelineLabelForm.querySelector("[data-label-quarter]");
  const textInput = timelineLabelForm.querySelector("[data-label-text]");
  const colorInput = timelineLabelForm.querySelector("[data-label-color]");
  const titleText = timelineLabelForm.querySelector("[data-label-form-title]");
  const saveButton = timelineLabelForm.querySelector("[data-label-save]");
  const deleteButton = timelineLabelForm.querySelector("[data-label-delete]");
  let editingLabelId = "";
  const hideLabelForm = () => {
    timelineLabelForm.hidden = true;
  };
  const placeLabelForm = (clientX, clientY) => {
    const left = Math.min(clientX, window.innerWidth - 260);
    const top = Math.min(clientY, window.innerHeight - 190);
    timelineLabelForm.style.left = `${left}px`;
    timelineLabelForm.style.top = `${top}px`;
  };
  const showCreateLabelForm = (mark, clientX, clientY) => {
    editingLabelId = "";
    timelineLabelForm.action = "/timeline-labels";
    if (titleText) titleText.textContent = "时间标签";
    if (saveButton) saveButton.textContent = "添加";
    if (deleteButton) deleteButton.hidden = true;
    placeLabelForm(clientX, clientY);
    window.setTimeout(() => {
      yearInput.value = mark.dataset.year || "";
      quarterInput.value = mark.dataset.quarter || "";
      textInput.value = "";
      if (colorInput) colorInput.value = "#16a394";
      timelineLabelForm.hidden = false;
      textInput.focus();
    }, 0);
  };
  const showEditLabelForm = (tag, clientX, clientY) => {
    editingLabelId = tag.dataset.labelId || "";
    if (!editingLabelId) return;
    timelineLabelForm.action = `/timeline-labels/${editingLabelId}`;
    if (titleText) titleText.textContent = "编辑标签";
    if (saveButton) saveButton.textContent = "保存";
    if (deleteButton) deleteButton.hidden = false;
    placeLabelForm(clientX, clientY);
    window.setTimeout(() => {
      yearInput.value = tag.dataset.labelYear || "";
      quarterInput.value = tag.dataset.labelQuarter || "";
      textInput.value = tag.dataset.labelText || tag.dataset.label || "";
      if (colorInput) colorInput.value = tag.dataset.labelColor || "#16a394";
      timelineLabelForm.hidden = false;
      textInput.focus();
      textInput.select();
    }, 0);
  };

  for (const mark of document.querySelectorAll(".timeline-mark")) {
    mark.addEventListener("pointerdown", (event) => {
      if (event.button !== 2) return;
      event.preventDefault();
      event.stopPropagation();
      showCreateLabelForm(mark, event.clientX, event.clientY);
    });
    mark.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      event.stopPropagation();
      showCreateLabelForm(mark, event.clientX, event.clientY);
    });
  }

  for (const tag of document.querySelectorAll(".timeline-tag")) {
    tag.addEventListener("pointerdown", (event) => {
      if (event.button !== 2) return;
      event.preventDefault();
      event.stopPropagation();
      showEditLabelForm(tag, event.clientX, event.clientY);
    });
    tag.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      event.stopPropagation();
      showEditLabelForm(tag, event.clientX, event.clientY);
    });
  }

  deleteButton?.addEventListener("click", () => {
    if (!editingLabelId) return;
    timelineLabelForm.action = `/timeline-labels/${editingLabelId}/delete`;
    timelineLabelForm.submit();
  });

  document.addEventListener("click", (event) => {
    if (timelineLabelForm.hidden || timelineLabelForm.contains(event.target)) return;
    hideLabelForm();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideLabelForm();
  });
}
