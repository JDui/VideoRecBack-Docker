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

  for (const mark of marks) {
    mark.addEventListener("click", (event) => {
      if (mark.classList.contains("timeline-mark--empty")) {
        event.preventDefault();
      }
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

    const mark = markById.get(activeSection.id);
    if (!mark) return;
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
