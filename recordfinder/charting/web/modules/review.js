/* Chart Review and Results/Testing. PDF pages render through /api/doc. */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let requestedPath = "", activePreview = null, activeApi = null;

  function showMissing(host, doc) {
    host.innerHTML = `<div class="placeholder-file"><b>Not a real file</b>${esc(doc.name || "This document")} is a placeholder. The demo clinic does not have a file for it, so there is nothing to open.</div>`;
  }

  async function showDoc(doc, host, api) {
    if (!doc || !doc.path) { showMissing(host, doc || {}); return; }
    host.innerHTML = `<div class="muted">Opening ${esc(doc.name || "file")}…</div>`;
    let info;
    try { info = await api.get("/api/doc?info=1&path=" + encodeURIComponent(doc.path)); }
    catch (e) { host.innerHTML = `<div class="placeholder-file"><b>Can't open this file</b>${esc(e.message || "It isn't on this computer.")}</div>`; return; }
    if (info.kind === "text") { host.innerHTML = `<pre class="card" style="white-space:pre-wrap">${esc(info.text || "")}</pre>`; return; }
    if (info.kind !== "pdf") { host.innerHTML = `<div class="placeholder-file"><b>${esc(info.name || doc.name || "File")}</b>No preview for this file type. It can still be opened from the classic chart.</div>`; return; }
    const pages = document.createElement("div");
    pages.className = "doc-pages";
    for (let i = 0; i < (info.pages || 0); i++) {
      const img = document.createElement("img");
      img.alt = `Page ${i + 1}`;
      // the screen's real resolution (the page shows up to 860px wide), so filed notes read sharply
      const zoom = Math.min(3, Math.max(1.5, 860 * (window.devicePixelRatio || 1) / 612)).toFixed(2);
      img.src = "/api/doc?path=" + encodeURIComponent(doc.path) + "&page=" + i + "&zoom=" + zoom;
      pages.append(img);
    }
    host.innerHTML = "";
    const banner = document.createElement("div");
    banner.className = "readonly-banner";
    const what = doc.kind && doc.kind !== "Progress note" ? `${doc.kind} report` : "Filed note";
    banner.innerHTML = `<b>${esc(what)} · read only</b><span>${esc(info.name || doc.name || "Progress note")}</span>`;
    host.append(banner, pages);
  }

  function list(host, items, onPick) {
    host.innerHTML = "";
    if (!items.length) { host.innerHTML = `<div class="muted" style="padding:12px">Nothing here.</div>`; return; }
    let heading = null;
    for (const item of items) {
      if (item.group && item.group !== heading) {
        heading = item.group;
        const g = document.createElement("div");
        g.className = "review-group";
        g.textContent = heading;
        host.append(g);
      }
      const b = document.createElement("button");
      b.type = "button";
      if (item.path) b.dataset.path = item.path;
      // the details break only between their parts ("not a real file" stays together)
      b.innerHTML = `<span>${esc(item.title)}</span><small>${String(item.sub || "").split(" · ").filter(Boolean).map((p) => `<span class="rv-part">${esc(p)}</span>`).join(" · ")}</small>`;
      b.title = item.title;
      b.addEventListener("click", () => {
        pick(host.closest(".review-list") || host, b);
        onPick(item);
      });
      host.append(b);
    }
  }
  function pick(listHost, button) {
    listHost.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === button));
  }
  // Studies first (EMG/NCV, vestibular testing), then the rest in the chart's usual order.
  const ORDER = ["Neuro testing", "Vestibular testing", "Progress notes", "Prepped notes", "Referrals & authorizations", "Faxes", "Records", "Billing", "Other files"];
  const rank = (g) => { const i = ORDER.indexOf(g); return i < 0 ? ORDER.length : i; };

  function shell(host) {
    host.innerHTML = `<div class="review"><div class="review-list"></div><div class="review-preview"><div class="muted">Choose a visit or a document.</div></div></div>`;
    return { list: host.querySelector(".review-list"), preview: host.querySelector(".review-preview") };
  }

  function mount(host, ctx) {
    const chart = (ctx && ctx.chart) || {};
    const api = ctx.api;
    const ui = shell(host);
    activePreview = ui.preview; activeApi = api; activeUi = ui; activeDocs = chart.documents || [];
    const visits = chart.visits || [];
    const docs = chart.documents || [];
    ui.list.innerHTML = `<h3>Visits</h3><div id="visitList"></div><h3>Documents</h3><div id="docList"></div>`;
    list(ui.list.querySelector("#visitList"), visits.map((v) => ({
      title: v.day || v.iso || "Visit",
      sub: [v.tags, v.where, v.seen ? "Seen" : "Not seen", v.note_kind].filter(Boolean).join(" · "),
      doc: v.note ? { path: v.note, name: "Progress note" } : null,
    })), (item) => {
      if (!item.doc) ui.preview.innerHTML = `<div class="placeholder-file"><b>${esc(item.title)}</b>No note is filed for this visit.</div>`;
      else showDoc(item.doc, ui.preview, api);
    });
    const sorted = docs.map((d, i) => ({ d, i })).sort((a, b) => rank(a.d.group) - rank(b.d.group) || a.i - b.i).map((x) => x.d);
    list(ui.list.querySelector("#docList"), sorted.map((d) => ({
      title: d.name || "Document",
      sub: [d.kind !== d.group ? d.kind : "", d.modified, d.path ? "" : "not a real file"].filter(Boolean).join(" · "),
      group: d.group || "Other files",
      path: d.path || "",
      doc: d,
    })), (item) => showDoc(item.doc, ui.preview, api));
    if (requestedPath) {
      const path = requestedPath; requestedPath = "";
      openPath(path, ui, docs, api);
    }
    if (!visits.length && !docs.length) ui.preview.innerHTML = `<div class="empty"><h2>No chart documents</h2><p>Visits and files show up here once this patient has any.</p></div>`;
  }

  // Open one file by path: highlight it in the list when it's there, so the viewer and the list agree.
  function openPath(path, ui, docs, api) {
    const doc = docs.find((d) => d.path === path) || { path, name: path.split(/[\\/]/).pop() || "Progress note" };
    const b = [...ui.list.querySelectorAll("button[data-path]")].find((x) => x.dataset.path === path);
    if (b) { pick(ui.list, b); b.scrollIntoView({ block: "nearest" }); }
    showDoc(doc, ui.preview, api);
  }
  let activeUi = null, activeDocs = [];
  function open(path) {
    requestedPath = path || "";
    if (requestedPath && activeUi && activeUi.list.isConnected && activeApi) {
      const p = requestedPath; requestedPath = "";
      openPath(p, activeUi, activeDocs, activeApi);
    }
  }

  DDOS.register("review", { mount, open, show: showDoc, unmount() { activePreview = null; activeApi = null; activeUi = null; activeDocs = []; } });
})();
