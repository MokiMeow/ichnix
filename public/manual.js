const index = document.querySelector(".index-panel");
const nav = document.getElementById("chapter-links");
const links = [...nav.querySelectorAll("a")];
const sections = links.map((link) =>
  document.getElementById(link.hash.slice(1)),
);
const compact = matchMedia("(max-width:45rem)");
const current = document.getElementById("current-chapter");
const query = document.getElementById("chapter-query");
const topics = sections.map((section, i) =>
  [links[i], ...section.querySelectorAll("h2,h3,summary")]
    .map((el) => el.textContent)
    .join(" ")
    .toLowerCase()
    .replace(/\s+/g, " "),
);

const summary = index.querySelector("summary");
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const transitions = new WeakMap();

function setDisclosure(details, open, animate = true) {
  const previous = transitions.get(details);
  if (previous ? previous.open === open && animate : details.open === open)
    return;
  const popup = details === index && compact.matches;
  const surface = popup ? index.querySelector(".index-body") : details;
  const shifted = "translateY(-6px)";
  const from = popup
    ? {
        opacity: details.open ? getComputedStyle(surface).opacity : 0,
        transform: details.open ? getComputedStyle(surface).transform : shifted,
      }
    : { height: details.getBoundingClientRect().height + "px" };
  previous?.animation.cancel();
  transitions.delete(details);
  details.removeAttribute("data-animating");
  details.removeAttribute("data-closing");
  if (!animate || reduceMotion.matches) {
    details.open = open;
    return;
  }

  details.open = open;
  const to = popup
    ? { opacity: open ? 1 : 0, transform: open ? "none" : shifted }
    : { height: details.getBoundingClientRect().height + "px" };
  details.open = true;
  details.setAttribute("data-animating", "");
  details.toggleAttribute("data-closing", !open);
  const animation = surface.animate([from, to], {
    duration: 170,
    easing: "ease-out",
  });
  transitions.set(details, { animation, open });
  animation.onfinish = () => {
    if (transitions.get(details)?.animation !== animation) return;
    details.open = open;
    details.removeAttribute("data-animating");
    details.removeAttribute("data-closing");
    transitions.delete(details);
  };
}

document
  .querySelectorAll(".index-panel > summary, .manual-subsection > summary")
  .forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      const details = trigger.parentElement;
      setDisclosure(details, !(transitions.get(details)?.open ?? details.open));
    });
  });

const bar = document.createElement("input");
bar.type = "range";
bar.className = "chapter-bar";
bar.hidden = true;
bar.setAttribute("aria-label", "Scroll chapter list");
bar.setAttribute("aria-orientation", "vertical");
bar.setAttribute("aria-controls", nav.id);
nav.after(bar);
function syncBar() {
  const range = nav.scrollHeight - nav.clientHeight;
  bar.hidden = range <= 1 || !nav.clientHeight;
  if (bar.hidden) return;
  bar.style.setProperty(
    "--thumb-size",
    Math.max(24, nav.clientHeight ** 2 / nav.scrollHeight) + "px",
  );
  bar.value = String(Math.round((nav.scrollTop / range) * 100));
  bar.setAttribute("aria-valuetext", bar.value + "%");
}
bar.addEventListener("input", () => {
  nav.scrollTop =
    (Number(bar.value) / 100) * (nav.scrollHeight - nav.clientHeight);
});
nav.addEventListener("scroll", syncBar, { passive: true });
new ResizeObserver(() => requestAnimationFrame(syncBar)).observe(nav);
document.fonts.ready.then(syncBar);

document.querySelector(".chapter-finder").hidden = false;
function filterChapters() {
  const term = query.value.trim().toLowerCase();
  let matches = 0;
  for (const [i, link] of links.entries()) {
    link.hidden = !topics[i].includes(term);
    if (!link.hidden) matches++;
  }
  let group;
  for (const child of nav.children) {
    if (child.tagName === "P") {
      group = child;
      group.hidden = true;
    } else if (!child.hidden) group.hidden = false;
  }
  syncBar();
  document.getElementById("chapter-result").textContent = !term
    ? ""
    : matches
      ? matches + (matches === 1 ? " chapter" : " chapters")
      : "No matching chapters.";
}
query.addEventListener("input", filterChapters);
query.setAttribute("aria-keyshortcuts", "/");
query.title = "Find a chapter (/)";
document.addEventListener("keydown", (event) => {
  if (
    event.key !== "/" ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    event.isComposing ||
    event.defaultPrevented ||
    event.target.closest("input, textarea, select, [contenteditable]")
  )
    return;
  event.preventDefault();
  setDisclosure(index, true);
  query.focus({ preventScroll: true });
});

let active = -1;
let frame;
function markChapter() {
  frame = 0;
  const bottom =
    Math.ceil(scrollY + innerHeight) >= document.documentElement.scrollHeight;
  const guide = compact.matches
    ? Math.max(
        index.parentElement.offsetHeight,
        parseFloat(getComputedStyle(sections[0]).scrollMarginTop),
      ) + 2
    : 32;
  let i = sections.length - 1;
  while (!bottom && i > 0 && sections[i].getBoundingClientRect().top > guide)
    i--;
  if (i === active) return;
  links[active]?.removeAttribute("aria-current");
  active = i;
  const link = links[i];
  link.setAttribute("aria-current", "location");
  current.textContent =
    link.querySelector(".chapter-no").textContent +
    " · " +
    link.lastChild.textContent.trim().replace(/\s+/g, " ");
  // Keep the chapter visible inside a short desktop rail without moving the article.
  if (
    !compact.matches &&
    index.open &&
    !link.hidden &&
    !index.contains(document.activeElement)
  ) {
    const rail = nav.getBoundingClientRect();
    const row = link.getBoundingClientRect();
    if (row.bottom > rail.bottom) nav.scrollTop += row.bottom - rail.bottom;
    else if (row.top < rail.top) nav.scrollTop += row.top - rail.top;
  }
}
function schedule() {
  if (!frame) frame = requestAnimationFrame(markChapter);
}
function adapt() {
  setDisclosure(index, !compact.matches, false);
  schedule();
}
function openLinkedSection() {
  const target = document.getElementById(location.hash.slice(1));
  let disclosure = target?.closest(".manual-subsection");
  let revealed = false;
  while (disclosure) {
    if (!disclosure.open) {
      setDisclosure(disclosure, true, false);
      revealed = true;
    }
    disclosure = disclosure.parentElement.closest(".manual-subsection");
  }
  if (revealed) target.scrollIntoView({ block: "start" });
  schedule();
}
adapt();
openLinkedSection();
function closeCompactIndex() {
  if (!compact.matches || !index.open) return;
  if (index.contains(document.activeElement))
    summary.focus({ preventScroll: true });
  setDisclosure(index, false);
}
window.addEventListener(
  "scroll",
  () => {
    closeCompactIndex();
    schedule();
  },
  { passive: true },
);
document.addEventListener("pointerdown", (event) => {
  if (!index.contains(event.target)) closeCompactIndex();
});
document.addEventListener("focusin", (event) => {
  if (!index.contains(event.target)) closeCompactIndex();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && index.open && compact.matches) {
    closeCompactIndex();
    summary.focus({ preventScroll: true });
  }
});
window.addEventListener("hashchange", openLinkedSection);
new ResizeObserver(schedule).observe(document.querySelector(".manual-article"));
compact.addEventListener("change", adapt);
index.addEventListener("click", (event) => {
  const link = event.target.closest("a");
  if (!link) return;
  if (query.value) {
    query.value = "";
    filterChapters();
  }
  if (compact.matches) {
    closeCompactIndex();
    const chapter = document.getElementById(link.hash.slice(1));
    chapter.tabIndex = -1;
    chapter.focus({ preventScroll: true });
  }
});
