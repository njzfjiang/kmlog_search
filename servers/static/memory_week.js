const state = {
  groups: [],
  token: localStorage.getItem("kmlogApiToken") || "",
  activePromoteGroup: null,
};

const apiToken = document.querySelector("#apiToken");
const startDate = document.querySelector("#startDate");
const endDate = document.querySelector("#endDate");
const statusFilter = document.querySelector("#statusFilter");
const domainFilter = document.querySelector("#domainFilter");
const functionFilter = document.querySelector("#functionFilter");
const keywordFilter = document.querySelector("#keywordFilter");
const loadButton = document.querySelector("#loadButton");
const exportButton = document.querySelector("#exportButton");
const rawCount = document.querySelector("#rawCount");
const groupCount = document.querySelector("#groupCount");
const acceptedCount = document.querySelector("#acceptedCount") || document.querySelector("#keepCount");
const rejectedCount = document.querySelector("#rejectedCount") || document.querySelector("#dropCount");
const domainBars = document.querySelector("#domainBars");
const functionBars = document.querySelector("#functionBars");
const candidateList = document.querySelector("#candidateList");
const emptyState = document.querySelector("#emptyState");
const message = document.querySelector("#message");
const exportDialog = document.querySelector("#exportDialog");
const exportText = document.querySelector("#exportText");
const promoteDialog = document.querySelector("#promoteDialog");
const promoteForm = document.querySelector("#promoteForm");
const promoteCandidateIds = document.querySelector("#promoteCandidateIds");
const promoteTitle = document.querySelector("#promoteTitle");
const promoteContent = document.querySelector("#promoteContent");
const promoteEvidence = document.querySelector("#promoteEvidence");
const promoteDomain = document.querySelector("#promoteDomain");
const promoteFunction = document.querySelector("#promoteFunction");
const promotePrimaryMother = document.querySelector("#promotePrimaryMother");
const promoteSecondaryMother = document.querySelector("#promoteSecondaryMother");
const promoteImportance = document.querySelector("#promoteImportance");
const promoteConfidence = document.querySelector("#promoteConfidence");
const promoteExplicitness = document.querySelector("#promoteExplicitness");
const promoteReviewer = document.querySelector("#promoteReviewer");
const promoteSubmit = document.querySelector("#promoteSubmit");

function setText(target, value) {
  if (target) target.textContent = value;
}

function setHtml(target, value) {
  if (target) target.innerHTML = value;
}

function setHidden(target, value) {
  if (target) target.hidden = value;
}

function inputValue(target, fallback = "") {
  return target ? target.value : fallback;
}

if (apiToken) apiToken.value = state.token;
setDefaultWeek();

function headers() {
  const out = { "Accept": "application/json" };
  if (state.token) out["X-API-Key"] = state.token;
  return out;
}

async function apiFetch(path, options = {}) {
  const separator = path.includes("?") ? "&" : "?";
  const method = options.method || "GET";
  const requestHeaders = {
    ...headers(),
    "Cache-Control": "no-cache",
    ...(options.headers || {}),
  };
  if (options.body && !requestHeaders["Content-Type"]) {
    requestHeaders["Content-Type"] = "application/json";
  }
  const response = await fetch(`${path}${separator}_=${Date.now()}`, {
    method,
    cache: "no-store",
    headers: requestHeaders,
    body: options.body,
  });
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const text = await response.text();
    throw new Error(`Expected JSON, got ${response.status}: ${text.slice(0, 120)}`);
  }
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `${response.status} ${response.statusText}`);
  return body;
}

function setDefaultWeek() {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 6);
  if (startDate) startDate.value = isoDate(start);
  if (endDate) endDate.value = isoDate(end);
}

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function queryString() {
  const params = new URLSearchParams({
    start_date: inputValue(startDate),
    end_date: inputValue(endDate),
    limit: "160",
    raw_limit: "1200",
  });
  const status = inputValue(statusFilter);
  const domain = inputValue(domainFilter).trim();
  const memoryFunction = inputValue(functionFilter).trim();
  const keyword = inputValue(keywordFilter).trim();
  if (status) params.set("status", status);
  if (domain) params.set("domain", domain);
  if (memoryFunction) params.set("function", memoryFunction);
  if (keyword) params.set("q", keyword);
  return params.toString();
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusFor(group) {
  return group.canonical.status || "candidate";
}

function isTerminalStatus(status) {
  return ["rejected", "promoted", "superseded", "merged"].includes(status);
}

function statusClass(status) {
  if (status === "accepted") return "accepted";
  if (status === "rejected") return "rejected";
  if (status === "deferred") return "deferred";
  if (status === "promoted") return "promoted";
  return "";
}

function groupByCandidateId(candidateId) {
  return state.groups.find((group) => group.candidate_ids.includes(Number(candidateId)));
}

function setGroupStatusLocal(group, status) {
  group.canonical.status = status;
  render();
}

async function updateCandidateStatus(candidateId, status) {
  return apiFetch(`memory_candidates/${candidateId}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

async function updateGroupStatus(group, status) {
  const previous = group.canonical.status;
  setGroupStatusLocal(group, status);
  setText(message, `Saving ${group.candidate_ids.length} candidate(s)...`);
  try {
    await Promise.all(group.candidate_ids.map((candidateId) => updateCandidateStatus(candidateId, status)));
    setText(message, `Saved status: ${status}`);
    if (inputValue(statusFilter) && inputValue(statusFilter) !== status) {
      await loadWeeklyCandidates();
    } else {
      render();
    }
  } catch (error) {
    group.canonical.status = previous;
    setText(message, error.message);
    render();
  }
}

function countBy(path) {
  const counts = new Map();
  state.groups.forEach((group) => {
    const value = group.canonical[path] || "uncategorized";
    counts.set(value, (counts.get(value) || 0) + group.count);
  });
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function renderBars(target, counts) {
  if (!target) return;
  const max = Math.max(1, ...counts.map((entry) => entry[1]));
  setHtml(target, counts.slice(0, 8).map(([label, count]) => `
    <div class="bar-row">
      <span>${escapeHtml(label)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.round((count / max) * 100)}%"></div></div>
      <strong>${count}</strong>
    </div>
  `).join(""));
}

function renderCards() {
  setHidden(emptyState, state.groups.length > 0);
  if (!candidateList) return;
  setHtml(candidateList, state.groups.map((group) => {
    const item = group.canonical;
    const status = statusFor(group);
    const dates = group.date_keys.join(", ");
    const ids = group.candidate_ids.join(",");
    return `
      <article class="candidate-card ${escapeHtml(statusClass(status))}">
        <div class="candidate-meta">
          <span class="pill">${escapeHtml(item.domain || "domain")}</span>
          <span class="pill">${escapeHtml(item.function || "function")}</span>
          <span class="pill status-pill">${escapeHtml(status)}</span>
          <span class="pill">x${escapeHtml(group.count)}</span>
          <span class="pill">#${escapeHtml(ids)}</span>
          <span>${escapeHtml(dates)}</span>
        </div>
        <h3>${escapeHtml(item.label)}</h3>
        <p>${escapeHtml(item.evidence)}</p>
        <div class="candidate-submeta">
          <span>${escapeHtml(item.primary_mother || "")}</span>
          <span>${escapeHtml(item.secondary_mother || "")}</span>
          <span>I${escapeHtml(item.importance)} C${escapeHtml(item.confidence)}</span>
          <span>${escapeHtml(item.target_layer || "")}</span>
        </div>
        <div class="candidate-actions">
          <button type="button" data-candidate-ids="${escapeHtml(ids)}" data-status="accepted" class="${status === "accepted" ? "active" : ""}">Accept</button>
          <button type="button" data-candidate-ids="${escapeHtml(ids)}" data-status="rejected" class="${status === "rejected" ? "active" : ""}">Reject</button>
          <button type="button" data-candidate-ids="${escapeHtml(ids)}" data-status="deferred" class="${status === "deferred" ? "active" : ""}">Defer</button>
          <button type="button" data-candidate-ids="${escapeHtml(ids)}" data-status="candidate" class="${status === "candidate" ? "active" : ""}">Reset</button>
          <button type="button" data-promote-ids="${escapeHtml(ids)}" ${isTerminalStatus(status) ? "disabled" : ""}>Promote</button>
        </div>
      </article>
    `;
  }).join(""));
}

function render() {
  const statuses = state.groups.map((group) => statusFor(group));
  setText(acceptedCount, String(statuses.filter((value) => value === "accepted").length));
  setText(rejectedCount, String(statuses.filter((value) => value === "rejected").length));
  renderBars(domainBars, countBy("domain"));
  renderBars(functionBars, countBy("function"));
  renderCards();
}

async function loadWeeklyCandidates() {
  if (!inputValue(startDate) || !inputValue(endDate)) return;
  try {
    setText(message, "Loading...");
    const data = await apiFetch(`weekly_memory_candidates?${queryString()}`);
    state.groups = data.groups || [];
    setText(rawCount, String(data.total_raw || 0));
    setText(groupCount, String(data.total_groups || 0));
    setText(message, "");
    render();
  } catch (error) {
    state.groups = [];
    setText(rawCount, "0");
    setText(groupCount, "0");
    setText(message, error.message);
    render();
  }
}

function markdownReport() {
  const kept = state.groups.filter((group) => statusFor(group) !== "rejected");
  const lines = [
    `# Memory Candidates ${inputValue(startDate)} to ${inputValue(endDate)}`,
    "",
    `Raw: ${rawCount ? rawCount.textContent : "0"} / Deduped: ${groupCount ? groupCount.textContent : "0"}`,
    "",
  ];

  kept.forEach((group) => {
    const item = group.canonical;
    lines.push(`## ${item.label}`);
    lines.push(`- Dates: ${group.date_keys.join(", ")}`);
    lines.push(`- Group: ${item.domain || ""} / ${item.function || ""}`);
    lines.push(`- Mothers: ${item.primary_mother || ""} / ${item.secondary_mother || ""}`);
    lines.push(`- Score: importance ${item.importance}, confidence ${item.confidence}`);
    lines.push(`- Status: ${item.status || "candidate"}`);
    lines.push(`- Candidate IDs: ${group.candidate_ids.join(", ")}`);
    lines.push(`- Evidence: ${item.evidence || ""}`);
    lines.push("");
  });

  return lines.join("\n");
}

if (loadButton) loadButton.addEventListener("click", loadWeeklyCandidates);
if (exportButton) {
  exportButton.addEventListener("click", () => {
    if (exportText) exportText.value = markdownReport();
    if (exportDialog) exportDialog.showModal();
  });
}

if (apiToken) {
  apiToken.addEventListener("change", () => {
    state.token = apiToken.value.trim();
    localStorage.setItem("kmlogApiToken", state.token);
    loadWeeklyCandidates();
  });
}

if (candidateList) {
  candidateList.addEventListener("click", (event) => {
    const promoteButton = event.target.closest("[data-promote-ids]");
    if (promoteButton) {
      openPromoteDialog(promoteButton.dataset.promoteIds);
      return;
    }
    const button = event.target.closest("[data-candidate-ids][data-status]");
    if (!button) return;
    const firstId = Number(button.dataset.candidateIds.split(",")[0]);
    const group = groupByCandidateId(firstId);
    if (!group) return;
    updateGroupStatus(group, button.dataset.status);
  });
}

function openPromoteDialog(idsText) {
  if (!promoteDialog) {
    setText(message, "Promote dialog is not available. Refresh the page assets and try again.");
    return;
  }
  const firstId = Number(idsText.split(",")[0]);
  const group = groupByCandidateId(firstId);
  if (!group) return;
  const item = group.canonical;
  state.activePromoteGroup = group;
  if (promoteCandidateIds) promoteCandidateIds.value = idsText;
  if (promoteTitle) promoteTitle.value = item.label || "";
  if (promoteContent) promoteContent.value = item.evidence || item.label || "";
  if (promoteEvidence) promoteEvidence.value = item.evidence || "";
  if (promoteDomain) promoteDomain.value = item.domain || "";
  if (promoteFunction) promoteFunction.value = item.function || "";
  if (promotePrimaryMother) promotePrimaryMother.value = item.primary_mother || "";
  if (promoteSecondaryMother) promoteSecondaryMother.value = item.secondary_mother || "";
  if (promoteImportance) promoteImportance.value = item.importance || "";
  if (promoteConfidence) promoteConfidence.value = item.confidence || "";
  if (promoteExplicitness) promoteExplicitness.value = "edited_by_human";
  if (promoteReviewer) promoteReviewer.value = promoteReviewer.value || "human";
  promoteDialog.showModal();
}

function promotePayload() {
  const payload = {
    candidate_ids: inputValue(promoteCandidateIds).split(",").map((value) => Number(value.trim())).filter(Boolean),
    title: inputValue(promoteTitle).trim(),
    content: inputValue(promoteContent).trim(),
    evidence: inputValue(promoteEvidence).trim(),
    domain: inputValue(promoteDomain).trim(),
    function: inputValue(promoteFunction).trim(),
    primary_mother: inputValue(promotePrimaryMother).trim() || null,
    secondary_mother: inputValue(promoteSecondaryMother).trim() || null,
    confidence: inputValue(promoteConfidence) || null,
    explicitness: inputValue(promoteExplicitness),
    reviewer: inputValue(promoteReviewer).trim() || "human",
  };
  if (inputValue(promoteImportance)) payload.importance = Number(inputValue(promoteImportance));
  return payload;
}

if (promoteSubmit) {
  promoteSubmit.addEventListener("click", async () => {
    if (promoteForm && !promoteForm.reportValidity()) return;
    const payload = promotePayload();
    try {
      promoteSubmit.disabled = true;
      setText(message, "Promoting candidate...");
      await apiFetch("memory_candidates/promote", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (promoteDialog) promoteDialog.close();
      setText(message, "Candidate promoted.");
      await loadWeeklyCandidates();
    } catch (error) {
      setText(message, error.message);
    } finally {
      promoteSubmit.disabled = false;
    }
  });
}

loadWeeklyCandidates();
