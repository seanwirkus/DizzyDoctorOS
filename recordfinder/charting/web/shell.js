/* The app window, laid out like Windows File Explorer: tabs across the top (Home and one per open patient), a left
   pane, the screen, and a status bar. Home has Today, Patients and Saved notes; a patient tab is that patient's note
   and chart, kept as it was while you work in other tabs. Billing opens the office app. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const DDOS = window.DDOS = window.DDOS || { modules: {}, register(name, mod) { this.modules[name] = mod; } };
  const HOME = [["today", "Calendar", "calendar"], ["patients", "Patients", "users"], ["notes", "Saved notes", "file"]];
  // One line-icon set for every screen (24px grid, drawn in the text colour). No emoji.
  const ICONS = {
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21"/>',
    pen: '<path d="M4 20l4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10z"/><path d="M13.5 6.5l3 3"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    left: '<path d="M14.5 18l-6-6 6-6"/>',
    right: '<path d="M9.5 18l6-6-6-6"/>',
    pause: '<path d="M9 6.5v11M15 6.5v11"/>',
    play: '<path d="M8.5 6v12l9.5-6z"/>',
    list: '<path d="M9 7h11M9 12h11M9 17h11"/><path d="M4.5 7h.01M4.5 12h.01M4.5 17h.01"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/>',
    undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
    print: '<path d="M7 8V3h10v5"/><path d="M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><rect x="7" y="14" width="10" height="7"/>',
    upload: '<path d="M12 16V4"/><path d="M7.5 8.5L12 4l4.5 4.5"/><path d="M4 14v5h16v-5"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    window: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 9h17"/>',
    home: '<path d="M3.5 11l8.5-7 8.5 7"/><path d="M5.5 9.5V20h13V9.5"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.5 3.3-5.5 6.5-5.5s5.7 2 6.5 5.5"/><path d="M15.5 4.8a3.5 3.5 0 0 1 0 6.4M17.5 14.8c2 .7 3.4 2.4 4 5.2"/>',
    wave: '<path d="M3 12h2M7 8v8M11 5v14M15 8v8M19 10v4M21 12h0"/>',
    wand: '<path d="M4 20L15 9"/><path d="M14 5.5l.8-2 .8 2 2 .8-2 .8-.8 2-.8-2-2-.8z"/><path d="M19 11l.5-1.3.5 1.3 1.3.5-1.3.5-.5 1.3-.5-1.3-1.3-.5z"/><path d="M15 9l1.5 1.5"/>',
    alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5"/><path d="M12 17.6v.4"/>',
    pill: '<rect x="3.5" y="8.5" width="17" height="7" rx="3.5" transform="rotate(-35 12 12)"/><path d="M9.4 8.2l5.2 7.6"/>',
    paste: '<rect x="5" y="4.5" width="14" height="16" rx="2"/><path d="M9 4.5V3.5h6v1"/><path d="M8.5 10h7M8.5 13.5h7M8.5 17h4"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
  };
  DDOS.icon = (name, cls = "") => `<svg class="icon${cls ? " " + cls : ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  const state = { status: {}, auth: null, active: "home", view: "today", tabs: [], patient: null, chart: null, snap: null, loadedFor: "" };
  let newCount = 0;

  const api = {
    async get(p) {
      const r = await fetch(p, { credentials: "same-origin" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || r.statusText || "Request failed");
      return data;
    },
    async post(p, body) {
      const r = await fetch(p, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || r.statusText || "Request failed");
      return data;
    },
  };

  function h(tag, attrs = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k === "hidden") el.hidden = !!v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }
  function parseWhen(s) {
    if (s instanceof Date) return Number.isNaN(s.getTime()) ? null : s;
    const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ""));
    if (iso) return new Date(+iso[1], +iso[2] - 1, +iso[3]);
    const m = /(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})/.exec(String(s || ""));
    if (!m) return null;
    let y = +m[3]; if (y < 100) y += 2000;
    const d = new Date(y, +m[1] - 1, +m[2]);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const fmt = {
    parse: parseWhen,
    short(s) { const d = s instanceof Date ? s : parseWhen(s); return d ? d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : String(s || ""); },
    date(s) { return fmt.short(s); },
    age(dob) { const d = parseWhen(dob); if (!d) return ""; const now = new Date(); let a = now.getFullYear() - d.getFullYear(); if (now.getMonth() < d.getMonth() || (now.getMonth() === d.getMonth() && now.getDate() < d.getDate())) a--; return a; },
    name(p) { if (!p) return ""; if (p.name) return p.name; const last = p.last || "", first = p.first || ""; return last && first ? `${last}, ${first}` : (last || first); },
    initials(p) { if (!p) return ""; return ((p.first || "")[0] || "") + ((p.last || "")[0] || ""); },
  };
  const bus = {};
  function on(ev, fn) { (bus[ev] ||= []).push(fn); return () => { bus[ev] = (bus[ev] || []).filter((f) => f !== fn); }; }
  function emit(ev, data) { for (const fn of bus[ev] || []) fn(data); }
  function context() {
    return { api, h, fmt, toast, on, emit, status: state.status, state, patient: state.patient, chart: state.chart, snap: state.snap, snapshot: state.snap,
      params: Object.fromEntries(new URLSearchParams(location.search)), openChart, openPatient, openOffice, go };
  }
  // The note shows chart screens (SnapShot, Chart review, Results / testing) inside its own panel.
  DDOS.context = context;
  DDOS.on = on;

  let activeModule = "";
  function useModule(name, host) {
    const prev = DDOS.modules[activeModule];
    if (prev && prev.unmount && activeModule !== name) { try { prev.unmount(); } catch { /* a screen's cleanup is best-effort */ } }
    if (activeModule !== name) { const tools = $("#headTools"); if (tools) tools.innerHTML = ""; }   // a screen's own top-bar buttons leave with it
    activeModule = name;
    const mod = DDOS.modules[name];
    if (!mod) { host.innerHTML = `<div class="view-scroll"><div class="empty"><h2>${esc(name)}</h2><p>This section isn't available.</p></div></div>`; return; }
    return mod.mount(host, context());
  }

  function toast(msg) {
    const t = $("#shellToast");
    if (!t) return;
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 2800);
  }

  function patientQuery(p) {
    const q = new URLSearchParams();
    for (const k of ["last", "first", "dob", "acct", "id"]) if (p && p[k]) q.set(k, p[k]);
    return q.toString();
  }
  // One tab per patient: the same person opened again goes to their tab.
  function tabKey(p) {
    if (!p) return "";
    const id = p.id || "", last = (p.last || "").trim().toLowerCase(), first = (p.first || "").trim().toLowerCase();
    return id || last || first ? `${id}|${last}|${first}` : "";
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---------------------------------------------------------------- tabs
  function paintTabs() {
    const strip = $("#tabstrip");
    strip.innerHTML = "";
    strip.append(h("button", { type: "button", class: "tab home-tab" + (state.active === "home" ? " active" : ""), title: "Today, patients and saved notes",
      onclick: () => activate("home") }, h("span", { html: DDOS.icon("home") }), h("span", { class: "tab-label" }, "Home")));
    for (const t of state.tabs) {
      const close = h("span", { class: "tab-close", role: "button", tabindex: "0", title: "Close " + t.label, "aria-label": "Close " + t.label,
        onclick: (e) => { e.stopPropagation(); closeTab(t.key); }, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); closeTab(t.key); } },
        html: DDOS.icon("x") });
      // the visit type's colour on the tab, the same colour as on the calendar and the note's header
      const kind = t.visit && t.visit.color ? h("span", { class: "tab-kind", style: `--kind:${esc(t.visit.color)}`, title: t.visit.label, "aria-label": t.visit.label }) : null;
      strip.append(h("button", { type: "button", class: "tab" + (state.active === t.key ? " active" : ""), title: t.label + (t.visit ? " · " + t.visit.label : "") + (t.dirty ? " (unsaved changes)" : ""),
        onclick: () => activate(t.key), onauxclick: (e) => { if (e.button === 1) closeTab(t.key); } },
        kind, h("span", { class: "tab-label" }, t.label), t.dirty ? h("span", { class: "tab-dot", "aria-label": "unsaved" }) : null, close));
    }
    strip.append(h("button", { type: "button", class: "tab-new", title: "New note (no patient yet)", "aria-label": "New note", onclick: () => openChart(null), html: DDOS.icon("plus") }));
    const active = strip.querySelector(".tab.active");
    if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function hosts() {
    const view = $("#view");
    let home = $("#homeView"), doc = $("#documentHost");
    if (!home) {
      home = h("div", { class: "home-view", id: "homeView" },
        h("nav", { class: "home-nav steps", "aria-label": "Home" }, h("div", { class: "steps-title" }, "Home"), h("div", { class: "step-list", id: "homeList" })),
        h("div", { class: "home-content", id: "homeContent" }));
      view.append(home);
    }
    if (!doc) { doc = h("div", { class: "document-view", id: "documentHost", hidden: true }); view.append(doc); }
    return { home, doc };
  }

  function paintHome() {
    const list = $("#homeList");
    list.innerHTML = "";
    const items = [...HOME];
    const permissions = state.auth?.permissions || [];
    if (permissions.includes("*") || permissions.includes("users.manage")) items.push(["users", "Users & groups", "users"]);
    for (const [id, label, icon] of items) list.append(h("button", { type: "button", class: "step" + (state.view === id ? " active" : ""), onclick: () => go(id) },
      h("span", { class: "step-ico", html: DDOS.icon(icon) }), h("span", {}, label)));
  }

  function paintStatus() {
    const left = $("#statusLeft"), right = $("#statusRight");
    if (left) {
      const open = state.tabs.length ? `${state.tabs.length} patient${state.tabs.length === 1 ? "" : "s"} open` : "No patients open";
      left.textContent = state.active === "home" ? open : "";
    }
    const noteStatus = $("#noteStatus");
    if (noteStatus) noteStatus.hidden = state.active === "home";
    if (right) right.textContent = [state.status.demo ? "Demo clinic" : (state.status.clinic || ""), state.status.version ? "DizzyDoctor " + state.status.version : ""].filter(Boolean).join("  ·  ");
  }

  function render() {
    paintTabs();
    const { home, doc } = hosts();
    if (state.active === "home") {
      doc.hidden = true;
      home.hidden = false;
      paintHome();
      const content = $("#homeContent");
      content.innerHTML = "";
      useModule(state.view, content);
    } else {
      home.hidden = true;
      doc.hidden = false;
      useModule("note", doc);                       // mounted once; the note keeps every tab's state itself
    }
    $("#settingsBtn").classList.toggle("active", state.active === "home" && state.view === "settings");
    paintStatus();
  }

  async function loadChartData(tab) {
    const key = tab.key;
    let chart = { patient: tab.patient, visits: [], documents: [], notes: [], facts: {} };
    let snap = {};
    try { chart = await api.get("/api/chart?" + patientQuery(tab.patient)); } catch { /* empty chart */ }
    try { snap = await api.get("/api/snapshot?" + patientQuery(tab.patient)); } catch { /* no note */ }
    tab.chart = chart || {};
    tab.snap = snap || {};
    if (state.active !== key) return;
    state.chart = tab.chart;
    state.snap = tab.snap;
    state.loadedFor = key;
    emit("chart:loaded", tab.patient);
  }

  async function activate(key, patient) {
    if (key !== state.active && window.NoteWriter && NoteWriter.busy()) { toast("Stop the recording before switching tabs."); return; }
    state.active = key;
    const tab = state.tabs.find((t) => t.key === key);
    if (key !== "home" && !tab) { state.active = "home"; }
    state.patient = tab ? tab.patient : null;
    state.chart = tab ? (tab.chart || null) : null;
    state.snap = tab ? (tab.snap || null) : null;
    render();
    if (!tab || !window.NoteWriter) return;
    await NoteWriter.activate(key, patient);
    if (tab.patient && (tab.patient.last || tab.patient.first || tab.patient.id) && !tab.chart) await loadChartData(tab);
    else if (tab.chart) emit("chart:loaded", tab.patient);
  }

  async function openChart(p) {
    const patient = p ? { ...p } : null;
    let key = tabKey(patient);
    let tab = key && state.tabs.find((t) => t.key === key);
    if (tab && patient && patient.path && state.active === key && window.NoteWriter && !NoteWriter.mayReplace()) return;
    if (!tab) {
      key = key || `new:${++newCount}`;
      tab = { key, patient: patient && (patient.last || patient.first || patient.id) ? patient : null, label: fmt.name(patient) || "New note" };
      state.tabs.push(tab);
    }
    await activate(key, patient || {});
  }

  function closeTab(key) {
    if (window.NoteWriter && !NoteWriter.close(key)) return;
    const i = state.tabs.findIndex((t) => t.key === key);
    if (i < 0) return;
    state.tabs.splice(i, 1);
    if (state.active === key) { const next = state.tabs[i] || state.tabs[i - 1]; activate(next ? next.key : "home"); }
    else { paintTabs(); paintStatus(); }
  }

  async function openOffice(tab) {
    try { await api.post("/api/classic", { tab: tab || "billing" }); toast("Opening the office app"); }
    catch (e) { toast(e.message || "The office app isn't available on this computer."); }
  }

  function openPatient(p, opts) {
    const patient = { ...(p || {}) };
    const params = (opts && opts.params) || {};
    if (params.path) patient.path = params.path;
    openChart(patient).then(() => { if (params.tab && window.NoteWriter) NoteWriter.showTab(params.tab); });
  }

  function go(view, opts) {
    const params = (opts && opts.params) || {};
    if (view === "chart" || view === "billing" || view === "document" || view === "superbill") {   // the open patient's note
      const tab = state.tabs.find((t) => t.key === state.active) || state.tabs[state.tabs.length - 1];
      if (!tab) { openChart(null); return; }
      activate(tab.key).then(() => {
        const path = (opts && opts.path) || params.path;
        if (path && window.NoteWriter) { if (NoteWriter.mayReplace()) NoteWriter.open({ ...(tab.patient || {}), path }); }
        else if (window.NoteWriter && (params.tab || view === "billing" || view === "superbill")) NoteWriter.showTab(params.tab || (view === "superbill" ? "superbill" : "billing"));
      });
      return;
    }
    state.view = HOME.some(([id]) => id === view) || view === "settings" || view === "users" ? view : "today";
    activate("home");
  }

  $("#billingBtn").insertAdjacentHTML("afterbegin", DDOS.icon("window"));
  $("#billingBtn").addEventListener("click", () => openOffice("billing"));
  $("#settingsBtn").insertAdjacentHTML("afterbegin", DDOS.icon("sliders"));
  $("#settingsBtn").addEventListener("click", () => go("settings"));
  $("#userBtn").addEventListener("click", async () => {
    try { await api.post("/api/auth/logout", {}); } catch { /* reloading clears the in-memory screen either way */ }
    location.reload();
  });
  $("#view").addEventListener("click", (e) => {
    if (e.target.closest("[data-classic]")) { openOffice(e.target.closest("[data-classic]").dataset.classic || ""); return; }
    if (e.target.closest("#jumpNoteBilling")) go("billing");
  });

  const search = $("#globalSearch");
  const results = $("#globalResults");
  let searchTimer;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      const q = search.value.trim();
      if (!q) { results.hidden = true; return; }
      let rows = [];
      try { rows = await api.get("/api/patients?q=" + encodeURIComponent(q)); } catch { rows = []; }
      const fill = DDOS.modules.patients && DDOS.modules.patients.fill;
      if (fill) fill(results, Array.isArray(rows) ? rows : [], (p) => { results.hidden = true; search.value = ""; openChart(p); });
      results.hidden = false;
    }, 160);
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { results.hidden = true; search.blur(); }
    if (e.key === "Enter") { const first = results.querySelector("button:not([disabled])"); if (first && !results.hidden) { e.preventDefault(); first.click(); } }
    if (e.key === "ArrowDown") { const first = results.querySelector("button:not([disabled])"); if (first && !results.hidden) { e.preventDefault(); first.focus(); } }
  });
  results.addEventListener("keydown", (e) => {
    const items = [...results.querySelectorAll("button:not([disabled])")], i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown" && i >= 0) { e.preventDefault(); (items[i + 1] || items[i]).focus(); }
    if (e.key === "ArrowUp" && i >= 0) { e.preventDefault(); (i ? items[i - 1] : search).focus(); }
    if (e.key === "Escape") { results.hidden = true; search.focus(); }
  });
  const mac = /Mac/i.test(navigator.platform || navigator.userAgent);
  $("#searchKey").textContent = mac ? "⌘K" : "Ctrl K";
  $("#searchIcon").innerHTML = DDOS.icon("search");
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") { e.preventDefault(); search.focus(); search.select(); }
    if (e.ctrlKey && e.key === "Tab") {           // Ctrl+Tab / Ctrl+Shift+Tab: next / previous tab
      e.preventDefault();
      const keys = ["home", ...state.tabs.map((t) => t.key)], i = keys.indexOf(state.active);
      activate(keys[(i + (e.shiftKey ? -1 : 1) + keys.length) % keys.length]);
    }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".patient-search")) results.hidden = true; });

  // Backspace in an empty text field steps back to the previous field (same as Shift+Tab).
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Backspace" || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.isComposing) return;
    const el = e.target;
    const textual = el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && /^(text|search|tel|email|url|number|date)?$/i.test(el.type));
    if (!textual || el.readOnly || el.disabled || el.value !== "") return;
    const fields = [...document.querySelectorAll("input, textarea, select, button, [tabindex]")].filter((f) =>
      f.tabIndex >= 0 && !f.disabled && f.type !== "hidden" && f.getClientRects().length && !f.closest("[hidden]"));
    const prev = fields[fields.indexOf(el) - 1];
    if (!prev) return;
    e.preventDefault();
    prev.focus();
    if (prev.select && /^(INPUT|TEXTAREA)$/.test(prev.tagName) && prev.type !== "number") { const n = prev.value.length; try { prev.setSelectionRange(n, n); } catch (_) {} }
  });

  async function startApp() {
    document.title = "DizzyDoctor";
    if (window.NoteWriter) NoteWriter.onTab = (key, info) => {
      const t = state.tabs.find((x) => x.key === key);
      if (!t) return;
      if (info.label) t.label = info.label;
      if ("dirty" in info) t.dirty = !!info.dirty;
      if ("visit" in info) t.visit = info.visit || null;
      if (info.patient && !t.patient) t.patient = info.patient;
      paintTabs();
    };
    api.get("/api/status").then((s) => {
      state.status = s || {};
      state.auth = s.user || state.auth;
      const badge = $("#demoBadge");
      if (badge) badge.hidden = !state.status.demo;
      const permissions = state.auth?.permissions || [];
      const allowed = (p) => permissions.includes("*") || permissions.includes(p);
      if (!state.status.classic || !allowed("billing.view")) $("#billingBtn").hidden = true;
      $("#settingsBtn").hidden = !allowed("settings.manage");
      $("#topUser").textContent = state.auth?.display_name || state.auth?.username || "User";
      paintStatus();
      if (state.active === "home" && state.view === "settings") render();
    }).catch(() => {});
    // The note mounts once, hidden, so notes left open last time come back as tabs.
    const { doc } = hosts();
    useModule("note", doc);
    if (window.NoteWriter) {
      await NoteWriter.ready();
      for (const t of NoteWriter.savedTabs()) if (!state.tabs.some((x) => x.key === t.key)) state.tabs.push(t);
    }
    const params = new URLSearchParams(location.search);
    if (params.get("path") || params.get("last") || params.get("name")) {
      openChart({
        path: params.get("path") || "",
        last: params.get("last") || "",
        first: params.get("first") || "",
        dob: params.get("dob") || "",
        acct: params.get("acct") || "",
        id: params.get("id") || "",
      });
    } else go("today");
  }

  DDOS.start = async function () {
    document.title = "DizzyDoctor";
    const gate = $("#loginGate"), shell = $("#appShell"), form = $("#loginForm"), error = $("#loginError"), submit = $("#loginSubmit");
    let auth = null;
    try { auth = await api.get("/api/auth/status"); } catch { auth = null; }
    const enter = async (user) => {
      state.auth = user;
      gate.hidden = true;
      shell.hidden = false;
      await startApp();
    };
    if (auth?.authenticated && auth.user) { await enter(auth.user); return; }
    gate.hidden = false;
    shell.hidden = true;
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); error.hidden = true; submit.disabled = true;
      try {
        const result = await api.post("/api/auth/login", { username: $("#loginUsername").value, password: $("#loginPassword").value });
        await enter(result.user);
      } catch (ex) {
        error.textContent = ex.message || "Sign in failed."; error.hidden = false; $("#loginPassword").select();
      } finally { submit.disabled = false; }
    }, { once: false });
    $("#loginUsername").focus();
  };
})();
