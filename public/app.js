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
let imageRequests = new AbortController();
let selected;
let generation = 0;
let sessionVersion = 0;
let publicDemo = false;
let currentPage = "";
let canReview = false;
let disclosedCase;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function beginView(label) {
  const activity = byId("activity");
  activity.setAttribute("aria-label", label);
  activity.hidden = false;
  byId("workspace-skeleton").hidden = !desk.hidden || !login.hidden;
  detail.setAttribute("aria-busy", "true");
  detail.inert = true;
  byId("retry").hidden = true;
  byId("retry").onclick = null;
  status.textContent = "";
  return ++generation;
}

function endView(view) {
  if (view !== generation) return;
  byId("activity").hidden = true;
  byId("workspace-skeleton").hidden = true;
  detail.removeAttribute("aria-busy");
  detail.inert = false;
}

function showError(message, retry) {
  status.textContent = message;
  const button = byId("retry");
  button.hidden = !retry;
  button.onclick = retry || null;
}

function clearCaseImages() {
  imageRequests.abort();
  imageRequests = new AbortController();
  for (const url of caseImages) URL.revokeObjectURL(url);
  caseImages.clear();
}

async function loadCaseImage(container, caseId, path, className, alt, failure) {
  const { signal } = imageRequests;
  container.setAttribute("aria-busy", "true");
  const retryImage = () => {
    if (signal.aborted || !container.isConnected || selected !== caseId) return;
    const retry = element("button", "Retry image", "cmd");
    retry.type = "button";
    retry.onclick = () =>
      loadCaseImage(container, caseId, path, className, alt, failure);
    container.replaceChildren(element("p", failure), retry);
  };
  container.replaceChildren();
  try {
    const response = await request(
      "cases/" + encodeURIComponent(caseId) + "/" + path,
      { signal },
    );
    if (response.headers.get("Content-Type")?.split(";")[0] !== "image/svg+xml")
      throw new Error("Expected an SVG image");
    const blob = await response.blob();
    if (signal.aborted || !container.isConnected || selected !== caseId) return;
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
        retryImage();
      },
      { once: true },
    );
    container.replaceChildren(image);
  } catch {
    retryImage();
  } finally {
    container.removeAttribute("aria-busy");
  }
}

function clearSession() {
  sessionVersion++;
  byId("report-file").disabled = false;
  byId("report-drop").classList.remove("dragging");
  byId("report-upload").hidden = true;
  byId("report-upload").reset();
  byId("report-submit").disabled = true;
  byId("token").value = "";
  endView(++generation);
  byId("activity").removeAttribute("aria-label");
  byId("retry").hidden = true;
  byId("retry").onclick = null;
  for (const id of ["previous-page", "next-page"]) {
    byId(id).value = "";
    byId(id).disabled = true;
  }
  currentPage = "";
  byId("case-pager").hidden = true;
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
  const session = sessionVersion;
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
      signal: options.signal
        ? AbortSignal.any([options.signal, AbortSignal.timeout(30000)])
        : AbortSignal.timeout(30000),
    });
  } catch {
    throw new Error("Connection failed. Check that the workspace is running.");
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      const message =
        "Access denied. Reconnect with an authorized workspace token.";
      if (token && !publicDemo && session === sessionVersion) {
        clearSession();
        showError(message);
      }
      throw new Error(message);
    }
    if (response.status === 400 && path === "import/report")
      throw new Error(
        "The workspace rejected the report. Check its identifiers, field names and field lengths.",
      );
    if (response.status === 404 && path.startsWith("case-page"))
      throw new Error(
        "Case paging is unavailable. Check that the workspace origin is up to date.",
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
      renderCase(context);
    } catch (error) {
      if (view === generation) status.textContent = error.message;
    } finally {
      row.querySelectorAll("button").forEach((el) => (el.disabled = false));
    }
  });
  return button;
}

function imageDisclosure(label, caseId, path, className, alt, failure) {
  const disclosure = element("details", undefined, "case-diagram");
  const container = element("div");
  disclosure.append(element("summary", label), container);
  disclosure.addEventListener("toggle", () => {
    if (
      !disclosure.open ||
      !disclosure.isConnected ||
      selected !== caseId ||
      container.childNodes.length ||
      container.getAttribute("aria-busy") === "true"
    )
      return;
    loadCaseImage(container, caseId, path, className, alt, failure);
  });
  return disclosure;
}

function renderCase({
  report,
  recommendations,
  evidence,
  ranked_checks = [],
  review_history = [],
  related_reports = [],
  worker_outcomes = [],
}) {
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
  if (publicDemo && !orgWorkspace && report.id === selected) {
    const exportLink = element(
      "a",
      "Download SelfGraph facts (JSON)",
      "case-export",
    );
    exportLink.href =
      apiBase + "cases/" + encodeURIComponent(report.id) + "/selfgraph.json";
    exportLink.download = report.id + "-selfgraph.json";
    heading.append(exportLink);
  }
  detail.replaceChildren(heading);
  detail.append(
    imageDisclosure(
      "Review workflow diagram",
      report.id,
      "diagram.svg",
      "review-diagram",
      "Review workflow for " + report.id + " on revision " + report.revision,
      "This workspace could not supply a review diagram. The case details remain available below.",
    ),
  );
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
  const trace = imageDisclosure(
    "Observed evidence trace",
    report.id,
    "trace.svg",
    "trace",
    "Observed steps in the latest run for " + report.id,
    "The observed evidence trace is unavailable.",
  );
  trace.append(
    element(
      "p",
      "Latest run · recorded steps include partial runs",
      "case-meta",
    ),
  );
  detail.append(trace);
}

async function openCase(caseId) {
  const view = beginView("Retrieving case " + caseId);
  try {
    const result = await request("cases/" + encodeURIComponent(caseId));
    const context = await result.json();
    if (view !== generation) return;
    selected = caseId;
    byId("case-select").value = caseId;
    for (const button of byId("cases").querySelectorAll("button")) {
      button.setAttribute(
        "aria-current",
        String(button.dataset.caseId === caseId),
      );
    }
    endView(view);
    renderCase(context);
  } catch (error) {
    if (view !== generation) return;
    showError(error.message, () => openCase(caseId));
    byId("case-select").value = selected || "";
  } finally {
    endView(view);
  }
}

async function refresh(query = currentPage, preferred = selected) {
  const view = beginView("Retrieving cases");
  const upload = byId("report-upload");
  let canImport = false;
  try {
    if (!publicDemo) {
      const session = await (await request("session")).json();
      if (view !== generation) return;
      canReview = session.can_review === true;
      canImport = session.can_import === true;
    }
    const result = await request(publicDemo ? "cases" : "case-page" + query);
    const page = await result.json();
    if (view !== generation) return;
    const cases = publicDemo ? page : page.cases;
    if (query && !cases.length) return refresh("");
    currentPage = query;
    const pager = byId("case-pager");
    pager.hidden = publicDemo || !(page.previous || page.next);
    for (const [id, cursor] of [
      ["previous-page", page.previous],
      ["next-page", page.next],
    ]) {
      const button = byId(id);
      button.disabled = !cursor;
      button.value = cursor || "";
    }
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
    byId("case-count").textContent = pager.hidden
      ? String(cases.length)
      : cases.length + " on this page";
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
      await openCase(cases.find((r) => r.id === preferred)?.id || cases[0].id);
    else {
      selected = null;
      clearCaseImages();
      status.textContent = "";
      detail.textContent =
        "No cases yet. Import a report through the CLI or sync your bounty program.";
    }
  } catch (error) {
    if (view === generation) {
      showError(error.message, () => refresh(query, preferred));
    }
  } finally {
    endView(view);
  }
}

function validReportFile(input) {
  const file = input.files[0];
  if (file && file.size && file.size <= 1024 * 1024) return file;
  input.value = "";
  status.textContent = "Choose a JSON file between one byte and 1 MiB.";
  return null;
}

async function uploadReport(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const input = byId("report-file");
  if (form.hidden || input.disabled) return;
  const file = validReportFile(input);
  if (!file) return;
  const button = byId("report-submit");
  button.disabled = true;
  input.disabled = true;
  const session = sessionVersion;
  const view = generation;
  try {
    const body = await file.arrayBuffer();
    if (view !== generation) return;
    let report;
    try {
      report = JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(body),
      );
    } catch {
      status.textContent = "Choose a valid UTF-8 JSON file.";
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
    form.reset();
    await refresh("?from=" + encodeURIComponent(saved.id), saved.id);
    if (selected === saved.id) status.textContent = "Imported " + saved.id;
  } catch (error) {
    if (view === generation) status.textContent = error.message;
  } finally {
    if (session === sessionVersion) {
      input.disabled = false;
      button.disabled = !input.files.length;
    }
  }
}

function initializeConsole() {
  if (orgWorkspace) {
    document.title = "Organization workspace · Ichnix";
    document.querySelector(".workspace-intro h1").textContent =
      "Organization workspace";

    document
      .querySelector('.topnav a[href="/console"]')
      .removeAttribute("aria-current");
    byId("private-signin").setAttribute("aria-current", "page");
  }
  const drop = byId("report-drop");
  const input = byId("report-file");
  for (const type of ["dragover", "drop"]) {
    document.addEventListener(type, (event) => {
      if (
        !event.dataTransfer.types.includes("Files") ||
        drop.contains(event.target)
      )
        return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "none";
    });
  }
  drop.addEventListener("dragover", (event) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = input.disabled ? "none" : "copy";
    drop.classList.toggle("dragging", !input.disabled);
  });
  drop.addEventListener("dragleave", (event) => {
    if (!drop.contains(event.relatedTarget)) drop.classList.remove("dragging");
  });
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("dragging");
    if (input.disabled || byId("report-upload").hidden) return;
    const items = [...event.dataTransfer.items];
    if (items.some((item) => item.webkitGetAsEntry?.()?.isDirectory)) {
      input.value = "";
      byId("report-submit").disabled = true;
      status.textContent = "Choose a file instead of a folder.";
      return;
    }
    const files = event.dataTransfer.files;
    if (files.length !== 1) {
      input.value = "";
      byId("report-submit").disabled = true;
      status.textContent = "Drop one file at a time.";
      return;
    }
    input.files = files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  input.addEventListener("change", () => {
    if (!input.files.length) {
      byId("report-submit").disabled = true;
      status.textContent = "";
      return;
    }
    const file = validReportFile(input);
    byId("report-submit").disabled = !file;
    if (file) status.textContent = "Ready to import " + file.name;
  });
  byId("report-upload").addEventListener("submit", uploadReport);
  login.addEventListener("submit", (event) => {
    event.preventDefault();
    sessionVersion++;
    token = byId("token").value.trim();
    refresh();
  });
  byId("leave").addEventListener("click", clearSession);
  byId("reload").addEventListener("click", () => refresh());
  for (const [id, direction] of [
    ["previous-page", "before"],
    ["next-page", "after"],
  ]) {
    byId(id).addEventListener("click", (event) => {
      refresh(
        "?" + direction + "=" + encodeURIComponent(event.currentTarget.value),
      );
    });
  }
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
    result.replaceChildren();
    try {
      const response = await request("evaluation");
      const { curve } = await response.json();
      const percent = new Intl.NumberFormat(undefined, {
        style: "percent",
        maximumFractionDigits: 1,
      });
      function scoreTable(caption, ownerMetric, checkMetric) {
        const table = element("table");
        table.append(element("caption", caption));
        const head = table.createTHead().insertRow();
        for (const label of ["Training cases", "Owner", "CI check"]) {
          const cell = element("th", label);
          cell.scope = "col";
          head.append(cell);
        }
        const body = table.createTBody();
        for (const point of curve) {
          const trainingCases = element("th", point.training_cases);
          trainingCases.scope = "row";
          body
            .insertRow()
            .append(
              trainingCases,
              element("td", percent.format(point[ownerMetric])),
              element("td", percent.format(point[checkMetric])),
            );
        }
        return table;
      }
      result.replaceChildren(
        element(
          "p",
          "First choice correct across " +
            curve[0].heldout_cases +
            " held-out synthetic cases.",
        ),
        scoreTable(
          "Feedback from the same component revision",
          "owner_top1",
          "ci_check_top1",
        ),
      );
      if (
        curve.every(
          (point) =>
            Number.isFinite(point.pooled_owner_top1) &&
            Number.isFinite(point.pooled_ci_check_top1),
        )
      ) {
        result.append(
          scoreTable(
            "Same feedback pooled across components",
            "pooled_owner_top1",
            "pooled_ci_check_top1",
          ),
        );
      }
      result.append(
        element(
          "p",
          "Fixed catalog owner accuracy: " +
            percent.format(curve[0].catalog_owner_top1),
        ),
      );
    } catch {
      result.textContent = "Results unavailable. Close and reopen to retry.";
    } finally {
      result.removeAttribute("aria-busy");
    }
  });

  async function initializeWorkspace() {
    const view = beginView("Connecting to workspace");
    try {
      const response = await fetch(apiBase + "session", {
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(4000),
      });
      if (!response.ok && !(response.status === 401 && !hostedPreview)) {
        throw new Error(
          "The workspace is unavailable. Check the connection and try again.",
        );
      }
      const session = response.ok ? await response.json() : {};
      if (view !== generation) return;
      publicDemo =
        !orgWorkspace && (hostedPreview || session.public_demo === true);
      disclosedCase = publicDemo ? session.disclosed_case : null;
      evaluation.hidden = !publicDemo;
      if (publicDemo) {
        byId("workspace-label").textContent = disclosedCase
          ? "Read-only workspace · public disclosure and sample cases"
          : "Read-only demo · synthetic cases";
        selected ??= disclosedCase;
        return refresh();
      }
      byId("workspace-label").textContent = orgWorkspace
        ? ""
        : "Private workspace";
      login.hidden = false;
    } catch {
      if (view !== generation) return;
      login.hidden = true;
      showError(
        "The workspace is unavailable. Check the connection and try again.",
        initializeWorkspace,
      );
    } finally {
      endView(view);
    }
  }

  window.addEventListener("pagehide", clearSession);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) initializeWorkspace();
  });
  initializeWorkspace();
}

if (desk) initializeConsole();
