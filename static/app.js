let items = [];
let selected = new Set();
let sources = [];
let editorPasswords = {};
let editorFiles = [];

const pdfInput = document.getElementById("pdfInput");
const addToQueueBtn = document.getElementById("addToQueueBtn");
const loadEditorBtn = document.getElementById("loadEditorBtn");
const resetBtn = document.getElementById("resetBtn");
const clearSelectionBtn = document.getElementById("clearSelectionBtn");
const stats = document.getElementById("stats");
const selectedWrap = document.getElementById("selectedWrap");
const sourcesWrap = document.getElementById("sourcesWrap");
const thumbGrid = document.getElementById("thumbGrid");
const passwordsWrap = document.getElementById("passwordsWrap");
const queueWrap = document.getElementById("queueWrap");

const rotate90Btn = document.getElementById("rotate90Btn");
const rotate180Btn = document.getElementById("rotate180Btn");
const clearRotationBtn = document.getElementById("clearRotationBtn");
const deleteBtn = document.getElementById("deleteBtn");
const duplicateBtn = document.getElementById("duplicateBtn");
const blankBtn = document.getElementById("blankBtn");
const exportBtn = document.getElementById("exportBtn");
const exportCompression = document.getElementById("exportCompression");

function uid() { return Math.random().toString(36).slice(2, 10); }

function renderQueue() {
  queueWrap.innerHTML = "";
  if (!editorFiles.length) {
    queueWrap.textContent = "Nessun PDF in coda.";
    return;
  }
  editorFiles.forEach(file => {
    const el = document.createElement("span");
    el.className = "queue-badge";
    el.textContent = file.name;
    queueWrap.appendChild(el);
  });
}

function renderPasswords() {
  passwordsWrap.innerHTML = "";
  const names = [...new Set(editorFiles.map(f => f.name))];
  names.forEach(name => {
    const card = document.createElement("div");
    card.className = "password-card";
    card.innerHTML = `<label>Password PDF per ${name} (lascia vuoto se non serve)</label>`;
    const inp = document.createElement("input");
    inp.type = "password";
    inp.placeholder = `Password per ${name}`;
    inp.value = editorPasswords[name] || "";
    inp.addEventListener("input", () => { editorPasswords[name] = inp.value; });
    card.appendChild(inp);
    passwordsWrap.appendChild(card);
  });
}

function renderSelected() {
  selectedWrap.innerHTML = "";
  [...selected].forEach(label => {
    const el = document.createElement("span");
    el.className = "badge";
    el.textContent = label;
    selectedWrap.appendChild(el);
  });
}

function renderSources() {
  sourcesWrap.innerHTML = "";
  sources.forEach(src => {
    const el = document.createElement("span");
    el.className = "source-badge";
    el.textContent = `${src.filename} (${src.page_count} pagine)`;
    sourcesWrap.appendChild(el);
  });
}

function renderStats() {
  if (!items.length) {
    stats.textContent = "Nessun PDF caricato nell'editor.";
    return;
  }
  stats.textContent = `${sources.length} PDF caricati · ${items.length} pagine correnti · ${selected.size} selezionate`;
}

function makeThumb(item) {
  const div = document.createElement("div");
  div.className = "thumb" + (selected.has(item.label) ? " selected" : "");
  div.draggable = true;
  div.dataset.label = item.label;
  div.innerHTML = `
    <div class="thumb-title">${item.label}</div>
    <img src="${item.thumb}" alt="${item.label}">
    <div class="thumb-meta">
      ${item.source_name ? item.source_name : ""}
      ${item.is_blank ? " · Blank page" : ""}
      ${item.rotation ? ` · Rot: ${item.rotation}°` : ""}
    </div>
  `;
  div.addEventListener("click", () => {
    if (selected.has(item.label)) selected.delete(item.label);
    else selected.add(item.label);
    render();
  });
  div.addEventListener("dragstart", () => div.classList.add("dragging"));
  div.addEventListener("dragend", () => div.classList.remove("dragging"));
  return div;
}

function getDragAfterElement(container, x, y) {
  const elements = [...container.querySelectorAll(".thumb:not(.dragging)")];
  return elements.reduce((closest, child) => {
    const box = child.getBoundingClientRect();
    const centerX = box.left + box.width / 2;
    const centerY = box.top + box.height / 2;
    const score = Math.abs(x - centerX) + Math.abs(y - centerY);
    if (score < closest.score) return { score, element: child };
    return closest;
  }, { score: Number.POSITIVE_INFINITY, element: null }).element;
}

function renderGrid() {
  thumbGrid.innerHTML = "";
  items.forEach(item => thumbGrid.appendChild(makeThumb(item)));
  thumbGrid.querySelectorAll(".thumb").forEach(el => {
    el.addEventListener("dragover", e => {
      e.preventDefault();
      const dragging = thumbGrid.querySelector(".dragging");
      if (!dragging) return;
      const after = getDragAfterElement(thumbGrid, e.clientX, e.clientY);
      if (after == null) thumbGrid.appendChild(dragging);
      else if (after !== dragging) thumbGrid.insertBefore(dragging, after);
    });
  });
  thumbGrid.addEventListener("drop", () => {
    const order = [...thumbGrid.querySelectorAll(".thumb")].map(el => el.dataset.label);
    const lookup = new Map(items.map(i => [i.label, i]));
    items = order.map(label => lookup.get(label)).filter(Boolean);
    render();
  }, { once: true });
}

function render() {
  renderQueue();
  renderPasswords();
  renderStats();
  renderSources();
  renderSelected();
  renderGrid();
}

function addSelectedFilesToQueue() {
  const newFiles = [...pdfInput.files];
  if (!newFiles.length) return;
  const existingKeys = new Set(editorFiles.map(f => `${f.name}__${f.size}__${f.lastModified}`));
  newFiles.forEach(f => {
    const key = `${f.name}__${f.size}__${f.lastModified}`;
    if (!existingKeys.has(key)) editorFiles.push(f);
  });
  pdfInput.value = "";
  render();
}

async function loadEditorFromQueue() {
  if (!editorFiles.length) return;
  const fd = new FormData();
  editorFiles.forEach(file => fd.append("files", file));
  fd.append("passwords_json", JSON.stringify(editorPasswords));

  stats.textContent = "Caricamento PDF...";
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  const data = await res.json();
  if (!res.ok) {
    alert(data.detail || "Errore upload");
    return;
  }

  sources = data.sources || [];
  items = (data.pages || []).map((p, idx) => ({
    id: uid(),
    token: p.token,
    source_index: p.source_index,
    label: p.label || `Page ${idx+1}`,
    source_name: p.source_name || "",
    rotation: p.rotation || 0,
    is_blank: p.is_blank || false,
    width: p.width || 595,
    height: p.height || 842,
    thumb: p.thumb
  }));
  selected = new Set();
  render();
}

function resetAll() {
  items = [];
  selected = new Set();
  sources = [];
  editorPasswords = {};
  editorFiles = [];
  pdfInput.value = "";
  render();
}

addToQueueBtn.addEventListener("click", addSelectedFilesToQueue);
loadEditorBtn.addEventListener("click", loadEditorFromQueue);
resetBtn.addEventListener("click", resetAll);
clearSelectionBtn.addEventListener("click", () => { selected = new Set(); render(); });

rotate90Btn.addEventListener("click", () => {
  items.forEach(i => { if (selected.has(i.label)) i.rotation = (i.rotation + 90) % 360; });
  render();
});
rotate180Btn.addEventListener("click", () => {
  items.forEach(i => { if (selected.has(i.label)) i.rotation = (i.rotation + 180) % 360; });
  render();
});
clearRotationBtn.addEventListener("click", () => {
  items.forEach(i => { if (selected.has(i.label)) i.rotation = 0; });
  render();
});
deleteBtn.addEventListener("click", () => {
  items = items.filter(i => !selected.has(i.label));
  selected = new Set();
  render();
});
duplicateBtn.addEventListener("click", () => {
  let counter = 1;
  const out = [];
  items.forEach(i => {
    out.push(i);
    if (selected.has(i.label)) out.push({ ...i, id: uid(), label: `${i.label} copy ${counter++}` });
  });
  items = out;
  render();
});
blankBtn.addEventListener("click", () => {
  if (selected.size === 0) return;
  let counter = 1;
  const blankThumb = "data:image/svg+xml;base64," + btoa(`
    <svg xmlns="http://www.w3.org/2000/svg" width="595" height="842">
      <rect width="100%" height="100%" fill="white" stroke="#ddd"/>
      <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#999" font-size="28">Blank page</text>
    </svg>
  `);
  const out = [];
  items.forEach(i => {
    out.push(i);
    if (selected.has(i.label)) {
      out.push({
        id: uid(), token: null, source_index: null, label: `Blank ${counter++}`,
        source_name: "", rotation: 0, is_blank: true, width: 595, height: 842, thumb: blankThumb
      });
    }
  });
  items = out;
  render();
});

exportBtn.addEventListener("click", async () => {
  if (!items.length) return;
  const payload = {
    compression: exportCompression.value,
    items: items.map(i => ({
      token: i.token, source_index: i.source_index, rotation: i.rotation, is_blank: i.is_blank,
      width: i.width, height: i.height, label: i.label, source_name: i.source_name
    }))
  };
  const res = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.detail || "Errore export");
    return;
  }
  window.location.href = data.download_url;
});

render();
