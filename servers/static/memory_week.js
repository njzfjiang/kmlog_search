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
const acceptedCount = document.querySelector("#acceptedCount");
const rejectedCount = document.querySelector("#rejectedCount");
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

apiToken.value = state.token;
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
  startDate.value = isoDate(start);
  endDate.value = isoDate(end);
}

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function queryString() {
  const params = new URLSearchParams({
    start_date: startDate.value,
    end_date: endDate.value,
    limit: "160",
    raw_limit: "1200",
  });
  if (statusFilter.value) params.set("status", statusFilter.value);
  if (domainFilter.value.trim()) params.set("domain", domainFilter.value.trim());
  if (functionFilter.value.trim()) params.set("function", functionFilter.value.trim());
  if (keywordFilter.value.trim()) params.set("q", keywordFilter.value.trim());
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
  message.textContent = `Saving ${group.candidate_ids.length} candidate(s)...`;
  try {
    await Promise.all(group.candidate_ids.map((candidateId) => updateCandidateStatus(candidateId, status)));
    message.textContent = `Saved status: ${status}`;
    if (statusFilter.value && statusFilter.value !== status) {
      await loadWeeklyCandidates();
    } else {
      render();
    }
  } catch (error) {
    group.canonical.status = previous;
    message.textContent = error.message;
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
  const max = Math.max(1, ...counts.map((entry) => entry[1]));
  target.innerHTML = counts.slice(0, 8).map(([label, count]) => `
    <div class="bar-row">
      <span>${escapeHtml(label)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.round((count / max) * 100)}%"></div></div>
      <strong>${count}</strong>
    </div>
  `).join("");
}

function renderCards() {
  emptyState.hidden = state.groups.length > 0;
  candidateList.innerHTML = state.groups.map((group) => {
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
  }).join("");
}

function render() {
  const statuses = state.groups.map((group) => statusFor(group));
  acceptedCount.textContent = String(statuses.filter((value) => value === "accepted").length);
  rejectedCount.textContent = String(statuses.filter((value) => value === "rejected").length);
  renderBars(domainBars, countBy("domain"));
  renderBars(functionBars, countBy("function"));
  renderCards();
}

async function loadWeeklyCandidates() {
  if (!startDate.value || !endDate.value) return;
  try {
    message.textContent = "Loading...";
    const data = await apiFetch(`weekly_memory_candidates?${queryString()}`);
    state.groups = data.groups || [];
    rawCount.textContent = String(data.total_raw || 0);
    groupCount.textContent = String(data.total_groups || 0);
    message.textContent = "";
    render();
  } catch (error) {
    state.groups = [];
    rawCount.textContent = "0";
    groupCount.textContent = "0";
    message.textContent = error.message;
    render();
  }
}

function markdownReport() {
  const kept = state.groups.filter((group) => statusFor(group) !== "rejected");
  const lines = [
    `# Memory Candidates ${startDate.value} to ${endDate.value}`,
    "",
    `Raw: ${rawCount.textContent} / Deduped: ${groupCount.textContent}`,
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

loadButton.addEventListener("click", loadWeeklyCandidates);
exportButton.addEventListener("click", () => {
  exportText.value = markdownReport();
  exportDialog.showModal();
});

apiToken.addEventListener("change", () => {
  state.token = apiToken.value.trim();
  localStorage.setItem("kmlogApiToken", state.token);
  loadWeeklyCandidates();
});

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

function openPromoteDialog(idsText) {
  const firstId = Number(idsText.split(",")[0]);
  const group = groupByCandidateId(firstId);
  if (!group) return;
  const item = group.canonical;
  state.activePromoteGroup = group;
  promoteCandidateIds.value = idsText;
  promoteTitle.value = item.label || "";
  promoteContent.value = item.evidence || item.label || "";
  promoteEvidence.value = item.evidence || "";
  promoteDomain.value = item.domain || "";
  promoteFunction.value = item.function || "";
  promotePrimaryMother.value = item.primary_mother || "";
  promoteSecondaryMother.value = item.secondary_mother || "";
  promoteImportance.value = item.importance || "";
  promoteConfidence.value = item.confidence || "";
  promoteExplicitness.value = "edited_by_human";
  promoteReviewer.value = promoteReviewer.value || "human";
  promoteDialog.showModal();
}

function promotePayload() {
  const payload = {
    candidate_ids: promoteCandidateIds.value.split(",").map((value) => Number(value.trim())).filter(Boolean),
    title: promoteTitle.value.trim(),
    content: promoteContent.value.trim(),
    evidence: promoteEvidence.value.trim(),
    domain: promoteDomain.value.trim(),
    function: promoteFunction.value.trim(),
    primary_mother: promotePrimaryMother.value.trim() || null,
    secondary_mother: promoteSecondaryMother.value.trim() || null,
    confidence: promoteConfidence.value || null,
    explicitness: promoteExplicitness.value,
    reviewer: promoteReviewer.value.trim() || "human",
  };
  if (promoteImportance.value) payload.importance = Number(promoteImportance.value);
  return payload;
}

promoteSubmit.addEventListener("click", async () => {
  if (!promoteForm.reportValidity()) return;
  const payload = promotePayload();
  try {
    promoteSubmit.disabled = true;
    message.textContent = "Promoting candidate...";
    await apiFetch("memory_candidates/promote", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    promoteDialog.close();
    message.textContent = "Candidate promoted.";
    await loadWeeklyCandidates();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    promoteSubmit.disabled = false;
  }
});

loadWeeklyCandidates();
