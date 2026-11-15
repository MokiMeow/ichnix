const byId = (id) => document.getElementById(id);
const desk = byId("desk");
const detail = byId("case-content");
const status = byId("status");
const login = byId("connect");
const orgWorkspace = ["/workspace", "/workspace/"].includes(location.pathname);
const hostedPreview =
  document.documentElement.dataset.hosted === "true" && !orgWorkspace;
const apiBase = orgWorkspace ? "/org-api/" : "/api/";
let token = "";
let reportBody;
const caseObjectUrls = new Set();
let imageRequests = new AbortController();
let selected;
let generation = 0;
let sessionVersion = 0;
let publicDemo = false;
const publishedCases = new Map();
let currentPage = "";
let canReview = false;
let disclosedCase;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function startActivity(host, label) {
  host.querySelector(":scope > .view-activity")?.remove();
  const activity = element("span", undefined, "view-activity");
  activity.setAttribute("role", "progressbar");
  activity.setAttribute("aria-label", label);
  activity.setAttribute("aria-live", "polite");
  const mark = element("span", undefined, "thinking-mark");
  mark.setAttribute("aria-hidden", "true");
  const iconOnly = host.matches(".aside-title, #case-activity");
  activity.append(
    mark,
    element("span", label, iconOnly ? "sr-only" : undefined),
  );
  host.append(activity);
  return () => activity.remove();
}

let finishViewActivity;
function beginView(label, host) {
  finishViewActivity?.();
  finishViewActivity = startActivity(host, label);
  detail.setAttribute("aria-busy", "true");
  detail.inert = true;
  byId("retry").hidden = true;
  byId("retry").onclick = null;
  status.textContent = "";
  return ++generation;
}

function endView(view) {
  if (view !== generation) return;
  finishViewActivity?.();
  finishViewActivity = undefined;
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
  for (const url of caseObjectUrls) URL.revokeObjectURL(url);
  caseObjectUrls.clear();
}

async function loadCaseImage(container, caseId, path, className, alt, failure) {
  const { signal } = imageRequests;
  container.replaceChildren();
  const finish = startActivity(
    container,
    path === "trace.svg" ? "Loading evidence trace" : "Loading review diagram",
  );
  container.setAttribute("aria-busy", "true");
  const retryImage = () => {
    if (signal.aborted || !container.isConnected || selected !== caseId) return;
    const retry = element("button", "Retry image", "cmd");
    retry.type = "button";
    retry.onclick = () =>
      loadCaseImage(container, caseId, path, className, alt, failure);
    container.replaceChildren(element("p", failure), retry);
  };
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
    caseObjectUrls.add(url);
    const image = element("img", undefined, className);
    image.alt = alt;
    image.src = url;
    image.addEventListener(
      "error",
      () => {
        URL.revokeObjectURL(url);
        caseObjectUrls.delete(url);
        retryImage();
      },
      { once: true },
    );
    container.replaceChildren(image);
  } catch {
    retryImage();
  } finally {
    container.removeAttribute("aria-busy");
    finish();
  }
}

function clearSession() {
  sessionVersion++;
  resetReport();
  byId("report-upload").hidden = true;
  byId("token").value = "";
  byId("token").readOnly = false;
  byId("connect-submit").disabled = true;
  document
    .querySelectorAll(".view-activity")
    .forEach((activity) => activity.remove());
  endView(++generation);
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
  byId("case-activity").replaceChildren();
  publishedCases.clear();
  byId("cases").replaceChildren();
  byId("case-select").replaceChildren();
  byId("case-count").textContent = "";
  byId("leave").hidden = desk.hidden = true;
  login.hidden = publicDemo;
  if (orgWorkspace) byId("private-signin").textContent = "Private sign-in";
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
        { cause: response.status },
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
    const finish = startActivity(button.parentElement, "Saving owner review");
    const controls = detail.querySelectorAll(".review-row button");
    controls.forEach((control) => (control.disabled = true));
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
      finish();
      controls.forEach((control) => (control.disabled = false));
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

function readmeDownload(caseId) {
  const button = element("button", "Download report README", "cmd");
  button.type = "button";
  button.onclick = async () => {
    const { signal } = imageRequests;
    const finish = startActivity(
      button.parentElement,
      "Preparing report README",
    );
    button.disabled = true;
    try {
      const response = await request(
        "cases/" + encodeURIComponent(caseId) + "/readme.md",
        { signal },
      );
      if (
        response.headers.get("content-type")?.split(";")[0] !== "text/markdown"
      )
        throw new Error("The workspace returned an unexpected file type.");
      const file = await response.blob();
      if (signal.aborted || !button.isConnected || selected !== caseId) return;
      const link = element("a");
      const url = URL.createObjectURL(file);
      link.href = url;
      link.download = "README.md";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      if (!signal.aborted && button.isConnected) showError(error.message);
    } finally {
      button.disabled = false;
      finish();
    }
  };
  return button;
}

function sampleReadme(report) {
  // Build the public download exclusively from the mapped fields already on screen.
  const fields = ["id", "title", "service", "component", "revision", "owner"];
  const lines = fields.map(
    (key) => "    " + key + ": " + JSON.stringify(report[key]),
  );
  const file = new Blob(
    [
      "# Report context\n\nUntrusted intake metadata\n\n",
      lines.join("\n"),
      "\n",
    ],
    { type: "text/markdown;charset=utf-8" },
  );
  const link = element("a", "Download report README", "case-export");
  link.download = "README.md";
  link.href = URL.createObjectURL(file);
  caseObjectUrls.add(link.href);
  return link;
}

function appendObservations(parent, observations) {
  for (const item of observations) {
    const row = element("div", undefined, "evidence-row");
    row.append(element("strong", item.step), element("span", item.result));
    parent.append(row);
    if (item.source_digest) {
      const origin =
        item.source_method === "hmac-sha256"
          ? "Signed CI manifest"
          : "Local manifest";
      const details = element("details", undefined, "manifest-source");
      details.append(
        element("summary", origin),
        element("code", item.source_digest),
      );
      parent.append(details);
    }
  }
}

function renderEvidence(evidence) {
  const byRun = new Map();
  for (const item of evidence) {
    const id = item.run_id || null;
    const rows = byRun.get(id) || [];
    rows.push(item);
    // Observations arrive chronologically, including interleaved runs.
    byRun.delete(id);
    byRun.set(id, rows);
  }
  const runs = [...byRun].reverse().filter(([id]) => id);
  if (runs.length < 2) {
    appendObservations(detail, evidence);
    return;
  }
  const table = element("table", undefined, "run-comparison");
  table.append(element("caption", "Two most recent recorded runs"));
  const head = table.createTHead().insertRow();
  for (const label of ["Check", "Previous", "Latest"]) {
    const cell = element("th", label);
    cell.scope = "col";
    head.append(cell);
  }
  const [latest, previous] = runs
    .slice(0, 2)
    .map(([, rows]) => new Map(rows.map((row) => [row.step, row.result])));
  const body = table.createTBody();
  for (const step of new Set([...latest.keys(), ...previous.keys()])) {
    const row = body.insertRow();
    const name = element("th", step.replaceAll("_", " "));
    name.scope = "row";
    row.append(name);
    for (const results of [previous, latest])
      row.append(element("td", results.get(step) ?? "not recorded"));
  }
  detail.append(table);
  for (const [index, [, observations]] of runs.entries()) {
    const disclosure = element("details", undefined, "history-panel");
    disclosure.append(
      element(
        "summary",
        "Run " +
          (runs.length - index) +
          " · " +
          observations.length +
          " observations",
      ),
    );
    appendObservations(disclosure, observations);
    detail.append(disclosure);
  }
  if (byRun.has(null)) {
    const group = element("details", undefined, "history-panel");
    group.append(element("summary", "Observations without a run ID"));
    appendObservations(group, byRun.get(null));
    detail.append(group);
  }
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
  const title = element("h2", report.title);
  title.tabIndex = -1;
  byId("case-activity").replaceChildren(element("p", report.id, "case-id"));
  heading.append(title);
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
    const downloads = element("div", undefined, "case-downloads");
    downloads.append(sampleReadme(report), exportLink);
    heading.append(downloads);
  }
  if (!publicDemo) heading.append(readmeDownload(report.id));
  detail.replaceChildren(heading);
  if (publicDemo && report.id === "IX-214") {
    const guide = element("section", undefined, "case-guide");
    guide.append(
      element("h3", "How to read this example"),
      element(
        "p",
        "Compare the recorded checks, inspect the ownership decision, then follow the outstanding evidence request. This walkthrough uses a synthetic report.",
      ),
    );
    const jumps = element("nav", undefined, "case-jumps");
    jumps.setAttribute("aria-label", "Example sections");
    for (const [label, id] of [
      ["Compare runs", "case-evidence"],
      ["Owner review", "case-owners"],
      ["Worker handoff", "case-handoffs"],
    ]) {
      const link = element("a", label);
      link.href = "#" + id;
      jumps.append(link);
    }
    guide.append(jumps);
    detail.append(guide);
  }
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
        "The source is a public curl report. The signed observations shown here measure the demo console’s HTTP headers.",
        "case-meta",
      ),
    );
  }
  detail.append(
    Object.assign(element("h3", "Suggested owners"), { id: "case-owners" }),
  );
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
      button.addEventListener("click", async () => {
        const options = byId("case-select").options;
        if (
          publicDemo ||
          [...options].some((option) => option.value === match.id)
        )
          await openCase(match.id);
        else await refresh("?from=" + encodeURIComponent(match.id), match.id);
        if (selected === match.id) detail.querySelector("h2")?.focus();
      });
      row.append(
        button,
        element("span", "lexical similarity " + match.score, "case-meta"),
      );
      if (match.possible_duplicate === true)
        detail.append(element("strong", "Possible duplicate · compare reports"));
      detail.append(row, element("p", match.reason, "review-reason"));
    }
  }
  if (worker_outcomes.length) {
    detail.append(
      Object.assign(element("h3", "Worker handoffs"), { id: "case-handoffs" }),
    );
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
  detail.append(
    Object.assign(element("h3", "Recorded evidence"), { id: "case-evidence" }),
  );
  if (!evidence.length) {
    detail.append(element("p", "No checks recorded for this revision."));
    return;
  }
  renderEvidence(evidence);
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

async function readCase(caseId) {
  if (publicDemo && publishedCases.has(caseId))
    return publishedCases.get(caseId);
  const response = await request("cases/" + encodeURIComponent(caseId));
  return response.json();
}

function displayCase(context) {
  selected = context.report.id;
  if (publicDemo) {
    // Revisited public snapshots live only in this tab, until its next refresh.
    if (!publishedCases.has(selected) && publishedCases.size >= 16)
      publishedCases.delete(publishedCases.keys().next().value);
    publishedCases.set(selected, context);
  }
  byId("case-select").value = selected;
  for (const button of byId("cases").querySelectorAll("button")) {
    button.disabled = button.dataset.caseId === selected;
    button.setAttribute("aria-current", String(button.disabled));
  }
  renderCase(context);
}

async function openCase(caseId, trigger) {
  const view = beginView("Retrieving case " + caseId, byId("case-activity"));
  try {
    const context = await readCase(caseId);
    if (view !== generation) return;
    const moveFocus = trigger === document.activeElement;
    endView(view);
    displayCase(context);
    if (moveFocus) detail.querySelector("h2").focus();
  } catch (error) {
    if (view !== generation) return;
    showError(error.message, () => openCase(caseId));
    byId("case-select").value = selected || "";
  } finally {
    endView(view);
  }
}

async function refresh(query = currentPage, preferred = selected) {
  const host = desk.hidden
    ? login.hidden
      ? byId("workspace-label").parentElement
      : login
    : byId("reload").parentElement;
  publishedCases.clear();
  const view = beginView("Retrieving cases", host);
  const upload = byId("report-upload");
  try {
    const [session, page] = await Promise.all([
      publicDemo ? null : request("session").then((res) => res.json()),
      request(publicDemo ? "cases" : "case-page" + query).then((res) =>
        res.json(),
      ),
    ]);
    if (view !== generation) return;
    const cases = publicDemo ? page : page.cases;
    if (
      query === "?from=" + encodeURIComponent(preferred) &&
      cases[0]?.id !== preferred
    )
      throw new Error("This case is no longer available.");
    if (query && !cases.length) return refresh("");
    const target = cases.find((report) => report.id === preferred) || cases[0];
    let context;
    let initialCaseError;
    if (target) {
      try {
        context = await readCase(target.id);
      } catch (error) {
        // A first-case failure still leaves other reports and intake available.
        if (!desk.hidden) throw error;
        initialCaseError = error;
      }
    }
    if (view !== generation) return;
    canReview = session?.can_review === true;
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
    upload.hidden = session?.can_import !== true;
    desk.hidden = false;
    byId("leave").hidden = publicDemo;
    login.hidden = true;
    if (orgWorkspace) byId("private-signin").textContent = "Private workspace";
    byId("token").value = "";
    const list = byId("cases");
    const picker = byId("case-select");
    list.replaceChildren();
    picker.replaceChildren();
    picker.disabled = cases.length < 2;
    picker.hidden = !cases.length;
    picker.labels[0].hidden = picker.hidden;
    byId("case-count").textContent = pager.hidden
      ? String(cases.length)
      : cases.length + " on this page";
    if (initialCaseError) {
      const placeholder = element("option", "Choose a case");
      placeholder.value = "";
      placeholder.disabled = placeholder.hidden = true;
      picker.append(placeholder);
    }

    for (const report of cases) {
      const option = element("option", report.id + " · " + report.title);
      option.value = report.id;
      picker.append(option);
      const button = element("button", undefined, "case-btn");
      button.dataset.caseId = report.id;
      button.append(element("small", report.id), element("span", report.title));
      button.addEventListener("click", () => openCase(report.id, button));
      const li = element("li");
      li.append(button);
      list.append(li);
    }
    if (context) displayCase(context);
    else if (initialCaseError) {
      selected = null;
      picker.value = "";
      showError(initialCaseError.message, () => openCase(target.id));
    } else {
      selected = null;
      clearCaseImages();
      byId("case-activity").replaceChildren();
      status.textContent = "";
      detail.textContent = "No cases yet.";
    }
  } catch (error) {
    if (view === generation) {
      showError(error.message, () => refresh(query, preferred));
    }
  } finally {
    endView(view);
  }
}

function resetReport(message = "") {
  reportBody = undefined;
  const form = byId("report-upload");
  form.reset();
  form.removeAttribute("aria-busy");
  byId("report-drop").classList.remove("dragging");
  byId("report-file").disabled = false;
  byId("report-submit").disabled = true;
  form.querySelector(":scope > .view-activity")?.remove();
  byId("report-clear").hidden = true;
  byId("report-clear").disabled = false;
  byId("report-name").textContent = "Drop a report here";
  byId("report-browse").textContent = "Choose file";
  status.textContent = message;
}

async function selectReport() {
  const input = byId("report-file");
  const file = input.files[0];
  reportBody = undefined;
  byId("report-submit").disabled = true;
  if (!file) return resetReport();
  if (!file.size || file.size > 1024 * 1024)
    return resetReport("Choose a JSON file between one byte and 1 MiB.");
  byId("report-name").textContent = file.name;
  byId("report-browse").textContent = "Replace file";
  byId("report-clear").hidden = false;
  status.textContent = "";
  const finish = startActivity(input.form, "Checking report");
  try {
    const body = await file.arrayBuffer();
    if (input.files[0] !== file) return;
    const report = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(body),
    );
    const keys = ["id", "title", "service", "revision", "component", "owner"];
    if (
      !report ||
      Object.keys(report).length !== keys.length ||
      !keys.every(
        (key) => typeof report[key] === "string" && report[key].trim(),
      )
    )
      return resetReport(
        "Choose one report with the six required metadata fields.",
      );
    reportBody = body;
    byId("report-submit").disabled = false;
    status.textContent = "Ready to import " + file.name;
  } catch {
    if (input.files[0] === file) resetReport("Choose a valid UTF-8 JSON file.");
  } finally {
    finish();
  }
}

async function uploadReport(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const input = byId("report-file");
  if (form.hidden || input.disabled || !reportBody) return;
  const session = sessionVersion;
  const view = generation;
  const file = input.files[0];
  const button = byId("report-submit");
  button.disabled = input.disabled = byId("report-clear").disabled = true;
  const finish = startActivity(form, "Importing report");
  form.setAttribute("aria-busy", "true");
  status.textContent = "";
  try {
    const response = await request("import/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: reportBody,
    });
    const saved = await response.json();
    if (session !== sessionVersion) return;
    resetReport("Imported " + saved.id);
    if (view !== generation) return;
    await refresh("?from=" + encodeURIComponent(saved.id), saved.id);
    if (session === sessionVersion && selected === saved.id)
      status.textContent = "Imported " + saved.id;
  } catch (error) {
    if (session !== sessionVersion) return;
    if (error.cause === 400) return resetReport(error.message);
    status.textContent = error.message;
  } finally {
    finish();
    if (session === sessionVersion && input.files[0] === file) {
      form.removeAttribute("aria-busy");
      input.disabled = byId("report-clear").disabled = false;
      button.disabled = !reportBody;
    }
  }
}

function ownerCurve(points, pooled) {
  const figure = element("figure", undefined, "owner-curve");
  figure.append(element("figcaption", "First-choice owner accuracy"));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 340 130");
  svg.setAttribute("aria-hidden", "true");
  function draw(tag, attributes, text) {
    const node = document.createElementNS(svg.namespaceURI, tag);
    for (const [name, value] of Object.entries(attributes))
      node.setAttribute(name, value);
    if (text !== undefined) node.textContent = text;
    svg.append(node);
  }
  const last = points[points.length - 1].training_cases || 1;
  const x = (point) => 45 + 285 * (point.training_cases / last);
  const y = (rate) => 102 - rate * 88;
  for (const rate of [0, 0.5, 1]) {
    draw("line", {
      x1: 45,
      x2: 330,
      y1: y(rate),
      y2: y(rate),
      class: "curve-grid",
    });
    draw(
      "text",
      { x: 36, y: y(rate) + 4, "text-anchor": "end" },
      rate * 100 + "%",
    );
  }
  for (const point of points)
    draw(
      "text",
      { x: x(point), y: 124, "text-anchor": "middle" },
      point.training_cases,
    );
  for (const key of pooled
    ? ["pooled_owner_top1", "owner_top1"]
    : ["owner_top1"]) {
    const line = points
      .map((point) => x(point) + "," + y(point[key]))
      .join(" ");
    draw("polyline", { points: line, class: key });
  }
  for (const point of points)
    draw("circle", {
      cx: x(point),
      cy: y(point.owner_top1),
      r: 3,
      class: "owner-dot",
    });
  const legend = element("div", undefined, "curve-key");
  legend.append(element("span", "Same revision"));
  if (pooled) legend.append(element("span", "Pooled feedback"));
  figure.append(svg, element("small", "Reviewed training cases"), legend);
  return figure;
}

function initializeConsole() {
  if (orgWorkspace) {
    document.title = "Organization workspace · Ichnix";
    document.querySelector(".workspace-intro h1").textContent =
      "Organization workspace";

    document
      .querySelector('.topnav a[href="/console"]')
      .removeAttribute("aria-current");
    const workspaceNav = byId("private-signin");
    workspaceNav.setAttribute("aria-current", "page");
    workspaceNav.textContent = "Private workspace";
  }
  document.querySelector('.topnav [aria-current="page"]').href = "#review";
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
      return resetReport("Choose a file instead of a folder.");
    }
    const files = event.dataTransfer.files;
    if (files.length !== 1) {
      return resetReport("Drop one file at a time.");
    }
    input.files = files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  input.addEventListener("change", selectReport);
  byId("report-clear").addEventListener("click", () => {
    resetReport();
    input.focus();
  });
  byId("report-upload").addEventListener("submit", uploadReport);
  const field = byId("token");
  const connect = byId("connect-submit");
  field.addEventListener("input", () => {
    connect.disabled = !field.value.trim();
  });
  login.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (connect.disabled) return;
    const session = ++sessionVersion;
    token = field.value.trim();
    connect.disabled = field.readOnly = true;
    try {
      await refresh();
    } finally {
      if (session === sessionVersion) {
        field.readOnly = false;
        connect.disabled = !field.value.trim();
      }
    }
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
    result.replaceChildren();
    const finish = startActivity(result, "Loading learning curve");
    result.setAttribute("aria-busy", "true");
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
      const pooled = curve.every(
        (point) =>
          Number.isFinite(point.pooled_owner_top1) &&
          Number.isFinite(point.pooled_ci_check_top1),
      );
      const scores = element("details", undefined, "curve-scores");
      scores.append(
        element("summary", "View exact scores"),
        scoreTable(
          "Feedback from the same component revision",
          "owner_top1",
          "ci_check_top1",
        ),
      );
      if (pooled) {
        scores.append(
          scoreTable(
            "Same feedback pooled across components",
            "pooled_owner_top1",
            "pooled_ci_check_top1",
          ),
        );
      }
      scores.append(
        element(
          "p",
          "Fixed catalog owner accuracy: " +
            percent.format(curve[0].catalog_owner_top1),
        ),
      );
      const explanation = element("div", undefined, "curve-description");
      explanation.append(
        element(
          "p",
          "How often does earlier feedback point to the right owner?",
        ),
        element(
          "p",
          "Each point uses the same " +
            curve[0].heldout_cases +
            " held-out synthetic cases. The graph ranks owners after learning from a growing set of reviewed cases.",
        ),
        scores,
      );
      result.replaceChildren(ownerCurve(curve, pooled), explanation);
    } catch {
      result.textContent = "Results unavailable. Close and reopen to retry.";
    } finally {
      result.removeAttribute("aria-busy");
      finish();
    }
  });

  async function initializeWorkspace() {
    // The dedicated organization route always needs a named credential.
    if (orgWorkspace) {
      login.hidden = false;
      return;
    }
    const view = beginView(
      "Connecting to workspace",
      byId("workspace-label").parentElement,
    );
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
      publicDemo = hostedPreview || session.public_demo === true;
      disclosedCase = publicDemo ? session.disclosed_case : null;
      evaluation.hidden = !publicDemo;
      if (publicDemo) {
        byId("workspace-label").textContent = disclosedCase
          ? "Read-only workspace · public disclosure and sample cases"
          : "Read-only demo · synthetic cases";
        selected ??= "IX-214";
        return refresh();
      }
      byId("workspace-label").textContent = "Private workspace";
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
