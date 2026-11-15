const index = document.querySelector(".index-panel");
const links = [...document.querySelectorAll("#chapter-links a")];
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

document.querySelector(".chapter-finder").hidden = false;
function filterChapters() {
  const term = query.value.trim().toLowerCase();
  let matches = 0;
  for (const [i, link] of links.entries()) {
    link.hidden = !topics[i].includes(term);
    if (!link.hidden) matches++;
  }
  let group;
  for (const child of document.getElementById("chapter-links").children) {
    if (child.tagName === "P") {
      group = child;
      group.hidden = true;
    } else if (!child.hidden) group.hidden = false;
  }
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
  index.open = true;
  query.focus();
});

let active = -1;
let frame;
function markChapter() {
  frame = 0;
  const bottom =
    Math.ceil(scrollY + innerHeight) >= document.documentElement.scrollHeight;
  const guide = compact.matches
    ? Math.max(64, index.querySelector("summary").offsetHeight) + 8
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
    const nav = link.parentElement;
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
  index.open = !compact.matches;
  schedule();
}
function openLinkedSection() {
  const target = document.getElementById(location.hash.slice(1));
  const disclosure = target?.closest(".manual-subsection");
  if (disclosure && !disclosure.open) {
    disclosure.open = true;
    target.scrollIntoView({ block: "start" });
  }
  schedule();
}
adapt();
openLinkedSection();
window.addEventListener("scroll", schedule, { passive: true });
window.addEventListener("hashchange", openLinkedSection);
new ResizeObserver(schedule).observe(document.querySelector(".manual-article"));
compact.addEventListener("change", adapt);
index.addEventListener("click", (event) => {
  if (!event.target.closest("a")) return;
  if (query.value) {
    query.value = "";
    filterChapters();
  }
  if (compact.matches) index.open = false;
});
