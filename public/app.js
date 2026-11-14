const byId = (id) => document.getElementById(id);
const desk = byId("desk");
const detail = byId("detail");
const status = byId("status");
const login = byId("connect");
let token = "";
let traceUrl;
let selected;
let generation = 0;
let publicDemo = false;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function clearTrace() {
  if (traceUrl) URL.revokeObjectURL(traceUrl);
  traceUrl = undefined;
}

function clearSession() {
  byId("token").value = "";
  generation++;
  token = "";
  selected = null;
  clearTrace();
  detail.replaceChildren();
  byId("cases").replaceChildren();
  byId("leave").hidden = desk.hidden = true;
  login.hidden = publicDemo;
  status.textContent = "";
  if (!publicDemo) byId("token").focus();
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...(token && { Authorization: "Bearer " + token }),
        ...options.headers,
      },
      redirect: "error",
      cache: "no-store",
    });
  } catch {
    throw new Error("Connection failed. Check that the workspace is running.");
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403)
      throw new Error(
        options.method === "POST"
          ? "A reviewer token is required to save decisions."
          : "Access denied. Check your workspace token.",
      );
    throw new Error(
      response.status === 404
        ? "This case is no longer available."
        : "The workspace could not complete the request. Try again.",
    );
  }
  return response;
}

function reviewControl(caseId, owner, accepted) {
  const button = element(
    "button",
    accepted ? "Accept" : "Reject",
    accepted ? "pill" : "pill secondary",
  );
  button.setAttribute("aria-label", (accepted ? "Accept " : "Reject ") + owner);
  button.addEventListener("click", async () => {
    const view = generation;
    const row = button.parentElement;
    row.querySelectorAll("button").forEach((el) => (el.disabled = true));
    try {
      const result = await request(
        "/api/cases/" + encodeURIComponent(caseId) + "/reviews",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ owner, accepted }),
        },
      );
      const context = await result.json();
      if (view !== generation) return;
      status.textContent =
        "Recommendation " +
        (accepted ? "accepted" : "rejected") +
        " for " +
        owner;
      await renderCase(context, view);
    } catch (error) {
      if (view === generation) status.textContent = error.message;
    } finally {
      row.querySelectorAll("button").forEach((el) => (el.disabled = false));
    }
  });
  return button;
}

async function renderCase(
  {
    report,
    recommendations,
    evidence,
    ranked_checks = [],
    review_history = [],
    related_reports = [],
  },
  view,
) {
  clearTrace();
  detail.replaceChildren(
    element("p", report.id, "micro"),
    element("h2", report.title),
    element(
      "p",
      [report.service, report.component, report.revision].join(" / "),
      "case-meta",
    ),
    element("h3", "Suggested owners"),
  );
  for (const item of recommendations) {
    const row = element("div", undefined, "review-row");
    row.append(
      element("strong", item.owner),
      element("span", "score " + item.score, "case-meta"),
    );
    if (!publicDemo)
      row.append(
        reviewControl(report.id, item.owner, true),
        reviewControl(report.id, item.owner, false),
      );
    detail.append(row);
    if (item.reason) detail.append(element("p", item.reason, "review-reason"));
  }
  if (ranked_checks.length) {
    detail.append(element("h3", "Suggested CI checks"));
    for (const item of ranked_checks) {
      const row = element("div", undefined, "evidence-row");
      row.append(
        element("strong", item.step),
        element(
          "span",
          "score " + item.score + " / " + item.observations + " observations",
          "case-meta",
        ),
      );
      detail.append(row);
      if (item.reason)
        detail.append(element("p", item.reason, "review-reason"));
    }
  }
  if (related_reports.length) {
    detail.append(element("h3", "Related reports"));
    for (const match of related_reports) {
      const row = element("div", undefined, "evidence-row");
      const button = element(
        "button",
        match.id + " · " + match.title,
        "case-btn",
      );
      button.addEventListener("click", () => openCase(match.id));
      row.append(
        button,
        element("span", "lexical similarity " + match.score, "case-meta"),
      );
      detail.append(row, element("p", match.reason, "review-reason"));
    }
  }
  if (review_history.length) {
    detail.append(element("h3", "Review history"));
    const list = element("ol", undefined, "review-history");
    for (const review of [...review_history].reverse()) {
      const row = element("li");
      const decision = review.accepted ? "accepted" : "rejected";
      const time = element(
        "time",
        new Date(review.at).toLocaleString(undefined, {
          dateStyle: "medium",
          timeStyle: "short",
          timeZone: "UTC",
        }) + " UTC",
        "case-meta",
      );
      time.dateTime = review.at;
      row.append(
        element("span", review.actor + " " + decision + " " + review.owner),
        time,
      );
      list.append(row);
    }
    detail.append(list);
  }
  detail.append(element("h3", "Recorded evidence"));
  if (!evidence.length) {
    detail.append(element("p", "No checks recorded for this revision."));
    return;
  }
  for (const item of evidence) {
    const row = element("div", undefined, "evidence-row");
    row.append(element("strong", item.step), element("span", item.result));
    detail.append(row);
    if (item.source_digest) {
      const origin =
        item.source_method === "hmac-sha256"
          ? "Signed CI manifest"
          : "Local manifest";
      const details = element("details");
      details.append(
        element("summary", origin),
        element("code", item.source_digest),
      );
      details.className = "manifest-source";
      detail.append(details);
    }
  }
  try {
    const response = await request(
      "/api/cases/" + encodeURIComponent(report.id) + "/trace.svg",
    );
    const blob = await response.blob();
    if (view !== generation || report.id !== selected) return;
    clearTrace();
    traceUrl = URL.createObjectURL(blob);
    const img = element("img", undefined, "trace");
    img.alt =
      "Observed steps in the latest run. Interrupted runs may contain partial evidence.";
    img.src = traceUrl;
    detail.append(
      img,
      element(
        "p",
        "Latest run · recorded steps include partial runs",
        "case-meta",
      ),
    );
  } catch {
    if (view === generation)
      detail.append(element("p", "The evidence diagram is unavailable."));
  }
}

async function openCase(caseId) {
  const view = ++generation;
  selected = caseId;
  clearTrace();
  detail.textContent = "Loading case…";
  for (const button of byId("cases").querySelectorAll("button")) {
    button.setAttribute(
      "aria-current",
      String(button.dataset.caseId === caseId),
    );
  }
  try {
    const result = await request("/api/cases/" + encodeURIComponent(caseId));
    const context = await result.json();
    if (view !== generation) return;
    status.textContent = "";
    await renderCase(context, view);
  } catch (error) {
    if (view === generation) detail.textContent = error.message;
  }
}

async function refresh() {
  const view = ++generation;
  status.textContent = "Loading cases…";
  try {
    const result = await request("/api/cases");
    const cases = await result.json();
    if (view !== generation) return;
    desk.hidden = false;
    byId("leave").hidden = publicDemo;
    login.hidden = true;
    byId("token").value = "";
    const list = byId("cases");
    list.replaceChildren();
    for (const report of cases) {
      const button = element("button", undefined, "case-btn");
      button.dataset.caseId = report.id;
      button.append(element("small", report.id), element("span", report.title));
      button.addEventListener("click", () => openCase(report.id));
      const li = element("li");
      li.append(button);
      list.append(li);
    }
    if (cases.length)
      await openCase(cases.find((r) => r.id === selected)?.id || cases[0].id);
    else {
      selected = null;
      clearTrace();
      status.textContent = "";
      detail.textContent =
        "No cases yet. Import a report through the CLI or sync your bounty program.";
    }
  } catch (error) {
    if (view === generation) status.textContent = error.message;
  }
}

login.addEventListener("submit", (event) => {
  event.preventDefault();
  token = byId("token").value.trim();
  refresh();
});
byId("leave").addEventListener("click", clearSession);
byId("reload").addEventListener("click", refresh);
const evaluation = byId("evaluation");
evaluation.addEventListener("toggle", async () => {
  if (!publicDemo || !evaluation.open) return;
  const result = byId("evaluation-result");
  if (
    result.querySelector("table") ||
    result.getAttribute("aria-busy") === "true"
  )
    return;
  result.setAttribute("aria-busy", "true");
  result.textContent = "Loading measured results…";
  try {
    const response = await request("/api/evaluation");
    const { curve } = await response.json();
    const table = element("table");
    table.append(
      element(
        "caption",
        "First recommendation correct across " +
          curve[0].heldout_cases +
          " held-out synthetic cases",
      ),
    );
    const head = table.createTHead().insertRow();
    for (const label of ["Training cases", "Owner", "CI check"]) {
      const cell = element("th", label);
      cell.scope = "col";
      head.append(cell);
    }
    const body = table.createTBody();
    const percent = new Intl.NumberFormat(undefined, {
      style: "percent",
      maximumFractionDigits: 1,
    });
    for (const point of curve) {
      const row = body.insertRow();
      const trainingCases = element("th", point.training_cases);
      trainingCases.scope = "row";
      row.append(
        trainingCases,
        element("td", percent.format(point.owner_top1)),
        element("td", percent.format(point.ci_check_top1)),
      );
    }
    result.replaceChildren(table);
  } catch {
    result.textContent = "Results unavailable. Close and reopen to retry.";
  } finally {
    result.removeAttribute("aria-busy");
  }
});

async function initializeWorkspace() {
  try {
    const response = await fetch("/api/session", {
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(4000),
    });
    publicDemo = response.ok && (await response.json()).public_demo === true;
  } catch {
    publicDemo = false;
  }
  evaluation.hidden = !publicDemo;
  if (publicDemo) {
    byId("workspace-label").textContent = "Read-only demo · synthetic cases";
    return refresh();
  }
  status.textContent = "";
  login.hidden = false;
}

window.addEventListener("pagehide", clearSession);
window.addEventListener("pageshow", (event) => {
  if (event.persisted) initializeWorkspace();
});
initializeWorkspace();
