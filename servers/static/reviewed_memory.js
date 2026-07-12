const state = {
  token: localStorage.getItem("kmlogApiToken") || "",
  mode: "list",
};

const apiToken = document.querySelector("#apiToken");
const statusFilter = document.querySelector("#statusFilter");
const domainFilter = document.querySelector("#domainFilter");
const functionFilter = document.querySelector("#functionFilter");
const primaryMotherFilter = document.querySelector("#primaryMotherFilter");
const secondaryMotherFilter = document.querySelector("#secondaryMotherFilter");
const keywordFilter = document.querySelector("#keywordFilter");
const limitInput = document.querySelector("#limitInput");
const includeSources = document.querySelector("#includeSources");
const includeExpired = document.querySelector("#includeExpired");
const loadButton = document.querySelector("#loadButton");
const messagePkInput = document.querySelector("#messagePkInput");
const messageIdInput = document.querySelector("#messageIdInput");
const lookupButton = document.querySelector("#lookupButton");
const clearLookupButton = document.querySelector("#clearLookupButton");
const message = document.querySelector("#message");
const resultCount = document.querySelector("#resultCount");
const modeLabel = document.querySelector("#modeLabel");
const itemList = document.querySelector("#itemList");
const emptyState = document.querySelector("#emptyState");

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

function checked(target) {
  return Boolean(target && target.checked);
}

if (apiToken) apiToken.value = state.token;

function headers() {
  const out = { "Accept": "application/json" };
  if (state.token) out["X-API-Key"] = state.token;
  return out;
}

async function apiFetch(path, options = {}) {
  const method = options.method || "GET";
  const separator = path.includes("?") ? "&" : "?";
  const response = await fetch(`${path}${separator}_=${Date.now()}`, {
    ...options,
    method,
    cache: "no-store",
    headers: {
      ...headers(),
      "Cache-Control": "no-cache",
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const text = await response.text();
    throw new Error(`Expected JSON, got ${response.status}: ${text.replace(/\s+/g, " ").slice(0, 140)}`);
  }
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `${response.status} ${response.statusText}`);
  return body;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 19);
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function jsonList(value) {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function listQuery() {
  const params = new URLSearchParams({
    limit: inputValue(limitInput, "50") || "50",
    include_sources: checked(includeSources) ? "true" : "false",
    include_expired: checked(includeExpired) ? "true" : "false",
  });
  const status = inputValue(statusFilter);
  const domain = inputValue(domainFilter).trim();
  const memoryFunction = inputValue(functionFilter).trim();
  const primaryMother = inputValue(primaryMotherFilter).trim();
  const secondaryMother = inputValue(secondaryMotherFilter).trim();
  const q = inputValue(keywordFilter).trim();
  if (status) params.set("status", status);
  if (domain) params.set("domain", domain);
  if (memoryFunction) params.set("function", memoryFunction);
  if (primaryMother) params.set("primary_mother", primaryMother);
  if (secondaryMother) params.set("secondary_mother", secondaryMother);
  if (q) params.set("q", q);
  return params.toString();
}

function lookupQuery() {
  const params = new URLSearchParams({
    limit: inputValue(limitInput, "50") || "50",
    include_expired: checked(includeExpired) ? "true" : "false",
  });
  const messagePk = inputValue(messagePkInput).trim();
  const messageId = inputValue(messageIdInput).trim();
  const status = inputValue(statusFilter);
  if (messagePk) params.set("message_pk", messagePk);
  if (messageId) params.set("message_id", messageId);
  if (status) params.set("status", status);
  return params.toString();
}

function sourcePills(item) {
  const sources = item.sources || [];
  if (sources.length) {
    return sources.map((source) => {
      const label = source.source_role === "message"
        ? `message ${source.message_pk || source.message_id || ""}`
        : `candidate ${source.candidate_id || ""}`;
      return `<span class="pill">${escapeHtml(label.trim())}</span>`;
    }).join("");
  }
  const candidateIds = jsonList(item.source_candidate_ids_json);
  const messageIds = jsonList(item.source_message_ids_json);
  return [
    ...candidateIds.map((id) => `<span class="pill">candidate ${escapeHtml(id)}</span>`),
    ...messageIds.map((id) => `<span class="pill">message ${escapeHtml(id)}</span>`),
  ].join("");
}

function renderItems(items) {
  setText(resultCount, `${items.length} item${items.length === 1 ? "" : "s"}`);
  setHidden(emptyState, items.length > 0);
  if (!itemList) return;
  setHtml(itemList, items.map((item) => `
    <article class="item-card ${escapeHtml(item.status || "")}">
      <div class="item-meta">
        <span class="pill">#${escapeHtml(item.id)}</span>
        <span class="pill">${escapeHtml(item.domain || "domain")}</span>
        <span class="pill">${escapeHtml(item.function || "function")}</span>
        <span class="pill status-${escapeHtml(item.status || "active")}">${escapeHtml(item.status || "active")}</span>
        <span>I${escapeHtml(item.importance || "")}</span>
        <span>${escapeHtml(item.confidence || "")}</span>
        <span>${escapeHtml(item.explicitness || "")}</span>
      </div>
      <h3>${escapeHtml(item.title)}</h3>
      <p class="item-content">${escapeHtml(item.content)}</p>
      ${item.evidence ? `<p class="item-evidence">${escapeHtml(item.evidence)}</p>` : ""}
      <div class="item-submeta">
        <span>Primary: ${escapeHtml(item.primary_mother || "")}</span>
        <span>Secondary: ${escapeHtml(item.secondary_mother || "")}</span>
        <span>Reviewer: ${escapeHtml(item.reviewer || "")}</span>
        <span>Reviewed: ${escapeHtml(formatDate(item.reviewed_at))}</span>
        ${item.expires_at ? `<span>Expires: ${escapeHtml(formatDate(item.expires_at))}</span>` : ""}
      </div>
      <div class="source-list">${sourcePills(item)}</div>
    </article>
  `).join(""));
}

async function loadItems() {
  state.mode = "list";
  try {
    setText(message, "Loading...");
    setText(modeLabel, "Filtered list");
    const data = await apiFetch(`reviewed_memory_items?${listQuery()}`);
    renderItems(data.results || []);
    setText(message, "");
  } catch (error) {
    renderItems([]);
    setText(message, error.message);
  }
}

async function lookupByMessage() {
  const messagePk = inputValue(messagePkInput).trim();
  const messageId = inputValue(messageIdInput).trim();
  if (!messagePk && !messageId) {
    setText(message, "Enter message_pk or message_id.");
    return;
  }
  state.mode = "lookup";
  try {
    setText(message, "Looking up source...");
    setText(modeLabel, "Message source lookup");
    const data = await apiFetch(`reviewed_memory/by_message?${lookupQuery()}`);
    renderItems(data.results || []);
    setText(message, "");
  } catch (error) {
    renderItems([]);
    setText(message, error.message);
  }
}

function clearLookup() {
  if (messagePkInput) messagePkInput.value = "";
  if (messageIdInput) messageIdInput.value = "";
  loadItems();
}

if (loadButton) loadButton.addEventListener("click", loadItems);
if (lookupButton) lookupButton.addEventListener("click", lookupByMessage);
if (clearLookupButton) clearLookupButton.addEventListener("click", clearLookup);

if (apiToken) {
  apiToken.addEventListener("change", () => {
    state.token = apiToken.value.trim();
    localStorage.setItem("kmlogApiToken", state.token);
    if (state.mode === "lookup") {
      lookupByMessage();
    } else {
      loadItems();
    }
  });
}

let filterTimer;
[statusFilter, domainFilter, functionFilter, primaryMotherFilter, secondaryMotherFilter, keywordFilter, limitInput, includeSources, includeExpired]
  .filter(Boolean)
  .forEach((input) => {
    const eventName = input.type === "checkbox" || input.tagName === "SELECT" ? "change" : "input";
    input.addEventListener(eventName, () => {
      clearTimeout(filterTimer);
      filterTimer = setTimeout(loadItems, 180);
    });
  });

loadItems();
