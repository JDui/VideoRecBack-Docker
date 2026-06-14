const LONG_PRESS_MS = 520;
const DESKTOP_QUERY = "(min-width: 980px) and (orientation: landscape)";
const shell = document.querySelector(".app-shell");
const frame = document.querySelector("[data-player-frame]");
const closePlayer = document.querySelector("[data-close-player]");
const resizer = document.querySelector("[data-resizer]");
const previewSize = document.querySelector("[data-preview-size]");
const themeToggle = document.querySelector("[data-theme-toggle]");
const timelineLabelForm = document.querySelector("[data-timeline-label-form]");

const setTheme = (theme) => {
  document.documentElement.dataset.theme = theme;
  if (themeToggle) themeToggle.textContent = theme === "dark" ? "🌛" : "☀️";
};

setTheme(localStorage.getItem("videorecback-theme") || "light");
themeToggle?.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  setTheme(next);
  localStorage.setItem("videorecback-theme", next);
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

if (timelineLabelForm) {
  const yearInput = timelineLabelForm.querySelector("[data-label-year]");
  const quarterInput = timelineLabelForm.querySelector("[data-label-quarter]");
  const textInput = timelineLabelForm.querySelector("[data-label-text]");
  const hideLabelForm = () => {
    timelineLabelForm.hidden = true;
  };
  const showLabelForm = (mark, clientX, clientY) => {
    const left = Math.min(clientX, window.innerWidth - 260);
    const top = Math.min(clientY, window.innerHeight - 190);
    window.setTimeout(() => {
      yearInput.value = mark.dataset.year || "";
      quarterInput.value = mark.dataset.quarter || "";
      timelineLabelForm.style.left = `${left}px`;
      timelineLabelForm.style.top = `${top}px`;
      timelineLabelForm.hidden = false;
      textInput.focus();
    }, 0);
  };

  for (const mark of document.querySelectorAll(".timeline-mark")) {
    mark.addEventListener("pointerdown", (event) => {
      if (event.button !== 2) return;
      event.preventDefault();
      event.stopPropagation();
      showLabelForm(mark, event.clientX, event.clientY);
    });
    mark.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      event.stopPropagation();
      showLabelForm(mark, event.clientX, event.clientY);
    });
  }

  document.addEventListener("click", (event) => {
    if (timelineLabelForm.hidden || timelineLabelForm.contains(event.target)) return;
    hideLabelForm();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideLabelForm();
  });
}
