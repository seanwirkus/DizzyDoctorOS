/* Saved notes: work in progress (drafts) and the recent patients' filed notes. Finding a patient is the top bar's job.
 *
 * Data: GET /api/drafts   -> [{path, name, modified}]            drafts saved from the Progress Note screen
 *       GET /api/status   -> {recent: [{id, last, first, dob, acct}], ...}
 *       GET /api/notes?last=&first=  -> [{path, dos, kind, name}]  one patient's filed notes
 *       POST /api/open {path}        -> opens the file on this computer
 * Draft file names follow the office convention "LastFirst mm dd yy.pdf".
 */
(() => {
  const ICON = {
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/></svg>',
    pen: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20l4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10z"/><path d="M13.5 6.5l3 3"/></svg>',
    right: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 5 7 7-7 7"/></svg>',
  };

  let ctx = null, root = null, alive = false, offSaved = null;
  const state = { drafts: [], draftsError: "", recent: [], patients: [], notes: new Map(), openPatients: new Set() };

  // ------------------------------------------------------------------ helpers
  const key = (p) => `${(p.last || "").trim().toLowerCase()}|${(p.first || "").trim().toLowerCase()}`;
  const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

  /* "LastFirst mm dd yy.pdf" -> {last, first, dos}. The last name is the leading capitalised run; whatever follows is the
   * first name. When the run split is ambiguous (McDonaldJohn), a split that matches a patient we already know wins. */
  function parseDraftName(name, known) {
    const base = String(name || "").replace(/\.pdf$/i, "").trim();
    const m = /^(.*?)\s+(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})$/.exec(base);
    const who = (m ? m[1] : base).trim();
    const dos = m ? `${m[2]}/${m[3]}/${m[4]}` : "";
    const runs = who.match(/[A-Z]+[a-z]*/g) || [];
    let last = who, first = "";
    if (runs.length >= 2 && runs.join("") === who) {
      last = runs[0]; first = runs.slice(1).join(" ");
      if (known) for (let i = 1; i < runs.length; i++) {
        const cand = { last: runs.slice(0, i).join(""), first: runs.slice(i).join(" ") };
        if (known.has(key(cand))) { last = cand.last; first = cand.first; break; }
      }
    }
    return { last, first, dos };
  }

  /* "10/03/2026 05:21 PM" -> ms since epoch (date only when the time is missing). */
  function stamp(s) {
    const d = ctx.fmt.parse(s); if (!d) return 0;
    const t = /(\d{1,2}):(\d{2})\s*([AP]M)/i.exec(s || "");
    if (t) { let hh = +t[1] % 12; if (/p/i.test(t[3])) hh += 12; d.setHours(hh, +t[2]); }
    return d.getTime();
  }
  /* "10/03/2026 05:21 PM" -> "Today, 5:21 PM" / "Yesterday, 5:21 PM" / "Sep 30, 2026, 5:21 PM" */
  function when(s) {
    const d = ctx.fmt.parse(s); if (!d) return s || "";
    const t = /(\d{1,2}):(\d{2})\s*([AP]M)/i.exec(s || "");
    const time = t ? `${+t[1]}:${t[2]} ${t[3].toUpperCase()}` : "";
    const days = Math.round((startOfDay(new Date()) - startOfDay(d)) / 864e5);
    const label = days === 0 ? "Today" : days === 1 ? "Yesterday" : ctx.fmt.short(d);
    return time ? `${label}, ${time}` : label;
  }
  function ago(s) {
    const d = ctx.fmt.parse(s); if (!d) return "";
    const days = Math.round((startOfDay(new Date()) - startOfDay(d)) / 864e5);
    if (days <= 0) return "today"; if (days === 1) return "yesterday"; if (days < 14) return `${days} days ago`;
    if (days < 60) return `${Math.round(days / 7)} weeks ago`; if (days < 730) return `${Math.round(days / 30.4)} months ago`;
    return `${Math.floor(days / 365)} years ago`;
  }
  function svg(name, cls) { const s = ctx.h("span", { class: "notes-icon" + (cls ? " " + cls : ""), "aria-hidden": "true" }); s.innerHTML = ICON[name]; return s; }

  function patientSub(p) {
    const { fmt } = ctx; const age = p.dob ? fmt.age(p.dob) : "";
    return [p.dob ? fmt.date(p.dob) : "", age !== "" ? `${age} y` : "", p.acct ? `Pt # ${p.acct}` : ""].filter(Boolean).join(" · ");
  }
  function knownPatients() { const m = new Map(); for (const p of state.recent) m.set(key(p), p); return m; }

  async function openFile(path, name) {
    const { api, toast } = ctx;
    toast(`Opening ${name || "file"}…`, { ms: 1800 });
    try { const r = await api.post("/api/open", { path }); if (r && r.error) toast(r.error); }
    catch (e) { toast("Couldn't open that file."); }
  }

  // ------------------------------------------------------------------ data
  async function refresh(announce) {
    const { api, toast } = ctx;
    const [drafts, status] = await Promise.all([api.get("/api/drafts").catch(() => null), api.get("/api/status").catch(() => null)]);
    if (!alive) return;
    if (Array.isArray(drafts)) { state.drafts = drafts.slice().sort((a, b) => stamp(b.modified) - stamp(a.modified)); state.draftsError = ""; }
    else { state.drafts = []; state.draftsError = (drafts && drafts.error) || "Couldn't read the drafts folder."; }
    const recent = status && Array.isArray(status.recent) ? status.recent : (ctx.status && ctx.status.recent) || [];
    const seen = new Set(); state.recent = [];
    for (const p of recent) { if (!p || !(p.last || p.first)) continue; const k = key(p); if (seen.has(k)) continue; seen.add(k); state.recent.push(p); }
    const known = knownPatients();
    state.patients = state.recent.slice();
    for (const draft of state.drafts) {
      const parsed = parseDraftName(draft.name, known), k = key(parsed);
      if (!known.has(k)) { const p = { last: parsed.last, first: parsed.first }; known.set(k, p); state.patients.push(p); }
    }
    state.notes = new Map(state.patients.map((p) => [key(p), null]));
    renderPatients();
    const lists = await Promise.all(state.patients.map(p =>
      api.get("/api/notes?" + new URLSearchParams({ last: p.last || "", first: p.first || "" }))
        .then(r => Array.isArray(r) ? r : []).catch(() => [])));
    if (!alive) return;
    state.patients.forEach((p, i) => state.notes.set(key(p), lists[i].slice().sort((a, b) => String(b.dos || "").localeCompare(String(a.dos || "")) || String(b.name || "").localeCompare(String(a.name || "")))));
    renderPatients();
    if (announce) toast("Notes are up to date.", { ms: 1600 });
  }

  // ------------------------------------------------------------------ opening a patient
  function focusSearch() { const s = document.getElementById("globalSearch"); if (s) { s.focus(); s.select(); } }

  // ------------------------------------------------------------------ one collapsed section per patient
  function renderPatients() {
    const { h, fmt } = ctx; const sec = root.querySelector("#notesPatients"); if (!sec) return; sec.innerHTML = "";
    const n = state.patients.length;
    sec.append(h("div", { class: "notes-section-head" }, h("h2", {}, "Patients"), h("span", { class: "notes-count" }, n ? `${n} patient${n === 1 ? "" : "s"}` : "")));
    if (state.draftsError) sec.append(h("div", { class: "card notes-alert" }, h("span", {}, state.draftsError), h("button", { class: "btn", onclick: () => refresh() }, "Try drafts again")));
    if (!n) {
      sec.append(h("div", { class: "card notes-empty" }, h("div", { class: "empty" }, h("div", { class: "empty-title" }, "No saved notes yet"),
        h("div", { class: "muted" }, "Recent patients and notes in progress will appear here."),
        h("button", { class: "btn notes-empty-action", onclick: focusSearch }, svg("search"), "Find a patient")))); return;
    }
    const known = knownPatients(), drafts = new Map();
    for (const d of state.drafts) { const k = key(parseDraftName(d.name, known)); if (!drafts.has(k)) drafts.set(k, []); drafts.get(k).push(d); }
    const wrap = h("div", { class: "notes-patients" });
    for (const p of state.patients) {
      const k = key(p), patientDrafts = drafts.get(k) || [], list = state.notes.get(k);
      const counts = [];
      if (patientDrafts.length) counts.push(`${patientDrafts.length} draft${patientDrafts.length === 1 ? "" : "s"}`);
      if (list && list.length) counts.push(`${list.length} filed`);
      if (list === null) counts.push("loading notes");
      const details = h("details", { class: "card notes-patient", open: state.openPatients.has(k) },
        h("summary", { class: "notes-patient-summary" },
          h("div", { class: "notes-avatar" }, fmt.initials(p) || "?"),
          h("div", { class: "notes-who-text" }, h("div", { class: "notes-name" }, fmt.name(p) || "Unknown patient"), h("div", { class: "notes-sub" }, patientSub(p) || "Not yet matched to the directory")),
          h("span", { class: "spacer" }),
          patientDrafts.length ? h("span", { class: "chip notes-draft-chip" }, "Draft in progress") : null,
          h("span", { class: "notes-count" }, counts.join(" · ") || "No notes"),
          h("span", { class: "notes-chevron", "aria-hidden": "true" }, svg("right"))));
      const body = h("div", { class: "notes-patient-body" },
        h("div", { class: "notes-patient-tools" }, h("button", { class: "btn", onclick: () => ctx.openPatient(p, { params: { tab: "chart:snapshot" } }) }, "Open patient SnapShot")));
      if (patientDrafts.length) {
        body.append(h("div", { class: "subhead" }, "In progress"), h("div", { class: "notes-list" }, patientDrafts.map((d) => {
          const parsed = parseDraftName(d.name, known), dos = parsed.dos ? fmt.short(parsed.dos) : "Undated";
          return h("div", { class: "notes-row notes-draft-row" },
            h("div", { class: "notes-row-main" }, h("div", { class: "notes-row-date" }, dos, h("span", { class: "tag amber" }, "Draft"), h("span", { class: "notes-ago muted" }, when(d.modified))), h("div", { class: "notes-file", title: d.path || d.name }, d.name)),
            h("div", { class: "notes-actions" }, h("button", { class: "btn primary", onclick: () => ctx.openPatient(p, { sub: "note", params: { path: d.path } }) }, svg("pen"), "Resume"), h("button", { class: "btn", onclick: () => openFile(d.path, d.name) }, "Open file")));
        })));
      }
      body.append(h("div", { class: "subhead" }, "Filed notes"));
      if (list === null) body.append(h("div", { class: "notes-loading muted" }, h("span", { class: "spinner" }), "Loading notes…"));
      else if (!list.length) body.append(h("div", { class: "notes-empty-inline muted" }, "No filed notes for this patient yet."));
      else body.append(h("div", { class: "notes-list" }, list.map(note => h("div", { class: "notes-row" },
        h("div", { class: "notes-row-main" }, h("div", { class: "notes-row-date" }, note.dos ? fmt.short(note.dos) : (parseDraftName(note.name).dos ? fmt.short(parseDraftName(note.name).dos) : "Undated"), h("span", { class: "tag " + (note.kind === "final" ? "green" : "amber") }, note.kind === "final" ? "Final" : "Draft"), note.dos ? h("span", { class: "notes-ago muted" }, ago(note.dos)) : null), h("div", { class: "notes-file", title: note.path || note.name }, note.name)),
        h("div", { class: "notes-actions" }, h("button", { class: "btn", onclick: () => ctx.openPatient(p, { sub: "note", params: { path: note.path } }) }, "Open"), h("button", { class: "btn", onclick: () => ctx.openPatient(p, { sub: "note", params: { from: note.path } }) }, svg("pen"), "New visit from this note"))))));
      details.append(body);
      details.addEventListener("toggle", () => { if (details.open) state.openPatients.add(k); else state.openPatients.delete(k); });
      wrap.append(details);
    }
    sec.append(wrap);
  }

  // ------------------------------------------------------------------ module
  DDOS.register("notes", {
    title: "Notes",
    async mount(el, c) {
      ctx = c; alive = true;
      // A draft handed straight to Notes (?path=...) resumes it for its patient.
      if (ctx.params && ctx.params.path) {
        const parsed = parseDraftName(ctx.params.path.split(/[\\/]/).pop());
        el.append(ctx.h("div", { class: "loading" }, ctx.h("div", { class: "spinner" }), `Opening ${ctx.fmt.name(parsed) || "the draft"}…`));
        ctx.openPatient({ last: parsed.last, first: parsed.first }, { sub: "note", params: { path: ctx.params.path } });
        return;
      }
      root = ctx.h("div", { class: "notes" }, ctx.h("div", { class: "day-head" }, ctx.h("div", {}, ctx.h("h1", {}, "Saved notes"), ctx.h("p", {}, "Each patient stays collapsed until you need their drafts or filed notes."))), ctx.h("section", { class: "notes-section", id: "notesPatients" }));
      el.append(root);
      const tools = document.getElementById("headTools");
      if (tools) { tools.innerHTML = ""; tools.append(ctx.h("button", { class: "btn notes-tool", title: "Re-read drafts and recent notes", onclick: () => refresh(true) }, svg("refresh"), "Refresh")); }
      offSaved = ctx.on("note:saved", () => { if (alive) refresh(); });
      root.querySelector("#notesPatients").append(ctx.h("div", { class: "loading" }, ctx.h("div", { class: "spinner" }), "Loading notes…"));
      await refresh();
    },
    unmount() {
      alive = false;
      if (offSaved) offSaved(); offSaved = null;
      root = null;
    },
  });
})();
