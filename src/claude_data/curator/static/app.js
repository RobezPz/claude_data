const state = {
  library: { sources: [], pairs: [] },
  collections: [],
  revision: 0,
  currentCollectionId: null,
  selectedPairId: null,
  filters: { search: "", source: "", from: "", to: "" },
};

const $ = (id) => document.getElementById(id);

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body && body.error) message = body.error;
    } catch (_error) {
      // keep the status text
    }
    throw new Error(message);
  }
  return response.json();
}

function setStatus(text) {
  $("save-status").textContent = text;
}

let errorTimer = null;
function showError(message) {
  const existing = document.querySelector(".error-banner");
  if (existing) existing.remove();
  const banner = document.createElement("div");
  banner.className = "error-banner";
  banner.textContent = message;
  document.body.appendChild(banner);
  clearTimeout(errorTimer);
  errorTimer = setTimeout(() => banner.remove(), 6000);
}

function sourceById(sourceId) {
  return state.library.sources.find((source) => source.source_id === sourceId) || null;
}

function sourceTitle(sourceId) {
  const source = sourceById(sourceId);
  return source ? source.title : sourceId;
}

function pairById(pairId) {
  return state.library.pairs.find((pair) => pair.pair_id === pairId) || null;
}

function formatDate(iso, withTime = false) {
  if (!iso) return "unknown date";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const options = withTime
    ? { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "short", day: "numeric" };
  return date.toLocaleString(undefined, options);
}

function makeBadge(text, className = "badge") {
  const badge = document.createElement("span");
  badge.className = className;
  badge.textContent = text;
  return badge;
}

function filteredPairs() {
  const query = state.filters.search.trim().toLowerCase();
  return state.library.pairs
    .filter((pair) => {
      if (state.filters.source && pair.source_id !== state.filters.source) return false;
      const date = (pair.date || "").slice(0, 10);
      if (state.filters.from && date && date < state.filters.from) return false;
      if (state.filters.to && date && date > state.filters.to) return false;
      if (!query) return true;
      return (
        (pair.question_text || "").toLowerCase().includes(query) ||
        (pair.answer_text || "").toLowerCase().includes(query)
      );
    })
    .sort((left, right) => {
      if (left.date === right.date) return right.pair_id.localeCompare(left.pair_id);
      return left.date < right.date ? 1 : -1;
    });
}

function renderLibrary() {
  const container = $("library");
  container.replaceChildren();
  const pairs = filteredPairs();
  $("library-count").textContent = `${pairs.length} / ${state.library.pairs.length}`;
  if (!pairs.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No pairs match these filters.";
    container.appendChild(empty);
    return;
  }
  for (const pair of pairs) {
    const row = document.createElement("div");
    row.className = "pair-row";
    row.dataset.pairId = pair.pair_id;
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-selected", pair.pair_id === state.selectedPairId ? "true" : "false");
    if (pair.pair_id === state.selectedPairId) row.classList.add("selected");
    row.addEventListener("click", () => selectPair(pair.pair_id));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectPair(pair.pair_id);
      }
    });

    const meta = document.createElement("div");
    meta.className = "pair-meta";
    const date = document.createElement("span");
    date.className = "pair-date";
    date.textContent = formatDate(pair.date);
    date.title = pair.date;
    meta.appendChild(date);
    const source = sourceById(pair.source_id);
    meta.appendChild(
      makeBadge(source ? source.provider : "source", "badge provider")
    );
    if (pair.has_branches) meta.appendChild(makeBadge("branches", "badge warn"));
    if (pair.missing_answer) meta.appendChild(makeBadge("no answer", "badge warn"));

    const question = document.createElement("div");
    question.className = "pair-question";
    question.textContent = pair.question_text || "(no question text)";

    row.append(meta, question);
    container.appendChild(row);
  }
}

function renderCollectionSelect() {
  const select = $("collection-select");
  select.replaceChildren();
  if (!state.collections.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No collections";
    select.appendChild(option);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const collection of state.collections) {
    const option = document.createElement("option");
    option.value = collection.collection_id;
    option.textContent = collection.title;
    select.appendChild(option);
  }
  if (!state.currentCollectionId || !state.collections.some((c) => c.collection_id === state.currentCollectionId)) {
    state.currentCollectionId = state.collections[0].collection_id;
  }
  select.value = state.currentCollectionId;
}

function renderCollections() {
  renderCollectionSelect();
  const container = $("collections");
  const empty = $("collection-empty");
  container.replaceChildren();
  const collection = state.collections.find(
    (candidate) => candidate.collection_id === state.currentCollectionId
  );
  if (!collection) {
    $("collection-title").textContent = "Collections";
    empty.textContent = "Select a collection to see its pairs.";
    empty.hidden = false;
    return;
  }
  $("collection-title").textContent = collection.title;
  empty.textContent = "This collection is empty.";
  empty.hidden = collection.items.length > 0;
  if (collection.description) {
    const description = document.createElement("p");
    description.className = "muted";
    description.textContent = collection.description;
    container.appendChild(description);
  }
  for (const item of collection.items) {
    const pair = pairById(item.pair_id);
    const card = document.createElement("div");
    card.className = "collection-item" + (pair ? "" : " missing");
    card.dataset.pairId = item.pair_id;

    const head = document.createElement("div");
    head.className = "collection-item-head";
    const date = document.createElement("span");
    date.className = "pair-date";
    date.textContent = formatDate(item.date || (pair && pair.date));
    date.title = item.date || (pair && pair.date) || "";
    const source = document.createElement("span");
    source.textContent = sourceTitle(item.source_id);
    head.append(date, source);

    const question = document.createElement("div");
    question.className = "collection-item-question";
    question.textContent = item.preview || (pair ? pair.question_text : "(missing pair)");

    card.append(head, question);
    if (!pair) card.appendChild(makeBadge("missing pair", "badge missing"));
    if (item.note) {
      const note = document.createElement("div");
      note.className = "collection-item-note";
      note.textContent = item.note;
      card.appendChild(note);
    }
    card.addEventListener("click", () => {
      if (pair) selectPair(pair.pair_id);
    });
    container.appendChild(card);
  }
}

function renderPreviewAssets(assets) {
  if (!assets || !assets.length) return null;
  const title = document.createElement("h3");
  title.textContent = "Attachments";
  const list = document.createElement("ul");
  list.className = "assets";
  for (const asset of assets) {
    const item = document.createElement("li");
    if (asset.local_path) {
      const link = document.createElement("a");
      link.href = `/assets/${asset.local_path}`;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = asset.label;
      item.appendChild(link);
    } else {
      item.textContent = `${asset.label} (unavailable)`;
    }
    list.appendChild(item);
  }
  const wrapper = document.createElement("div");
  wrapper.append(title, list);
  return wrapper;
}

async function renderPreview(pairId) {
  const container = $("preview");
  container.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "empty";
  loading.textContent = "Loading…";
  container.appendChild(loading);
  try {
    const data = await fetchJSON(`/api/pairs/${encodeURIComponent(pairId)}`);
    const pair = data.pair;
    container.replaceChildren();

    const head = document.createElement("div");
    head.className = "preview-head";
    const date = document.createElement("span");
    date.textContent = formatDate(pair.date, true);
    date.title = pair.date;
    const source = document.createElement("span");
    source.textContent = ` · ${sourceTitle(pair.source_id)}`;
    head.append(date, source);
    if (pair.has_branches) head.appendChild(makeBadge("branches", "badge warn"));
    if (pair.missing_answer) head.appendChild(makeBadge("no answer", "badge warn"));
    container.appendChild(head);

    const questionTitle = document.createElement("h3");
    questionTitle.textContent = "Question";
    const question = document.createElement("div");
    question.className = "question";
    question.innerHTML = data.question_html || "<p>(no text)</p>";
    container.append(questionTitle, question);

    const answerTitle = document.createElement("h3");
    answerTitle.textContent = "Answer";
    const answer = document.createElement("div");
    answer.className = "answer";
    answer.innerHTML = data.answer_html || "<p>(no text)</p>";
    container.append(answerTitle, answer);

    const assets = renderPreviewAssets(data.assets);
    if (assets) container.appendChild(assets);
  } catch (error) {
    container.replaceChildren();
    const message = document.createElement("p");
    message.className = "empty";
    message.textContent = `Could not load pair: ${error.message}`;
    container.appendChild(message);
  }
}

function selectPair(pairId) {
  state.selectedPairId = pairId;
  renderLibrary();
  renderPreview(pairId);
}

function populateSources() {
  const select = $("source-filter");
  select.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "All sources";
  select.appendChild(all);
  for (const source of state.library.sources) {
    const option = document.createElement("option");
    option.value = source.source_id;
    option.textContent = `${source.title} (${source.provider})`;
    select.appendChild(option);
  }
  select.value = state.filters.source;
}

async function load() {
  setStatus("Loading…");
  try {
    const [library, statePayload] = await Promise.all([
      fetchJSON("/api/library"),
      fetchJSON("/api/state"),
    ]);
    state.library = library;
    state.collections = statePayload.collections;
    state.revision = statePayload.revision;
    state.currentCollectionId = state.collections.length
      ? state.collections[0].collection_id
      : null;
    populateSources();
    renderLibrary();
    renderCollections();
    setStatus("Loaded");
  } catch (error) {
    setStatus("Load failed");
    showError(`Could not load the curator: ${error.message}`);
  }
}

function bind() {
  $("search").addEventListener("input", (event) => {
    state.filters.search = event.target.value;
    renderLibrary();
  });
  $("source-filter").addEventListener("change", (event) => {
    state.filters.source = event.target.value;
    renderLibrary();
  });
  $("date-from").addEventListener("change", (event) => {
    state.filters.from = event.target.value;
    renderLibrary();
  });
  $("date-to").addEventListener("change", (event) => {
    state.filters.to = event.target.value;
    renderLibrary();
  });
  $("collection-select").addEventListener("change", (event) => {
    state.currentCollectionId = event.target.value || null;
    renderCollections();
  });
  $("reload").addEventListener("click", async () => {
    setStatus("Reloading…");
    try {
      await fetchJSON("/api/reload", { method: "POST", body: "{}" });
      await load();
      setStatus("Library reloaded");
    } catch (error) {
      setStatus("Reload failed");
      showError(`Could not reload: ${error.message}`);
    }
  });
}

bind();
load();
