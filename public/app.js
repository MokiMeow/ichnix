const byId = (id) => document.getElementById(id);
const desk = byId("desk");
const detail = byId("detail");
const status = byId("status");
const login = byId("connect");
const orgWorkspace = ["/workspace", "/workspace/"].includes(location.pathname);
const hostedPreview =
  document.documentElement.dataset.hosted === "true" && !orgWorkspace;
const apiBase = orgWorkspace ? "/org-api/" : "/api/";
let token = "";
const caseImages = new Set();
let selected;
let generation = 0;
let publicDemo = false;
let canReview = false;
let disclosedCase;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function clearCaseImages() {
  for (const url of caseImages) URL.revokeObjectURL(url);
  caseImages.clear();
}

async function loadCaseImage(container, caseId, path, className, alt, failure) {
  try {
    const response = await request(
      "cases/" + encodeURIComponent(caseId) + "/" + path,
    );
    if (response.headers.get("Content-Type")?.split(";")[0] !== "image/svg+xml")
      throw new Error("Expected an SVG image");
    const blob = await response.blob();
    if (!container.isConnected || selected !== caseId) return;
    const url = URL.createObjectURL(blob);
    caseImages.add(url);
    const image = element("img", undefined, className);
    image.alt = alt;
    image.src = url;
    image.addEventListener(
      "error",
      () => {
        URL.revokeObjectURL(url);
        caseImages.delete(url);
        container.textContent = failure;
      },
      { once: true },
    );
    container.replaceChildren(image);
  } catch {
    if (container.isConnected && selected === caseId)
      container.textContent = failure;
  }
}

function clearSession() {
  byId("report-upload").hidden = true;
  byId("report-upload").reset();
  byId("token").value = "";
  generation++;
  token = "";
  canReview = false;
  selected = null;
  clearCaseImages();
  detail.replaceChildren();
  byId("cases").replaceChildren();
  byId("case-select").replaceChildren();
  byId("case-count").textContent = "";
  byId("leave").hidden = desk.hidden = true;
  login.hidden = publicDemo;
  status.textContent = "";
  if (!publicDemo) byId("token").focus();
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(apiBase + path, {
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
        path === "import/report"
          ? "A named intake credential is required."
          : options.method === "POST"
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

function reviewControl(report, owner, accepted) {
  const { id: caseId, service, component, revision } = report;
  const decisionId = crypto.randomUUID();
  const button = element(
    "button",
    accepted ? "Accept" : "Reject",
    accepted ? "cmd primary" : "cmd",
  );
  button.setAttribute("aria-label", (accepted ? "Accept " : "Reject ") + owner);
  button.addEventListener("click", async () => {
    const view = generation;
    const row = button.parentElement;
    row.querySelectorAll("button").forEach((el) => (el.disabled = true));
    try {
      const result = await request(
        "cases/" + encodeURIComponent(caseId) + "/reviews",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": decisionId,
          },
          body: JSON.stringify({
            owner,
            accepted,
            scope: { service, component, revision },
          }),
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
    worker_outcomes = [],
  },
  view,
) {
  clearCaseImages();
  const heading = element("header", undefined, "case-heading");
  heading.append(
    element("p", report.id, "case-id"),
    element("h2", report.title),
  );
  const metadata = element("dl", undefined, "case-scope");
  for (const [label, value] of [
    ["Service", report.service],
    ["Component", report.component],
    ["Revision", report.revision],
  ]) {
    const field = element("div");
    field.append(element("dt", label), element("dd", value));
    metadata.append(field);
  }
  heading.append(metadata);
  detail.replaceChildren(heading);
  const diagram = element("details", undefined, "case-diagram");
  const diagramBody = element("div", "Loading workflow diagram…");
  diagram.append(element("summary", "Review workflow diagram"), diagramBody);
  diagram.addEventListener("toggle", () => {
    if (!diagram.open || diagramBody.dataset.loaded) return;
    diagramBody.dataset.loaded = "true";
    loadCaseImage(
      diagramBody,
      report.id,
      "diagram.svg",
      "review-diagram",
      "Review workflow for " + report.id + " on revision " + report.revision,
      "This workspace could not supply a review diagram. The case details remain available below.",
    );
  });
  detail.append(diagram);
  if (publicDemo && report.id === disclosedCase) {
    const source = element("a", "Read the disclosed HackerOne report");
    source.href = "https://hackerone.com/reports/2236133";
    detail.append(
      source,
      element(
        "p",
        "These signed HTTP observations came from the local Ichnix console during acceptance. The source report describes curl.",
        "case-meta",
      ),
    );
  }
  detail.append(element("h3", "Suggested owners"));
  for (const item of recommendations) {
    const row = element("div", undefined, "review-row");
    row.append(
      element("strong", item.owner),
      element("span", "score " + item.score, "case-meta"),
    );
    if (canReview)
      row.append(
        reviewControl(report, item.owner, true),
        reviewControl(report, item.owner, false),
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
  if (worker_outcomes.length) {
    detail.append(element("h3", "Worker handoffs"));
    for (const item of worker_outcomes) {
      const row = element("div", undefined, "evidence-row");
      row.append(
        element("strong", item.disposition.replaceAll("-", " ")),
        element("span", item.worker),
      );
      detail.append(row, element("p", item.at, "case-meta"));
    }
  }
  if (review_history.length) {
    const history = element("details", undefined, "history-panel");
    history.append(
      element("summary", "Review history (" + review_history.length + ")"),
    );
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
    history.append(list);
    detail.append(history);
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
  const trace = element("div");
  detail.append(
    element("h3", "Observed evidence trace"),
    trace,
    element(
      "p",
      "Latest run · recorded steps include partial runs",
      "case-meta",
    ),
  );
  await loadCaseImage(
    trace,
    report.id,
    "trace.svg",
    "trace",
    "Observed steps in the latest run for " + report.id,
    "The observed evidence trace is unavailable.",
  );
}

async function openCase(caseId) {
  const view = ++generation;
  selected = caseId;
  byId("case-select").value = caseId;
  clearCaseImages();
  detail.textContent = "Loading case…";
  for (const button of byId("cases").querySelectorAll("button")) {
    button.setAttribute(
      "aria-current",
      String(button.dataset.caseId === caseId),
    );
  }
  try {
    const result = await request("cases/" + encodeURIComponent(caseId));
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
  const upload = byId("report-upload");
  upload.hidden = true;
  let canImport = false;
  status.textContent = "Loading cases…";
  try {
    if (!publicDemo) {
      const session = await (await request("session")).json();
      if (view !== generation) return;
      canReview = session.can_review === true;
      canImport = session.can_import === true;
    }
    const result = await request("cases");
    const cases = await result.json();
    if (view !== generation) return;
    upload.hidden = !canImport;
    desk.hidden = false;
    byId("leave").hidden = publicDemo;
    login.hidden = true;
    byId("token").value = "";
    const list = byId("cases");
    const picker = byId("case-select");
    list.replaceChildren();
    picker.replaceChildren();
    picker.disabled = cases.length === 0;
    byId("case-count").textContent = String(cases.length);
    if (!cases.length) picker.append(element("option", "No cases"));

    for (const report of cases) {
      const option = element("option", report.id + " · " + report.title);
      option.value = report.id;
      picker.append(option);
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
      clearCaseImages();
      status.textContent = "";
      detail.textContent =
        "No cases yet. Import a report through the CLI or sync your bounty program.";
    }
  } catch (error) {
    if (view === generation) {
      if (!publicDemo) clearSession();
      status.textContent = error.message;
    }
  }
}

async function uploadReport(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const file = byId("report-file").files[0];
  if (!file || file.size > 1024 * 1024 || !file.size) {
    status.textContent = "Choose a JSON file between one byte and 1 MiB.";
    return;
  }
  const button = form.querySelector("button");
  button.disabled = true;
  const view = generation;
  try {
    const body = await file.text();
    if (view !== generation) return;
    let report;
    try {
      report = JSON.parse(body);
    } catch {
      status.textContent = "The selected file is not valid JSON.";
      return;
    }
    const keys = ["id", "title", "service", "revision", "component", "owner"];
    if (
      !report ||
      Object.keys(report).length !== keys.length ||
      !keys.every(
        (key) => typeof report[key] === "string" && report[key].trim(),
      )
    ) {
      status.textContent =
        "Choose one report with the six required metadata fields.";
      return;
    }
    const response = await request("import/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    const saved = await response.json();
    if (view !== generation) return;
    selected = saved.id;
    form.reset();
    await refresh();
    if (selected === saved.id) status.textContent = "Imported " + saved.id;
  } catch (error) {
    if (view === generation) status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function initializeConsole() {
  if (orgWorkspace) {
    document.title = "Organization workspace · Ichnix";
    document.querySelector(".workspace-intro h1").textContent =
      "Organization workspace";
    byId("workspace-label").textContent =
      "Sign in with a named organization credential.";
    document
      .querySelector('.topnav a[href="/console"]')
      .removeAttribute("aria-current");
    byId("private-signin").setAttribute("aria-current", "page");
  }
  byId("report-upload").addEventListener("submit", uploadReport);
  login.addEventListener("submit", (event) => {
    event.preventDefault();
    token = byId("token").value.trim();
    refresh();
  });
  byId("leave").addEventListener("click", clearSession);
  byId("reload").addEventListener("click", refresh);
  byId("case-select").addEventListener("change", (event) =>
    openCase(event.target.value),
  );
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
      const response = await request("evaluation");
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
      const response = await fetch(apiBase + "session", {
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(4000),
      });
      const session = response.ok ? await response.json() : {};
      publicDemo =
        !orgWorkspace && (hostedPreview || session.public_demo === true);
      disclosedCase = publicDemo ? session.disclosed_case : null;
    } catch {
      publicDemo = hostedPreview;
    }
    evaluation.hidden = !publicDemo;
    if (publicDemo) {
      byId("workspace-label").textContent = disclosedCase
        ? "Read-only workspace · public disclosure and sample cases"
        : "Read-only demo · synthetic cases";
      selected ??= disclosedCase;
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
}

if (desk) {
  initializeConsole();
} else {
  const index = document.querySelector(".index-panel");
  if (index) {
    const compact = matchMedia("(max-width:45rem)");
    const adapt = () => {
      index.open = !compact.matches;
    };
    const openLinkedSection = () => {
      const id = location.hash.slice(1);
      const target = id && document.getElementById(id);
      const subsection = target && target.closest(".manual-subsection");
      if (subsection) subsection.open = true;
    };
    adapt();
    openLinkedSection();
    window.addEventListener("hashchange", openLinkedSection);
    compact.addEventListener("change", adapt);
    index.addEventListener("click", (event) => {
      if (!compact.matches || !event.target.closest("a")) return;
      index.open = false;
    });
  }
}
