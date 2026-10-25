const state = {
  library: { sources: [], pairs: [] },
  collections: [],
  revision: 0,
  currentCollectionId: null,
  selectedPairId: null,
  selectedPairIds: new Set(),
  selectedItemIndex: null,
  filters: { search: "", source: "", from: "", to: "" },
  saving: false,
  pendingSave: false,
};

const $ = (id) => document.getElementById(id);
let saveTimer = null;

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
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function setStatus(text) {
  $("save-status").textContent = text;
}

function showError(message) {
  const existing = document.querySelector(".error-banner:not(.conflict)");
  if (existing) existing.remove();
  const banner = document.createElement("div");
  banner.className = "error-banner";
  banner.textContent = message;
  document.body.appendChild(banner);
  setTimeout(() => banner.remove(), 6000);
}

function showConflict() {
  const existing = document.querySelector(".error-banner.conflict");
  if (existing) existing.remove();
  const banner = document.createElement("div");
  banner.className = "error-banner conflict";
  banner.textContent = "Another tab changed the collections. Reload before saving.";
  const reload = document.createElement("button");
  reload.type = "button";
  reload.textContent = "Reload";
  reload.addEventListener("click", reloadState);
  banner.appendChild(reload);
  document.body.appendChild(banner);
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

function currentCollection() {
  return (
    state.collections.find(
      (collection) => collection.collection_id === state.currentCollectionId
    ) || null
  );
}

function formatDate(iso, withTime = false) {
  if (!iso) return "unknown date";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const options = withTime
    ? {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }
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

function updateAddSelected() {
  $("add-selected").disabled =
    state.selectedPairIds.size === 0 || currentCollection() === null;
  $("new-from-selection").disabled = state.selectedPairIds.size === 0;
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
    updateAddSelected();
    return;
  }
  for (const pair of pairs) {
    const row = document.createElement("div");
    row.className = "pair-row";
    row.dataset.pairId = pair.pair_id;
    row.tabIndex = 0;
    row.draggable = true;
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
    row.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("application/x-curator", `pair:${pair.pair_id}`);
      event.dataTransfer.effectAllowed = "copy";
      row.classList.add("dragging");
    });
    row.addEventListener("dragend", () => row.classList.remove("dragging"));

    const meta = document.createElement("div");
    meta.className = "pair-meta";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "pair-check";
    check.title = "Select for bulk add";
    check.checked = state.selectedPairIds.has(pair.pair_id);
    check.addEventListener("click", (event) => event.stopPropagation());
    check.addEventListener("change", () => {
      if (check.checked) state.selectedPairIds.add(pair.pair_id);
      else state.selectedPairIds.delete(pair.pair_id);
      updateAddSelected();
    });
    meta.appendChild(check);
    const date = document.createElement("span");
    date.className = "pair-date";
    date.textContent = formatDate(pair.date);
    date.title = pair.date;
    meta.appendChild(date);
    const source = sourceById(pair.source_id);
    meta.appendChild(makeBadge(source ? source.provider : "source", "badge provider"));
    const sourceLabel = document.createElement("span");
    sourceLabel.className = "pair-source";
    sourceLabel.textContent = sourceTitle(pair.source_id);
    sourceLabel.title = pair.source_id;
    meta.appendChild(sourceLabel);
    if (pair.has_branches) meta.appendChild(makeBadge("branches", "badge warn"));
    if (pair.missing_answer) meta.appendChild(makeBadge("no answer", "badge warn"));

    const question = document.createElement("div");
    question.className = "pair-question";
    question.textContent = pair.question_text || "(no question text)";

    row.append(meta, question);
    container.appendChild(row);
  }
  updateAddSelected();
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
  if (
    !state.currentCollectionId ||
    !state.collections.some((c) => c.collection_id === state.currentCollectionId)
  ) {
    state.currentCollectionId = state.collections[0].collection_id;
    state.selectedItemIndex = null;
  }
  select.value = state.currentCollectionId;
}

function updateToolbar() {
  const collection = currentCollection();
  const hasCollection = collection !== null;
  $("add-selected").disabled =
    state.selectedPairIds.size === 0 || !hasCollection;
  $("new-from-selection").disabled = state.selectedPairIds.size === 0;
  $("sort-date").disabled = !hasCollection || collection.items.length === 0;
  $("split-here").disabled =
    !hasCollection || state.selectedItemIndex === null || collection.items.length === 0;
  $("export-collection").disabled = !hasCollection || collection.items.length === 0;
  $("rename-collection").disabled = !hasCollection;
  $("delete-collection").disabled = !hasCollection;
  const otherCollections = state.collections.filter(
    (candidate) => candidate.collection_id !== state.currentCollectionId
  );
  $("merge-source").disabled = !hasCollection || otherCollections.length === 0;
  $("merge-collection").disabled =
    !hasCollection || !$("merge-source").value;
}

function renderMergeSource() {
  const select = $("merge-source");
  const previous = select.value;
  const collection = currentCollection();
  select.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Merge another…";
  select.appendChild(placeholder);
  for (const candidate of state.collections) {
    if (collection && candidate.collection_id === collection.collection_id) continue;
    const option = document.createElement("option");
    option.value = candidate.collection_id;
    option.textContent = candidate.title;
    select.appendChild(option);
  }
  if (previous && state.collections.some((c) => c.collection_id === previous)) {
    select.value = previous;
  }
}

function renderCollections() {
  renderCollectionSelect();
  const container = $("collections");
  const empty = $("collection-empty");
  container.replaceChildren();
  const collection = currentCollection();
  if (!collection) {
    $("collection-title").textContent = "Collections";
    empty.textContent = "Select a collection to see its pairs.";
    empty.hidden = false;
    container.ondragover = null;
    container.ondrop = null;
    renderMergeSource();
    updateToolbar();
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
  if (state.selectedItemIndex !== null && state.selectedItemIndex >= collection.items.length) {
    state.selectedItemIndex = null;
  }
  collection.items.forEach((item, index) => {
    const pair = pairById(item.pair_id);
    const card = document.createElement("div");
    card.className =
      "collection-item" +
      (pair ? "" : " missing") +
      (state.selectedItemIndex === index ? " selected" : "");
    card.dataset.pairId = item.pair_id;
    card.draggable = true;

    card.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("application/x-curator", `item:${index}`);
      event.dataTransfer.effectAllowed = "move";
      card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    card.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.stopPropagation();
      card.classList.add("dragover");
    });
    card.addEventListener("dragleave", () => card.classList.remove("dragover"));
    card.addEventListener("drop", (event) => {
      event.preventDefault();
      event.stopPropagation();
      card.classList.remove("dragover");
      handleDrop(event, index);
    });
    card.addEventListener("click", () => {
      state.selectedItemIndex = index;
      document
        .querySelectorAll(".collection-item.selected")
        .forEach((element) => element.classList.remove("selected"));
      card.classList.add("selected");
      updateToolbar();
      if (pair) selectPair(pair.pair_id);
    });

    const head = document.createElement("div");
    head.className = "collection-item-head";
    const left = document.createElement("span");
    const handle = document.createElement("span");
    handle.className = "drag-handle";
    handle.textContent = "≡";
    const date = document.createElement("span");
    date.className = "pair-date";
    date.textContent = formatDate(item.date || (pair && pair.date));
    date.title = item.date || (pair && pair.date) || "";
    left.append(handle, date);
    const source = document.createElement("span");
    source.textContent = sourceTitle(item.source_id);
    head.append(left, source);

    const question = document.createElement("div");
    question.className = "collection-item-question";
    question.textContent = item.preview || (pair ? pair.question_text : "(missing pair)");

    const actions = document.createElement("div");
    actions.className = "collection-item-actions";
    const up = document.createElement("button");
    up.type = "button";
    up.textContent = "↑";
    up.title = "Move up";
    up.disabled = index === 0;
    up.addEventListener("click", (event) => {
      event.stopPropagation();
      moveItem(index, -1);
    });
    const down = document.createElement("button");
    down.type = "button";
    down.textContent = "↓";
    down.title = "Move down";
    down.disabled = index === collection.items.length - 1;
    down.addEventListener("click", (event) => {
      event.stopPropagation();
      moveItem(index, 1);
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "Remove from collection";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      removeItem(index);
    });
    actions.append(up, down, remove);

    card.append(head, question, actions);
    if (!pair) card.appendChild(makeBadge("missing pair", "badge missing"));
    if (item.note) {
      const note = document.createElement("div");
      note.className = "collection-item-note";
      note.textContent = item.note;
      card.appendChild(note);
    }
    container.appendChild(card);
  });
  container.ondragover = (event) => event.preventDefault();
  container.ondrop = (event) => {
    if (event.target.closest(".collection-item")) return;
    event.preventDefault();
    handleDrop(event, collection.items.length);
  };
  renderMergeSource();
  updateToolbar();
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

function collectionItem(pair) {
  return {
    pair_id: pair.pair_id,
    source_id: pair.source_id,
    question_uuid: pair.question_uuid,
    date: pair.date,
    preview: pair.question_text,
    note: "",
  };
}

function touch(collection) {
  collection.updated_at = new Date().toISOString();
}

function newCollection() {
  const title = window.prompt("Collection title");
  if (!title || !title.trim()) return;
  const now = new Date().toISOString();
  const collection = {
    collection_id:
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `c-${Date.now()}`,
    title: title.trim(),
    description: "",
    items: [],
    created_at: now,
    updated_at: now,
  };
  state.collections.push(collection);
  state.currentCollectionId = collection.collection_id;
  state.selectedItemIndex = null;
  renderCollections();
  scheduleSave();
}

function newCollectionFromSelection() {
  const selected = [...state.selectedPairIds]
    .map((pairId) => pairById(pairId))
    .filter(Boolean)
    .sort((left, right) => (left.date < right.date ? -1 : 1));
  if (!selected.length) return;
  const title = window.prompt("New collection title");
  if (!title || !title.trim()) return;
  const now = new Date().toISOString();
  const collection = {
    collection_id:
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `c-${Date.now()}`,
    title: title.trim(),
    description: "",
    items: selected.map(collectionItem),
    created_at: now,
    updated_at: now,
  };
  state.collections.push(collection);
  state.currentCollectionId = collection.collection_id;
  state.selectedItemIndex = null;
  state.selectedPairIds.clear();
  renderLibrary();
  renderCollections();
  scheduleSave();
}

function renameCollection() {
  const collection = currentCollection();
  if (!collection) return;
  const title = window.prompt("Rename collection", collection.title);
  if (!title || !title.trim()) return;
  collection.title = title.trim();
  touch(collection);
  renderCollections();
  scheduleSave();
}

function deleteCollection() {
  const collection = currentCollection();
  if (!collection) return;
  if (!window.confirm(`Delete collection "${collection.title}"?`)) return;
  state.collections = state.collections.filter(
    (candidate) => candidate.collection_id !== collection.collection_id
  );
  state.currentCollectionId = state.collections.length
    ? state.collections[0].collection_id
    : null;
  state.selectedItemIndex = null;
  renderCollections();
  scheduleSave();
}

function addSelected() {
  const collection = currentCollection();
  if (!collection) return;
  const selected = [...state.selectedPairIds]
    .map((pairId) => pairById(pairId))
    .filter(Boolean)
    .sort((left, right) => (left.date < right.date ? -1 : 1));
  const existing = new Set(collection.items.map((item) => item.pair_id));
  for (const pair of selected) {
    if (!existing.has(pair.pair_id)) {
      collection.items.push(collectionItem(pair));
      existing.add(pair.pair_id);
    }
  }
  state.selectedPairIds.clear();
  touch(collection);
  renderLibrary();
  renderCollections();
  scheduleSave();
}

function removeItem(index) {
  const collection = currentCollection();
  if (!collection || index < 0 || index >= collection.items.length) return;
  collection.items.splice(index, 1);
  state.selectedItemIndex = null;
  touch(collection);
  renderCollections();
  scheduleSave();
}

function moveItem(index, delta) {
  const collection = currentCollection();
  if (!collection) return;
  const target = index + delta;
  if (target < 0 || target >= collection.items.length) return;
  const [item] = collection.items.splice(index, 1);
  collection.items.splice(target, 0, item);
  state.selectedItemIndex = target;
  touch(collection);
  renderCollections();
  scheduleSave();
}

function splitHere() {
  const collection = currentCollection();
  if (!collection || state.selectedItemIndex === null) return;
  const index = state.selectedItemIndex;
  if (index < 0 || index >= collection.items.length) return;
  const now = new Date().toISOString();
  const tail = collection.items.slice(index);
  if (!tail.length) return;
  const split = {
    collection_id:
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `c-${Date.now()}`,
    title: `${collection.title} — split`,
    description: "",
    items: tail,
    created_at: now,
    updated_at: now,
  };
  collection.items = collection.items.slice(0, index);
  touch(collection);
  state.collections.push(split);
  state.currentCollectionId = split.collection_id;
  state.selectedItemIndex = null;
  renderCollections();
  scheduleSave();
}

function mergeCollection() {
  const target = currentCollection();
  const sourceId = $("merge-source").value;
  const source = state.collections.find(
    (candidate) => candidate.collection_id === sourceId
  );
  if (!target || !source || source.collection_id === target.collection_id) return;
  if (!window.confirm(`Merge "${source.title}" into "${target.title}"?`)) return;
  const existing = new Set(target.items.map((item) => item.pair_id));
  for (const item of source.items) {
    if (!existing.has(item.pair_id)) {
      target.items.push(item);
      existing.add(item.pair_id);
    }
  }
  touch(target);
  state.collections = state.collections.filter(
    (candidate) => candidate.collection_id !== source.collection_id
  );
  state.selectedItemIndex = null;
  renderCollections();
  scheduleSave();
}

function sortByDate() {
  const collection = currentCollection();
  if (!collection) return;
  collection.items.sort((left, right) => {
    if (left.date === right.date) return left.pair_id.localeCompare(right.pair_id);
    return left.date < right.date ? -1 : 1;
  });
  state.selectedItemIndex = null;
  touch(collection);
  renderCollections();
  scheduleSave();
}

async function exportCollection() {
  const collection = currentCollection();
  if (!collection) return;
  const hasAssets = collection.items.some((item) => {
    const pair = pairById(item.pair_id);
    return pair && pair.assets && pair.assets.length > 0;
  });
  if (
    hasAssets &&
    !window.confirm(
      "This collection references assets. The export copies them into a zip and may be large. Continue?"
    )
  ) {
    return;
  }
  const hadPending = Boolean(saveTimer) || state.saving || state.pendingSave;
  clearTimeout(saveTimer);
  saveTimer = null;
  if (hadPending) {
    for (let attempt = 0; attempt < 5 && state.saving; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    if (state.saving) {
      showError("A save is still in progress; try exporting again.");
      return;
    }
    const saved = await saveState();
    if (!saved) return;
  }
  window.location.href = `/api/export/${encodeURIComponent(collection.collection_id)}.zip`;
}

function handleDrop(event, targetIndex) {
  const collection = currentCollection();
  if (!collection) return;
  const raw =
    event.dataTransfer.getData("application/x-curator") ||
    event.dataTransfer.getData("text/plain");
  if (!raw) return;
  if (raw.startsWith("pair:")) {
    const pair = pairById(raw.slice(5));
    if (!pair) return;
    const existing = collection.items.findIndex(
      (item) => item.pair_id === pair.pair_id
    );
    if (existing >= 0) {
      const [item] = collection.items.splice(existing, 1);
      const insert = existing < targetIndex ? targetIndex - 1 : targetIndex;
      collection.items.splice(insert, 0, item);
    } else {
      collection.items.splice(targetIndex, 0, collectionItem(pair));
    }
  } else if (raw.startsWith("item:")) {
    const sourceIndex = Number(raw.slice(5));
    if (
      Number.isNaN(sourceIndex) ||
      sourceIndex < 0 ||
      sourceIndex >= collection.items.length
    ) {
      return;
    }
    const [item] = collection.items.splice(sourceIndex, 1);
    const insert = sourceIndex < targetIndex ? targetIndex - 1 : targetIndex;
    collection.items.splice(insert, 0, item);
  } else {
    return;
  }
  state.selectedItemIndex = null;
  touch(collection);
  renderCollections();
  scheduleSave();
}

function scheduleSave() {
  clearTimeout(saveTimer);
  setStatus("Unsaved changes");
  saveTimer = setTimeout(() => {
    saveTimer = null;
    saveState();
  }, 400);
}

async function saveState() {
  if (state.saving) {
    state.pendingSave = true;
    return false;
  }
  state.saving = true;
  setStatus("Saving…");
  let saved = false;
  try {
    const payload = await fetchJSON("/api/state", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: state.revision,
        collections: state.collections,
      }),
    });
    state.revision = payload.revision;
    state.collections = payload.collections;
    setStatus("Saved");
    saved = true;
  } catch (error) {
    if (error.status === 409) {
      setStatus("Conflict");
      showConflict();
      state.pendingSave = false;
    } else {
      setStatus("Save failed");
      showError(`Could not save: ${error.message}`);
    }
  } finally {
    state.saving = false;
    if (state.pendingSave) {
      state.pendingSave = false;
      scheduleSave();
    }
  }
  return saved;
}

async function reloadState() {
  try {
    const payload = await fetchJSON("/api/state");
    state.revision = payload.revision;
    state.collections = payload.collections;
    state.selectedItemIndex = null;
    document.querySelector(".error-banner.conflict")?.remove();
    renderCollections();
    setStatus("Reloaded");
  } catch (error) {
    setStatus("Reload failed");
    showError(`Could not reload state: ${error.message}`);
  }
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
    const previousCollectionId = state.currentCollectionId;
    const [library, statePayload] = await Promise.all([
      fetchJSON("/api/library"),
      fetchJSON("/api/state"),
    ]);
    state.library = library;
    state.collections = statePayload.collections;
    state.revision = statePayload.revision;
    state.selectedPairIds.clear();
    state.selectedItemIndex = null;
    state.currentCollectionId = state.collections.some(
      (collection) => collection.collection_id === previousCollectionId
    )
      ? previousCollectionId
      : state.collections.length
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
    state.selectedItemIndex = null;
    renderCollections();
  });
  $("new-collection").addEventListener("click", newCollection);
  $("rename-collection").addEventListener("click", renameCollection);
  $("delete-collection").addEventListener("click", deleteCollection);
  $("add-selected").addEventListener("click", addSelected);
  $("new-from-selection").addEventListener("click", newCollectionFromSelection);
  $("sort-date").addEventListener("click", sortByDate);
  $("split-here").addEventListener("click", splitHere);
  $("merge-source").addEventListener("change", updateToolbar);
  $("merge-collection").addEventListener("click", mergeCollection);
  $("export-collection").addEventListener("click", exportCollection);
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
  window.addEventListener("beforeunload", (event) => {
    if (saveTimer || state.saving || state.pendingSave) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
}

bind();
load();
