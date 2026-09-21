(() => {
  const state = {
    emails: [],
    total: 0,
    offset: 0,
    limit: 80,
    selectedId: null,
    smart: "all",
    folder: "",
    tag: "",
    year: "",
    q: "",
    sort: "date_desc",
    loadingMore: false,
    remoteImages: false,
    stats: null,
    tags: [],
    folders: [],
    years: [],
    current: null,
    fsPath: "",
    noteTimer: null,
  };

  const $ = (id) => document.getElementById(id);
  const emailList = $("email-list");

  function params() {
    const p = new URLSearchParams();
    p.set("q", state.q);
    p.set("sort", state.sort);
    p.set("limit", String(state.limit));
    p.set("offset", String(state.offset));
    if (state.folder) p.set("folder", state.folder);
    if (state.tag) p.set("tag", state.tag);
    if (state.year) p.set("year", state.year);
    if (state.smart === "starred") p.set("starred", "1");
    if (state.smart === "unread") p.set("unread", "1");
    if (state.smart === "attachments") p.set("has_attachments", "1");
    return p;
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { error: text }; }
    if (!res.ok) throw new Error((data && data.error) || res.statusText);
    return data;
  }

  function fmtDate(ts, iso) {
    if (!ts) return iso || "";
    const d = new Date(ts * 1000);
    const now = new Date();
    const sameYear = d.getFullYear() === now.getFullYear();
    return d.toLocaleString(undefined, {
      year: sameYear ? undefined : "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function fmtSize(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  function treeIndent(folder) {
    if (!folder) return 0;
    return Math.min(folder.split("/").length, 3);
  }

  async function refreshNav() {
    const [stats, folderData, tagData] = await Promise.all([
      api("/api/stats"),
      api("/api/folders"),
      api("/api/tags"),
    ]);
    state.stats = stats;
    state.folders = folderData.folders || [];
    state.years = folderData.years || [];
    state.tags = tagData.tags || [];
    $("count-all").textContent = stats.total || 0;
    $("count-starred").textContent = stats.starred || 0;
    $("count-unread").textContent = stats.unread || 0;
    $("count-att").textContent = stats.with_attachments || 0;
    $("archive-label").textContent = stats.archive_root || "No folder selected";
    $("archive-label").title = stats.archive_root || "";
    renderFolders();
    renderYears();
    renderTags();
    updateIndexStatus(stats.index);
  }

  function renderFolders() {
    const box = $("folder-tree");
    box.innerHTML = "";
    if (!state.folders.length) {
      box.innerHTML = `<p class="muted tiny" style="padding:0 10px">None yet</p>`;
      return;
    }
    for (const f of state.folders) {
      const btn = document.createElement("button");
      const name = f.folder || "(archive root)";
      const value = f.folder || "(root)";
      btn.className = `tree-item indent-${treeIndent(f.folder)} ${state.folder === value ? "active" : ""}`;
      btn.innerHTML = `<span class="name"></span><span class="count">${f.count}</span>`;
      btn.querySelector(".name").textContent = name;
      btn.addEventListener("click", () => {
        state.smart = "all";
        state.tag = "";
        state.year = "";
        state.folder = value;
        markSmart();
        reloadList();
        renderFolders();
      });
      box.appendChild(btn);
    }
  }

  function renderYears() {
    const box = $("year-list");
    box.innerHTML = "";
    for (const y of state.years) {
      const btn = document.createElement("button");
      btn.className = `tree-item ${String(state.year) === String(y.year) ? "active" : ""}`;
      btn.innerHTML = `<span class="name">${y.year}</span><span class="count">${y.count}</span>`;
      btn.addEventListener("click", () => {
        state.year = String(y.year);
        state.folder = "";
        state.tag = "";
        state.smart = "all";
        markSmart();
        reloadList();
        renderYears();
      });
      box.appendChild(btn);
    }
  }

  function renderTags() {
    const box = $("tag-list");
    box.innerHTML = "";
    if (!state.tags.length) {
      box.innerHTML = `<p class="muted tiny" style="padding:0 10px">Tag emails without moving them</p>`;
      return;
    }
    for (const t of state.tags) {
      const btn = document.createElement("button");
      btn.className = `tree-item ${state.tag === t.name ? "active" : ""}`;
      btn.innerHTML = `<span class="name"></span><span class="count">${t.count}</span>`;
      btn.querySelector(".name").textContent = t.name;
      btn.style.borderLeft = `3px solid ${t.color}`;
      btn.addEventListener("click", () => {
        state.tag = t.name;
        state.folder = "";
        state.year = "";
        state.smart = "all";
        markSmart();
        reloadList();
        renderTags();
      });
      box.appendChild(btn);
    }
  }

  function markSmart() {
    document.querySelectorAll(".nav-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.smart === state.smart && !state.folder && !state.tag && !state.year);
    });
  }

  async function reloadList() {
    state.offset = 0;
    const data = await api("/api/emails?" + params().toString());
    state.emails = data.emails;
    state.total = data.total;
    $("result-meta").textContent = `${data.total} message${data.total === 1 ? "" : "s"}`;
    renderList(true);
    if (state.emails.length && !state.emails.some((e) => e.id === state.selectedId)) {
      selectEmail(state.emails[0].id);
    } else if (!state.emails.length) {
      state.selectedId = null;
      $("message").hidden = true;
      $("empty-read").hidden = false;
    }
  }

  function renderList(reset) {
    if (reset) emailList.innerHTML = "";
    const frag = document.createDocumentFragment();
    const start = reset ? 0 : emailList.querySelectorAll(".row").length;
    for (const item of state.emails.slice(start)) {
      frag.appendChild(rowEl(item));
    }
    emailList.appendChild(frag);
  }

  function rowEl(item) {
    const row = document.createElement("div");
    row.className = `row ${item.id === state.selectedId ? "selected" : ""} ${item.unread ? "unread" : ""}`;
    row.dataset.id = item.id;
    const pills = (item.tags || []).map((t) => `<span class="pill">${escapeHtml(t.name)}</span>`).join("");
    const att = item.has_attachments ? " · 📎" : "";
    row.innerHTML = `
      <button class="star ${item.starred ? "on" : ""}" title="Star">${item.starred ? "★" : "☆"}</button>
      <div>
        <p class="subject"></p>
        <div class="meta"><span class="from"></span><span>${escapeHtml(item.folder || "")}${att}</span></div>
      </div>
      <div class="when">${escapeHtml(fmtDate(item.date_ts, item.date_iso))}</div>
      <div class="snippet"></div>
    `;
    row.querySelector(".subject").textContent = item.subject || "(no subject)";
    row.querySelector(".from").textContent = item.sender || item.sender_email || "";
    row.querySelector(".snippet").textContent = item.snippet || "";
    if (pills) {
      const wrap = document.createElement("div");
      wrap.className = "pills";
      wrap.style.gridColumn = "2 / -1";
      wrap.innerHTML = pills;
      row.appendChild(wrap);
    }
    row.addEventListener("click", (ev) => {
      if (ev.target.closest(".star")) return;
      selectEmail(item.id);
    });
    row.querySelector(".star").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await toggleStar(item);
    });
    return row;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  async function toggleStar(item) {
    const next = !item.starred;
    await api(`/api/emails/${item.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ starred: next }),
    });
    item.starred = next;
    if (state.current && state.current.id === item.id) {
      state.current.starred = next;
      syncStarButtons();
    }
    const row = emailList.querySelector(`.row[data-id="${item.id}"]`);
    if (row) {
      const btn = row.querySelector(".star");
      btn.classList.toggle("on", next);
      btn.textContent = next ? "★" : "☆";
    }
    refreshNav().catch(() => {});
  }

  async function selectEmail(id) {
    state.selectedId = id;
    emailList.querySelectorAll(".row").forEach((el) => {
      el.classList.toggle("selected", Number(el.dataset.id) === id);
    });
    const rec = await api(`/api/emails/${id}`);
    state.current = rec;
    state.remoteImages = false;
    $("empty-read").hidden = true;
    $("message").hidden = false;
    $("msg-subject").textContent = rec.subject || "(no subject)";
    $("msg-from").textContent = rec.sender || "";
    $("msg-to").textContent = rec.recipients || "";
    $("msg-date").textContent = fmtDate(rec.date_ts, rec.date_iso);
    $("msg-folder").textContent = rec.folder || "(archive root)";
    $("msg-path").textContent = rec.path || "";
    $("msg-note").value = rec.note || "";
    $("toggle-unread").textContent = rec.unread ? "Mark read" : "Mark unread";
    syncStarButtons();
    renderMsgTags();
    renderAttachments(rec);
    $("msg-frame").src = `/api/emails/${id}/html`;
    if (rec.unread) {
      await api(`/api/emails/${id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unread: false }),
      });
      rec.unread = false;
      const item = state.emails.find((e) => e.id === id);
      if (item) item.unread = false;
      const row = emailList.querySelector(`.row[data-id="${id}"]`);
      if (row) row.classList.remove("unread");
      $("toggle-unread").textContent = "Mark unread";
      refreshNav().catch(() => {});
    }
  }

  function syncStarButtons() {
    const on = !!(state.current && state.current.starred);
    $("star-btn").classList.toggle("on", on);
    $("star-btn").textContent = on ? "★" : "☆";
  }

  function renderMsgTags() {
    const box = $("msg-tags");
    box.innerHTML = "";
    for (const t of (state.current && state.current.tags) || []) {
      const span = document.createElement("span");
      span.className = "pill";
      span.textContent = t.name;
      span.title = "Click to remove";
      span.style.cursor = "pointer";
      span.addEventListener("click", async () => {
        await api(`/api/emails/${state.current.id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tag_id: t.id, tagged: false }),
        });
        await selectEmail(state.current.id);
        refreshNav().catch(() => {});
      });
      box.appendChild(span);
    }
  }

  function renderAttachments(rec) {
    const box = $("msg-attachments");
    const files = (rec.attachments || []).filter((a) => !a.inline || a.filename);
    if (!files.length) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    box.innerHTML = files.map((a) => {
      const name = escapeHtml(a.filename || `part-${a.part_index}`);
      const size = fmtSize(a.size_bytes || 0);
      return `<a class="att" href="/api/emails/${rec.id}/attachments/${a.part_index}">📎 ${name} <span class="muted">${size}</span></a>`;
    }).join("");
  }

  function updateIndexStatus(snap) {
    if (!snap) return;
    const el = $("index-status");
    if (snap.running) {
      el.textContent = `Indexing ${snap.processed}/${snap.total}…`;
    } else if (snap.phase === "done") {
      el.textContent = `Last index: ${snap.updated} updated, ${snap.skipped} unchanged`;
    } else if (snap.phase === "error") {
      el.textContent = `Index error: ${snap.current || "failed"}`;
    } else {
      el.textContent = snap.phase === "idle" ? "Index idle" : String(snap.phase);
    }
  }

  async function pollIndex() {
    try {
      const snap = await api("/api/index");
      updateIndexStatus(snap);
      if (snap.running) {
        setTimeout(pollIndex, 600);
      } else if (snap.phase === "done") {
        await refreshNav();
        await reloadList();
      }
    } catch {
      setTimeout(pollIndex, 1500);
    }
  }

  async function startIndex(root, full) {
    await api("/api/index", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root, full: !!full }),
    });
    pollIndex();
  }

  function openModal(id) { $(id).hidden = false; }
  function closeModal(id) { $(id).hidden = true; }

  async function loadFs(path) {
    const data = await api("/api/fs" + (path ? `?path=${encodeURIComponent(path)}` : ""));
    state.fsPath = data.path || "";
    $("folder-path-input").value = data.path || "";
    const box = $("fs-list");
    box.innerHTML = "";
    if (data.parent) {
      const up = document.createElement("button");
      up.className = "fs-item";
      up.textContent = "↑ Parent folder";
      up.addEventListener("click", () => loadFs(data.parent));
      box.appendChild(up);
    }
    for (const ent of data.entries || []) {
      const btn = document.createElement("button");
      btn.className = "fs-item";
      btn.textContent = (ent.dir ? "📁 " : "✉ ") + ent.name;
      btn.addEventListener("click", () => {
        if (ent.dir) loadFs(ent.path);
        else $("folder-path-input").value = state.fsPath;
      });
      box.appendChild(btn);
    }
  }

  function selectedIndex() {
    return state.emails.findIndex((e) => e.id === state.selectedId);
  }

  function bind() {
    $("search-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      state.q = $("search-input").value.trim();
      reloadList();
    });
    let t = null;
    $("search-input").addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(() => {
        state.q = $("search-input").value.trim();
        reloadList();
      }, 280);
    });
    $("sort-select").addEventListener("change", () => {
      state.sort = $("sort-select").value;
      reloadList();
    });
    document.querySelectorAll(".nav-item").forEach((el) => {
      el.addEventListener("click", () => {
        state.smart = el.dataset.smart;
        state.folder = "";
        state.tag = "";
        state.year = "";
        markSmart();
        renderFolders();
        renderYears();
        renderTags();
        reloadList();
      });
    });
    emailList.addEventListener("scroll", () => {
      if (state.loadingMore) return;
      if (state.emails.length >= state.total) return;
      if (emailList.scrollTop + emailList.clientHeight > emailList.scrollHeight - 80) {
        state.loadingMore = true;
        state.offset = state.emails.length;
        api("/api/emails?" + params().toString()).then((data) => {
          state.emails = state.emails.concat(data.emails);
          renderList(false);
        }).finally(() => { state.loadingMore = false; });
      }
    });
    $("star-btn").addEventListener("click", () => {
      const item = state.emails.find((e) => e.id === state.selectedId) || state.current;
      if (item) toggleStar(item);
    });
    $("toggle-unread").addEventListener("click", async () => {
      if (!state.current) return;
      const next = !state.current.unread;
      await api(`/api/emails/${state.current.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unread: next }),
      });
      state.current.unread = next;
      $("toggle-unread").textContent = next ? "Mark read" : "Mark unread";
      const row = emailList.querySelector(`.row[data-id="${state.current.id}"]`);
      if (row) row.classList.toggle("unread", next);
      refreshNav().catch(() => {});
    });
    $("show-remote").addEventListener("click", () => {
      if (!state.current) return;
      state.remoteImages = !state.remoteImages;
      $("msg-frame").src = `/api/emails/${state.current.id}/html?remote=${state.remoteImages ? 1 : 0}`;
      $("show-remote").textContent = state.remoteImages ? "Hide remote images" : "Load remote images";
    });
    $("add-tag-to-mail").addEventListener("click", async () => {
      if (!state.current) return;
      const name = prompt("Tag name (files stay on the drive; this is only in the index)");
      if (!name) return;
      await api(`/api/emails/${state.current.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag_name: name }),
      });
      await selectEmail(state.current.id);
      refreshNav().catch(() => {});
    });
    $("new-tag-btn").addEventListener("click", async () => {
      const name = prompt("New tag name");
      if (!name) return;
      await api("/api/tags", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      refreshNav();
    });
    $("msg-note").addEventListener("input", () => {
      clearTimeout(state.noteTimer);
      state.noteTimer = setTimeout(async () => {
        if (!state.current) return;
        await api(`/api/emails/${state.current.id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note: $("msg-note").value }),
        });
      }, 400);
    });
    const openChooser = () => {
      openModal("folder-modal");
      loadFs(state.stats && state.stats.archive_root ? state.stats.archive_root : "");
    };
    $("choose-folder-btn").addEventListener("click", openChooser);
    $("choose-folder-btn-2").addEventListener("click", openChooser);
    $("folder-cancel").addEventListener("click", () => closeModal("folder-modal"));
    $("folder-open").addEventListener("click", async () => {
      const root = $("folder-path-input").value.trim();
      if (!root) return;
      closeModal("folder-modal");
      await startIndex(root, false);
    });
    $("fs-up").addEventListener("click", () => {
      const cur = $("folder-path-input").value.trim();
      if (!cur) { loadFs(""); return; }
      const parts = cur.replace(/\\/g, "/").split("/");
      parts.pop();
      loadFs(parts.join("/") || "/");
    });
    $("reindex-btn").addEventListener("click", async () => {
      const root = (state.stats && state.stats.archive_root) || $("folder-path-input").value.trim();
      if (!root) { openChooser(); return; }
      await startIndex(root, true);
    });
    $("help-close").addEventListener("click", () => closeModal("help-modal"));
    document.addEventListener("keydown", (ev) => {
      const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement && document.activeElement.tagName);
      if (ev.key === "?" && !typing) { openModal("help-modal"); ev.preventDefault(); }
      if (ev.key === "Escape") { closeModal("folder-modal"); closeModal("help-modal"); }
      if (ev.key === "/" && !typing) { $("search-input").focus(); ev.preventDefault(); }
      if (typing) return;
      if (ev.key === "j" || ev.key === "ArrowDown") {
        const i = selectedIndex();
        if (i >= 0 && i < state.emails.length - 1) selectEmail(state.emails[i + 1].id);
      }
      if (ev.key === "k" || ev.key === "ArrowUp") {
        const i = selectedIndex();
        if (i > 0) selectEmail(state.emails[i - 1].id);
      }
      if (ev.key === "s" && state.current) toggleStar(state.current);
      if (ev.key === "u" && state.current) $("toggle-unread").click();
    });
  }

  async function init() {
    bind();
    await refreshNav();
    await reloadList();
    if (state.stats && state.stats.index && state.stats.index.running) pollIndex();
  }

  init().catch((err) => {
    $("result-meta").textContent = err.message;
  });
})();
