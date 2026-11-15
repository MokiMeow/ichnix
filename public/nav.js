// Keep the ordinary navigation links usable when popovers are unavailable.
const nav = document.querySelector(".topnav");
const trigger = document.querySelector(".menu-toggle");
if (nav && trigger && "showPopover" in HTMLElement.prototype) {
  const compact = matchMedia("(max-width: 45rem)");
  function adapt() {
    if (compact.matches) nav.setAttribute("popover", "auto");
    else nav.removeAttribute("popover");
    trigger.hidden = !compact.matches;
  }
  adapt();
  compact.addEventListener("change", adapt);
  nav.addEventListener("click", (event) => {
    if (nav.matches(":popover-open") && event.target.closest("a"))
      nav.hidePopover();
  });
  document.addEventListener("focusin", (event) => {
    if (
      nav.matches(":popover-open") &&
      event.target !== trigger &&
      !nav.contains(event.target)
    )
      nav.hidePopover();
  });
  document.addEventListener("keydown", (event) => {
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
        nav.hidePopover();
        return;
      }
      nav.showPopover();
    }
    nav.querySelector("a").focus();
  });
}
