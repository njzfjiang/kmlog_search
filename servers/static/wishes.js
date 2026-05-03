const state = {
  status: "open",
  owner: "",
  scope: "",
  q: "",
  token: localStorage.getItem("kmlogApiToken") || "",
};

const apiToken = document.querySelector("#apiToken");
const wishForm = document.querySelector("#wishForm");
const wishText = document.querySelector("#wishText");
const wishOwner = document.querySelector("#wishOwner");
const wishScope = document.querySelector("#wishScope");
const wishPriority = document.querySelector("#wishPriority");
const wishTags = document.querySelector("#wishTags");
const formMessage = document.querySelector("#formMessage");
const statusSegment = document.querySelector("[data-filter='status']");
const ownerFilter = document.querySelector("#ownerFilter");
const scopeFilter = document.querySelector("#scopeFilter");
const keywordFilter = document.querySelector("#keywordFilter");
const wishList = document.querySelector("#wishList");
const resultCount = document.querySelector("#resultCount");
const emptyState = document.querySelector("#emptyState");

apiToken.value = state.token;

function headers() {
  const out = { "Content-Type": "application/json" };
  if (state.token) {
    out["X-API-Key"] = state.token;
  }
  return out;
}

async function apiFetch(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...headers(),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function wishQuery() {
  const params = new URLSearchParams({ limit: "100" });
  if (state.status) params.set("status", state.status);
  if (state.owner) params.set("owner", state.owner);
  if (state.scope) params.set("scope", state.scope);
  if (state.q) params.set("q", state.q);
  return params.toString();
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function tagPills(tags) {
  return (tags || "")
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean)
    .map((tag) => `<span class="pill">${escapeHtml(tag)}</span>`)
    .join("");
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function actionButtons(wish) {
  const actions = [];
  if (wish.status !== "done") actions.push(["done", "Done"]);
  if (wish.status !== "stale") actions.push(["stale", "Stale"]);
  if (wish.status !== "archived") actions.push(["archived", "Archive"]);
  if (wish.status !== "open") actions.push(["open", "Reopen"]);
  return actions
    .map(([status, label]) => (
      `<button class="action-button ${status}" type="button" data-id="${wish.id}" data-status="${status}">${label}</button>`
    ))
    .join("");
}

function renderWishes(wishes) {
  resultCount.textContent = String(wishes.length);
  emptyState.hidden = wishes.length > 0;
  wishList.innerHTML = wishes.map((wish) => `
    <article class="wish-card">
      <div class="wish-meta">
        <span class="pill owner">${escapeHtml(wish.owner)}</span>
        <span class="pill">${escapeHtml(wish.scope)}</span>
        <span class="pill status-${escapeHtml(wish.status)}">${escapeHtml(wish.status)}</span>
        <span>P${escapeHtml(wish.priority)}</span>
        <span>${escapeHtml(formatDate(wish.created_at))}</span>
      </div>
      <p class="wish-text">${escapeHtml(wish.text)}</p>
      <div class="wish-tags">${tagPills(wish.tags)}</div>
      <div class="wish-actions">${actionButtons(wish)}</div>
    </article>
  `).join("");
}

async function loadWishes() {
  try {
    const data = await apiFetch(`/wish?${wishQuery()}`, { method: "GET" });
    renderWishes(data.results || []);
    formMessage.textContent = "";
  } catch (error) {
    renderWishes([]);
    formMessage.textContent = error.message;
  }
}

async function addWish(event) {
  event.preventDefault();
  const text = wishText.value.trim();
  if (!text) return;

  const payload = {
    owner: wishOwner.value,
    scope: wishScope.value,
    text,
    priority: Number(wishPriority.value || 3),
    tags: wishTags.value.trim(),
    status: "open",
    source: "manual",
  };

  try {
    await apiFetch("/wish", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    wishForm.reset();
    wishPriority.value = "3";
    formMessage.textContent = "Added.";
    await loadWishes();
  } catch (error) {
    formMessage.textContent = error.message;
  }
}

async function setStatus(id, status) {
  try {
    await apiFetch(`/wish/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    await loadWishes();
  } catch (error) {
    formMessage.textContent = error.message;
  }
}

function setActiveStatus(value) {
  state.status = value;
  statusSegment.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.value === value);
  });
  loadWishes();
}

wishForm.addEventListener("submit", addWish);

apiToken.addEventListener("change", () => {
  state.token = apiToken.value.trim();
  localStorage.setItem("kmlogApiToken", state.token);
  loadWishes();
});

statusSegment.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (button) setActiveStatus(button.dataset.value);
});

ownerFilter.addEventListener("change", () => {
  state.owner = ownerFilter.value;
  loadWishes();
});

scopeFilter.addEventListener("change", () => {
  state.scope = scopeFilter.value;
  loadWishes();
});

let keywordTimer;
keywordFilter.addEventListener("input", () => {
  clearTimeout(keywordTimer);
  keywordTimer = setTimeout(() => {
    state.q = keywordFilter.value.trim();
    loadWishes();
  }, 180);
});

wishList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-id][data-status]");
  if (button) {
    setStatus(button.dataset.id, button.dataset.status);
  }
});

loadWishes();
