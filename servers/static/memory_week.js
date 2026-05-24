const state = {
  groups: [],
  token: localStorage.getItem("kmlogApiToken") || "",
  reviews: JSON.parse(localStorage.getItem("kmlogWeeklyMemoryReviews") || "{}"),
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
const keepCount = document.querySelector("#keepCount");
const dropCount = document.querySelector("#dropCount");
const domainBars = document.querySelector("#domainBars");
const functionBars = document.querySelector("#functionBars");
const candidateList = document.querySelector("#candidateList");
const emptyState = document.querySelector("#emptyState");
const message = document.querySelector("#message");
const exportDialog = document.querySelector("#exportDialog");
const exportText = document.querySelector("#exportText");

apiToken.value = state.token;
setDefaultWeek();

function headers() {
  const out = { "Accept": "application/json" };
  if (state.token) out["X-API-Key"] = state.token;
  return out;
}

async function apiFetch(path) {
  const separator = path.includes("?") ? "&" : "?";
  const response = await fetch(`${path}${separator}_=${Date.now()}`, {
    cache: "no-store",
    headers: {
      ...headers(),
      "Cache-Control": "no-cache",
    },
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

function reviewKey(group) {
  return `${startDate.value}:${endDate.value}:${group.dedupe_key}`;
}

function reviewFor(group) {
  return state.reviews[reviewKey(group)] || "";
}

function setReview(groupKey, value) {
  if (value) {
    state.reviews[groupKey] = value;
  } else {
    delete state.reviews[groupKey];
  }
  localStorage.setItem("kmlogWeeklyMemoryReviews", JSON.stringify(state.reviews));
  render();
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
    const key = reviewKey(group);
    const review = reviewFor(group);
    const dates = group.date_keys.join(", ");
    return `
      <article class="candidate-card ${escapeHtml(review)}">
        <div class="candidate-meta">
          <span class="pill">${escapeHtml(item.domain || "domain")}</span>
          <span class="pill">${escapeHtml(item.function || "function")}</span>
          <span class="pill">x${escapeHtml(group.count)}</span>
          <span>${escapeHtml(dates)}</span>
        </div>
        <h3>${escapeHtml(item.label)}</h3>
        <p>${escapeHtml(item.evidence)}</p>
        <div class="candidate-submeta">
          <span>${escapeHtml(item.primary_mother || "")}</span>
          <span>${escapeHtml(item.secondary_mother || "")}</span>
          <span>I${escapeHtml(item.importance)} C${escapeHtml(item.confidence)}</span>
        </div>
        <div class="candidate-actions">
          <button type="button" data-review-key="${escapeHtml(key)}" data-review="keep" class="${review === "keep" ? "active" : ""}">Keep</button>
          <button type="button" data-review-key="${escapeHtml(key)}" data-review="drop" class="${review === "drop" ? "active" : ""}">Drop</button>
          <button type="button" data-review-key="${escapeHtml(key)}" data-review="">Clear</button>
        </div>
      </article>
    `;
  }).join("");
}

function render() {
  const reviews = state.groups.map((group) => reviewFor(group));
  keepCount.textContent = String(reviews.filter((value) => value === "keep").length);
  dropCount.textContent = String(reviews.filter((value) => value === "drop").length);
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
  const kept = state.groups.filter((group) => reviewFor(group) !== "drop");
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
  const button = event.target.closest("[data-review-key]");
  if (!button) return;
  setReview(button.dataset.reviewKey, button.dataset.review);
});

loadWeeklyCandidates();
