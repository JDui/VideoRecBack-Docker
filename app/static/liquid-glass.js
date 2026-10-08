(() => {
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const animations = new Set();
  const tracks = [];
  const releases = [];

  const animate = (element, frames, duration) => {
    if (motion.matches) return null;
    const animation = element.animate(frames, { duration, easing: "cubic-bezier(.2, .8, .2, 1)" });
    animations.add(animation);
    animation.finished.then(() => animations.delete(animation), () => animations.delete(animation));
    return animation;
  };
  const register = element => element.classList.add("glass-surface");

  for (const container of document.querySelectorAll(".site-navigation, .calendar-zoom, .preferences-nav")) {
    const links = [...container.querySelectorAll(":scope > a")].filter(link => !link.classList.contains("disabled") && !link.classList.contains("preferences-back"));
    if (!links.length) continue;
    const selection = document.createElement("span");
    selection.className = "glass-selection";
    selection.setAttribute("aria-hidden", "true");
    container.append(selection);
    container.classList.add("glass-track");
    register(container);
    register(selection);
    const selected = () => links.find(link => link.hasAttribute("aria-current") || link.classList.contains("active"));
    const move = (link, immediate = false) => {
      selection.hidden = !link;
      if (!link) return;
      if (immediate) selection.style.transition = "none";
      selection.style.translate = `${link.offsetLeft}px ${link.offsetTop}px`;
      selection.style.width = link.offsetWidth + "px";
      selection.style.height = link.offsetHeight + "px";
      if (immediate) {
        selection.getBoundingClientRect();
        selection.style.removeProperty("transition");
      }
    };
    links.forEach(link => {
      link.addEventListener("pointerenter", () => { if (matchMedia("(hover: hover)").matches) move(link); });
      link.addEventListener("focus", () => move(link));
      link.addEventListener("pointerdown", () => move(link));
    });
    container.addEventListener("pointerleave", () => move(selected()));
    container.addEventListener("focusout", event => { if (!container.contains(event.relatedTarget)) move(selected()); });
    new MutationObserver(() => move(selected())).observe(container, { subtree: true, attributes: true, attributeFilter: ["aria-current", "class"] });
    new ResizeObserver(() => move(selected(), true)).observe(container);
    tracks.push(() => move(selected(), true));
    move(selected(), true);
  }

  const surfaceSelector = ".site-settings, .intranet-jump-button, .home-soft-button, .nav-button, .player-back-button, .timeline-position-bar > output, .timeline-jump-rail, .timeline-scrub-date, .filterbar, .player-controls, .preferences-savebar, .quality-menu-panel, .favorite-context-menu, .home-context-menu, button:not(.pane-resizer):not(.timeline-jump-mark):not(.timeline-favorite):not(.timeline-latest), summary";
  document.querySelectorAll(surfaceSelector).forEach(register);
  document.querySelectorAll("button.glass-surface, a.glass-surface, summary.glass-surface").forEach(element => {
    element.classList.add("glass-interactive");
    const release = () => element.classList.remove("glass-pressed");
    releases.push(release);
    element.addEventListener("pointerdown", event => {
      if (!element.disabled && event.button === 0 && !motion.matches) element.classList.add("glass-pressed");
    });
    element.addEventListener("keydown", event => {
      if (event.key === " " && !element.disabled && !motion.matches) element.classList.add("glass-pressed");
    });
    element.addEventListener("keyup", release);
    element.addEventListener("blur", release);
  });
  const releaseAll = () => releases.forEach(release => release());
  window.addEventListener("pointerup", releaseAll, { passive: true });
  window.addEventListener("pointercancel", releaseAll, { passive: true });
  window.addEventListener("blur", releaseAll);

  document.querySelectorAll(".filter-menu, .quality-menu").forEach(details => {
    const trigger = details.querySelector("summary");
    const panel = details.querySelector(".filterbar, .quality-menu-panel");
    let menuAnimation;
    let closing = false;
    const transition = reverse => {
      const current = getComputedStyle(panel);
      const from = menuAnimation?.playState === "running" ? { opacity: current.opacity, transform: current.transform } : { opacity: reverse ? 1 : 0, transform: reverse ? "none" : "translateY(-4px) scale(.985)" };
      menuAnimation?.cancel();
      return animate(panel, [from, { opacity: reverse ? 0 : 1, transform: reverse ? "translateY(-4px) scale(.985)" : "none" }], reverse ? 140 : 200);
    };
    details.addEventListener("toggle", () => {
      if (!details.open) { menuAnimation?.cancel(); menuAnimation = null; closing = false; return; }
      if (!closing) menuAnimation = transition(false);
    });
    trigger.addEventListener("click", event => {
      if (!details.open || motion.matches) return;
      event.preventDefault();
      if (closing) { closing = false; menuAnimation = transition(false); return; }
      closing = true;
      menuAnimation = transition(true);
      const animation = menuAnimation;
      animation?.finished.then(() => {
        if (closing && menuAnimation === animation) details.open = false;
      }, () => {});
    });
    motion.addEventListener("change", () => {
      if (closing) details.open = false;
      closing = false;
      menuAnimation = null;
    });
  });
  motion.addEventListener("change", () => {
    for (const animation of animations) animation.cancel();
    releaseAll();
    tracks.forEach(reset => reset());
  });
  const updateVisibility = () => document.body.classList.toggle("page-hidden", document.hidden);
  document.addEventListener("visibilitychange", updateVisibility);
  updateVisibility();
  window.addEventListener("pageshow", () => tracks.forEach(reset => reset()));
})();
