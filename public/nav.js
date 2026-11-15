// Keep the ordinary navigation links usable when popovers are unavailable.
const nav = document.querySelector(".topnav");
const trigger = document.querySelector(".menu-toggle");
if (nav && trigger && "showPopover" in HTMLElement.prototype) {
  const compact = matchMedia("(max-width: 45rem)");
  let exit;
  function adapt() {
    if (compact.matches) nav.setAttribute("popover", "manual");
    else {
      nav.removeAttribute("popover");
      nav.classList.remove("closing");
      exit = null;
    }
    trigger.hidden = !compact.matches;
  }
  adapt();
  compact.addEventListener("change", adapt);
  async function closeMenu() {
    if (!nav.matches(":popover-open") || exit) return;
    nav.classList.add("closing");
    // Keep the sheet in the top layer until its exit transition finishes.
    const finished = Promise.allSettled(
      nav.getAnimations().map((animation) => animation.finished),
    );
    exit = finished;
    await finished;
    if (exit !== finished) return;
    exit = null;
    if (nav.matches(":popover-open")) nav.hidePopover();
    nav.classList.remove("closing");
  }
  let openedAt;
  function onScroll() {
    if (Math.abs(window.scrollY - openedAt) > 4) closeMenu();
  }
  nav.addEventListener("beforetoggle", (event) => {
    if (event.newState === "open") {
      openedAt = window.scrollY;
      window.addEventListener("scroll", onScroll, { passive: true });
    } else window.removeEventListener("scroll", onScroll);
  });
  document.addEventListener("click", (event) => {
    if (!nav.matches(":popover-open")) return;
    if (event.target.closest(".menu-toggle, .menu-close")) {
      event.preventDefault();
      if (exit && trigger.contains(event.target)) {
        exit = null;
        nav.classList.remove("closing");
      } else closeMenu();
    } else if (event.target.closest(".topnav a")) {
      closeMenu();
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (!nav.contains(event.target) && !trigger.contains(event.target))
      closeMenu();
  });
  document.addEventListener("focusin", (event) => {
    if (
      nav.matches(":popover-open") &&
      event.target !== trigger &&
      !nav.contains(event.target)
    )
      closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && nav.matches(":popover-open")) {
      event.preventDefault();
      trigger.focus({ preventScroll: true });
      closeMenu();
      return;
    }
    if (
      event.defaultPrevented ||
      event.isComposing ||
      event.repeat ||
      !event.altKey ||
      !event.shiftKey ||
      event.ctrlKey ||
      event.metaKey ||
      event.code !== "KeyM" ||
      event.target.closest("input, textarea, select, [contenteditable]")
    )
      return;
    event.preventDefault();
    if (compact.matches) {
      trigger.focus();
      if (nav.matches(":popover-open")) {
        closeMenu();
        return;
      }
      nav.showPopover();
    }
    nav.querySelector("a").focus();
  });
}

// Warm only the public guide once a visitor shows navigation intent.
if (document.querySelector(".home") && !navigator.connection?.saveData) {
  const events = new AbortController();
  function warmGuide() {
    events.abort();
    for (const href of ["/docs", "/docs.css", "/manual.js"]) {
      const link = document.createElement("link");
      link.rel = "prefetch";
      link.href = href;
      document.head.append(link);
    }
  }
  function considerLink(event) {
    if (event.target.closest('a[href="/docs"], a[href^="/docs#"]')) warmGuide();
  }
  document.addEventListener("pointerover", considerLink, {
    signal: events.signal,
  });
  document.addEventListener("focusin", considerLink, { signal: events.signal });
  nav?.addEventListener(
    "toggle",
    (event) => {
      if (event.newState === "open") warmGuide();
    },
    { signal: events.signal },
  );
}
