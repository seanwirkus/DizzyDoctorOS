/* SnapShot: the patient on one screen. Who they are, what they came in with, and where it affects them: a body map
   coloured by complaint (numbness, pain, weakness, headache, hearing, tinnitus, vertigo), read off the note open on
   screen (or the latest filed note when none is open). Click a complaint to see where it lands; click a region to
   mark or clear it yourself - those marks are saved with the note (values["distributions"]). */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const KINDS = { cc: "Chief complaint", symptom: "Symptom", impression: "Impression", exam: "Exam", study: "Study", manual: "Marked by you" };
  let ui = null, docClick = null, lastView = "dermatome";

  const cats = () => ((DDOS.bodymap && DDOS.bodymap.data) || {}).categories || [];
  const color = (id) => (cats().find((c) => c.id === id) || {}).color || "#8e8e93";
  const catLabel = (id) => (cats().find((c) => c.id === id) || {}).label || id;
  const regionLabel = (id) => (DDOS.bodymap ? DDOS.bodymap.label(id) : id);
  const dot = (id, title) => `<i class="snap-dot" style="--c:${esc(color(id))}" title="${esc(title || catLabel(id))}"></i>`;

  // ------------------------------------------------------------------ data
  async function mount(host, ctx) {
    host.innerHTML = `<div class="snap"><div class="loading"><span class="spinner"></span>Reading the note…</div></div>`;
    try { await DDOS.bodymap.catalog(); } catch { /* the map draws without labels */ }
    const live = window.NoteWriter && NoteWriter.values;
    const has = (k) => live && String(live[k] || "").trim();
    const liveHas = !!(has("Name") || has("ChiefComplaint") || has("impression-plan-descr"));
    let data = null, source = "filed";
    if (liveHas) { try { data = await ctx.api.post("/api/snapshot", { values: live }); source = "open"; } catch { data = null; } }
    if (!data || data.error) { data = ctx.snap || null; source = "filed"; }
    if (!host.isConnected) return;
    const empty = !data || (!data.name && !data.chief_complaint && !(data.impressions || []).length && !data.note);
    if (empty) {
      host.innerHTML = `<div class="stack"><div class="empty"><h2>No note on file</h2><p>SnapShot is filled from the latest progress note. Open Note to start one.</p></div></div>`;
      return;
    }
    ui = { host, ctx, data, source, editable: source === "open" && !!(window.NoteWriter && typeof NoteWriter.set === "function"),
           view: lastView, focus: null, pop: null, tip: null };
    render();
  }
  async function refresh() {
    if (!ui) return;
    const live = window.NoteWriter && NoteWriter.values;
    try { ui.data = await ui.ctx.api.post("/api/snapshot", { values: live }); } catch (e) { ui.ctx.toast(e.message || "Couldn't reread the note"); return; }
    render();
  }
  // every region with what is marked there: { id, cats: { pain: { complaints, sources, manual, removed, unsided } } }
  function regionInfo() {
    const d = ui.data.distributions || {};
    const map = {};
    for (const c of d.complaints || []) {
      for (const m of c.marks || []) {
        const r = map[m.region] || (map[m.region] = { id: m.region, cats: {} });
        const s = r.cats[m.category] || (r.cats[m.category] = { complaints: [], sources: [], manual: false, removed: false, unsided: false });
        if (c.id === "manual") s.manual = true;
        else { if (!s.complaints.includes(c.id)) s.complaints.push(c.id); if (!s.sources.includes(m.source)) s.sources.push(m.source); }
        if (m.removed) s.removed = true;
        s.unsided = s.unsided || !!m.unsided;
      }
    }
    return map;
  }
  const activeCats = (r) => (r ? Object.keys(r.cats).filter((c) => !r.cats[c].removed) : []);

  // ------------------------------------------------------------------ the provider's own marks ("L5-L:pain -C6-R")
  function parseManual(text) {
    const adds = [], removes = [];
    for (const raw of String(text || "").trim().split(/[\s,;]+/)) {
      if (!raw) continue;
      const minus = raw.startsWith("-"), body = minus ? raw.slice(1) : raw;
      const [region, cat = ""] = body.split(":");
      if (!/^[A-Za-z0-9]+-[LR]$/.test(region)) continue;
      (minus ? removes : adds).push([region, cat]);
    }
    return { adds, removes };
  }
  const formatManual = ({ adds, removes }) => [...adds.map(([r, c]) => c ? `${r}:${c}` : r), ...removes.map(([r, c]) => c ? `-${r}:${c}` : `-${r}`)].join(" ");
  function setManual(m) {
    NoteWriter.set("distributions", formatManual(m));
    refresh();
  }
  function toggleMark(region, cat) {
    const r = regionInfo()[region], s = r && r.cats[cat];
    const m = parseManual((ui.data.distributions || {}).manual);
    const auto = !!(s && (s.complaints.length || s.sources.length));
    const active = !!(s && !s.removed);
    const not = (x, c) => !(x[0] === region && (c === undefined || x[1] === c));
    if (active) {
      m.adds = m.adds.filter((x) => not(x, cat));
      if (auto) m.removes.push([region, cat]);
    } else {
      // a whole-region clear ("-L5-L") turns into per-category clears for the others, so this one can come back
      if (m.removes.some((x) => x[0] === region && !x[1])) {
        m.removes = m.removes.filter((x) => not(x));
        for (const other of Object.keys(r ? r.cats : {})) if (other !== cat) m.removes.push([region, other]);
      }
      m.removes = m.removes.filter((x) => not(x, cat));
      if (!auto && !m.adds.some((x) => x[0] === region && x[1] === cat)) m.adds.push([region, cat]);
    }
    setManual(m);
  }
  function clearRegion(region) {
    const m = parseManual((ui.data.distributions || {}).manual);
    m.adds = m.adds.filter((x) => x[0] !== region);
    m.removes = m.removes.filter((x) => x[0] !== region);
    const r = regionInfo()[region];
    if (r && Object.values(r.cats).some((s) => s.complaints.length || s.sources.length)) m.removes.push([region, ""]);
    setManual(m);
  }
  function restoreRegion(region, cat) {
    const m = parseManual((ui.data.distributions || {}).manual);
    m.removes = m.removes.filter((x) => !(x[0] === region && (!cat || x[1] === cat || !x[1])));
    setManual(m);
  }

  // ------------------------------------------------------------------ drawing
  function render() {
    const { host, data, ctx } = ui;
    const scroll = host.scrollTop;
    const d = data.distributions || { complaints: [], regions: [], removed: [], denied: [] };
    const fmt = ctx.fmt || {};
    const age = data.age || (fmt.age ? fmt.age(data.dob) : "");
    const who = [age ? `${esc(age)} y/o${data.sex ? " " + esc(data.sex) : ""}` : (data.sex ? esc(data.sex) : ""), data.dob ? `DOB ${esc(data.dob)}` : "",
                 data.date ? `${ui.source === "open" ? "Visit" : "Seen"} ${esc(data.date)}` : ""].filter(Boolean);
    const visit = (data.visit || []).map((v) => `<span class="tag blue">${esc(v)}</span>`).join("");
    const flags = [
      data.allergies ? `<span class="tag ${data.allergies === "NKDA" ? "green" : "red"}">${esc(data.allergies === "NKDA" ? "NKDA" : "Allergies: " + data.allergies)}</span>` : "",
      data.falls === "yes" ? `<span class="tag orange">Falls</span>` : "",
      ...(data.pmh || []).map((p) => `<span class="tag">${esc(p)}</span>`),
    ].filter(Boolean).join("");
    const source = ui.source === "open"
      ? `<b>From the note open on screen</b><span>${ui.editable ? "Click a region on the map to mark or clear it; marks are saved with the note." : "Reading only."}</span>`
      : `<b>From the note filed ${esc(data.date || "")}</b><span>Open the note to mark regions yourself.</span>`;

    host.innerHTML = `<div class="snap">
      <div class="card snap-head">
        <div class="snap-id">
          <div class="snap-name">${esc(data.name || (fmt.name ? fmt.name(ctx.patient) : "") || "Patient")}</div>
          <div class="snap-meta">${who.map((w) => `<span>${w}</span>`).join("")}${visit}</div>
          <div class="snap-flags">${flags}</div>
        </div>
        <div class="snap-source">${source}</div>
      </div>
      <div class="snap-grid">
        <div class="card snap-complaints"><div class="card-head"><h2>Complaints and impressions</h2><span class="muted snap-hint">Click one to see where it lands</span></div><div class="snap-rows"></div></div>
        <div class="card snap-map">
          <div class="card-head"><h2>Affected distributions</h2>
            <div class="card-tools"><div class="seg snap-views" role="group" aria-label="Map view"></div></div>
          </div>
          <div class="snap-legend" role="group" aria-label="Legend"></div>
          <div class="snap-figures"></div>
          <div class="snap-other muted"></div>
        </div>
        <div class="card snap-side"><div class="card-head"><h2>Where it lands</h2></div><div class="snap-regions"></div></div>
      </div>
      <div class="snap-grid3">
        <div class="card"><div class="card-head"><h2>Medications</h2></div>${medsHtml(data)}</div>
        <div class="card"><div class="card-head"><h2>Key findings</h2></div>${findingsHtml(data)}</div>
        <div class="card"><div class="card-head"><h2>Plan</h2></div>${planHtml(data)}</div>
      </div>
    </div>`;
    paintComplaints(d);
    paintViews();
    paintLegend();
    paintFigures();
    paintRegions(d);
    host.querySelectorAll(".snap-report[data-path]").forEach((b) => b.addEventListener("click", () => {   // read it in Chart review
      const r = DDOS.modules && DDOS.modules.review;
      if (r && r.open) r.open(b.dataset.path);
      if (window.NoteWriter) NoteWriter.showTab("chart:review");
    }));
    host.scrollTop = scroll;
    if (ui.pop) openPopover(ui.pop.region, ui.pop.x, ui.pop.y);
  }

  function medsHtml(data) {
    const meds = data.meds || [];
    if (!meds.length) return `<div class="muted">No medications listed.</div>`;
    return `<div class="snap-list">${meds.map((m) => `<div class="snap-med${/\((stopped|d\s*\/?\s*c|did see|dee\s+cee|discontinued|not taking)/i.test(m) ? " stopped" : ""}">${esc(m)}</div>`).join("")}</div>`;
  }
  function findingsHtml(data) {
    const exam = (data.exam || []).map((e) => `<div class="snap-line"><b>${esc(e.group === "Cranial nerves" ? e.label : e.group || e.label)}</b> ${esc(e.text || "abnormal")}</div>`).join("");
    const ocul = (data.oculography || []).length ? `<div class="snap-line">${esc((data.oculography || []).join(", "))}${data.goggle_date ? ` <span class="muted">${esc(data.goggle_date)}</span>` : ""}</div>` : "";
    const studies = (data.studies || []).map((s) => `<div class="snap-line"><b>${esc(s.name)}</b> <span class="muted">${esc(s.date || "")}</span> ${esc(s.result || "")}</div>`).join("");
    // Reports on the drive (Zeto EEG, VNG, VEMP, EMG, chair) the note's rows don't list yet: read them from here.
    const onNote = new Set((data.studies || []).map((s) => String(s.name || "").trim().toLowerCase()));
    const docs = ((ui.ctx.chart && ui.ctx.chart.documents) || []).filter((d) => (d.group === "Neuro testing" || d.group === "Vestibular testing") && d.path);
    const reportKind = (d) => d.kind === "EEG" && /zeto/i.test(`${d.folder || ""} ${d.path || ""} ${d.name || ""}`) ? "Zeto EEG" : d.kind === "Chair report" ? "Chair" : d.kind;
    const reports = docs.filter((d) => !onNote.has(String(reportKind(d) || "").toLowerCase()))
      .map((d) => `<div class="snap-line"><b>${esc(reportKind(d))}</b> <span class="muted">${esc(d.modified || "")}</span> <button type="button" class="linkish snap-report" data-path="${esc(d.path)}" title="${esc(d.name || "")}">Open report</button></div>`).join("");
    const parts = [
      `<div class="subhead">Exam</div>${exam || `<div class="muted">No abnormal exam rows.</div>`}`,
      ocul ? `<div class="subhead">IR oculography</div>${ocul}` : "",
      `<div class="subhead">Studies</div>${studies || `<div class="muted">None on the note.</div>`}`,
      reports ? `<div class="subhead">Reports on file</div>${reports}` : "",
    ];
    return parts.join("");
  }
  function planHtml(data) {
    const imps = (data.impressions || []).map((i) => `<div class="snap-line"><b>${esc(i.n)}) ${esc(i.dx)}</b>${i.plan ? ` <span>${esc(i.plan)}</span>` : ""}</div>`).join("");
    const notes = String(data.plan || "").split("\n").map((l) => l.trim()).filter(Boolean).map((l) => `<div class="snap-line">${esc(l)}</div>`).join("");
    const next = [data.next, data.next_note].filter(Boolean).join(" · ");
    return [imps || `<div class="muted">No numbered impressions.</div>`, notes ? `<div class="subhead">Plan notes</div>${notes}` : "",
            next ? `<div class="subhead">Next visit</div><div class="snap-line">${esc(next)}</div>` : ""].join("");
  }

  function paintComplaints(d) {
    const box = ui.host.querySelector(".snap-rows");
    const info = regionInfo();
    const rows = (d.complaints || []).map((c) => {
      const live = (c.marks || []).filter((m) => !m.removed);
      const regions = [...new Set(live.map((m) => m.region))];
      const cs = [...new Set(live.map((m) => m.category))];
      const on = ui.focus && ui.focus.kind === "complaint" && ui.focus.id === c.id;
      const where = regions.length ? `${regions.length} region${regions.length === 1 ? "" : "s"}` : (c.kind === "manual" ? "" : "no distribution stated");
      return `<button type="button" class="snap-row${on ? " on" : ""}" data-complaint="${esc(c.id)}" aria-pressed="${on}">
        <span class="snap-kind">${esc(KINDS[c.kind] || c.kind)}</span>
        <span class="snap-row-main"><span class="snap-row-label">${esc(c.label)}</span>${c.detail ? `<span class="snap-row-detail">${esc(c.detail)}</span>` : ""}</span>
        <span class="snap-row-where">${cs.map((x) => dot(x)).join("")}<small>${esc(where)}</small></span>
      </button>`;
    });
    const denied = (d.denied || []).length ? `<div class="snap-denied muted">Denies: ${esc(d.denied.join(", "))}</div>` : "";
    box.innerHTML = (rows.join("") || `<div class="muted">Nothing recorded on the note yet.</div>`) + denied;
    box.querySelectorAll("[data-complaint]").forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.complaint;
      ui.focus = ui.focus && ui.focus.kind === "complaint" && ui.focus.id === id ? null : { kind: "complaint", id };
      paintComplaints(d); paintLegend(); paintMap(); paintRegions(d);
    }));
    void info;
  }
  function paintViews() {
    const seg = ui.host.querySelector(".snap-views");
    seg.innerHTML = "";
    for (const [id, label] of [["dermatome", "Dermatomes"], ["myotome", "Myotomes"], ["peripheral", "Peripheral nerves"]]) {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = label; b.className = ui.view === id ? "on" : ""; b.setAttribute("aria-pressed", String(ui.view === id));
      b.addEventListener("click", () => { if (ui.view !== id) { ui.view = lastView = id; paintViews(); paintFigures(); } });
      seg.append(b);
    }
  }
  function paintLegend() {
    const box = ui.host.querySelector(".snap-legend");
    const info = regionInfo();
    const counts = {};
    for (const r of Object.values(info)) for (const c of activeCats(r)) counts[c] = (counts[c] || 0) + 1;
    box.innerHTML = cats().map((c) => {
      const on = ui.focus && ui.focus.kind === "category" && ui.focus.id === c.id;
      return `<button type="button" class="snap-key${counts[c.id] ? "" : " none"}${on ? " on" : ""}" data-cat="${esc(c.id)}" aria-pressed="${!!on}" title="${counts[c.id] ? "Show only " + esc(c.label.toLowerCase()) : "Nothing marked"}">
        <i class="snap-dot" style="--c:${esc(c.color)}"></i>${esc(c.label)}${counts[c.id] ? `<b>${counts[c.id]}</b>` : ""}</button>`;
    }).join("");
    box.querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.cat;
      ui.focus = ui.focus && ui.focus.kind === "category" && ui.focus.id === id ? null : { kind: "category", id };
      paintComplaints(ui.data.distributions || {}); paintLegend(); paintMap(); paintRegions(ui.data.distributions || {});
    }));
  }
  function paintFigures() {
    const box = ui.host.querySelector(".snap-figures");
    box.innerHTML = "";
    for (const face of ["front", "back"]) box.append(DDOS.bodymap.figure(ui.view, face));
    const tip = document.createElement("div"); tip.className = "snap-tip"; tip.hidden = true; box.append(tip); ui.tip = tip;
    box.querySelectorAll(".region").forEach((el) => {
      el.addEventListener("mouseenter", (e) => showTip(el, e));
      el.addEventListener("mousemove", (e) => placeTip(e));
      el.addEventListener("mouseleave", () => { tip.hidden = true; });
      el.addEventListener("focus", (e) => showTip(el, e));
      el.addEventListener("blur", () => { tip.hidden = true; });
      el.addEventListener("click", (e) => clickRegion(el, e));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); clickRegion(el, e); } });
    });
    paintMap();
    const other = ui.host.querySelector(".snap-other");
    const here = DDOS.bodymap.idsInView(ui.view);
    const alternatives = DDOS.bodymap.VIEWS.filter((v) => v !== ui.view).map((v) => ({ id: v, ids: DDOS.bodymap.idsInView(v) }));
    const hidden = ((ui.data.distributions || {}).regions || []).filter((r) => { const base = r.id.replace(/-[LR]$/, ""); return !here.has(base) && alternatives.some((v) => v.ids.has(base)); });
    if (hidden.length) {
      const target = alternatives.find((v) => hidden.some((r) => v.ids.has(r.id.replace(/-[LR]$/, "")))) || alternatives[0];
      const targetLabel = target.id === "dermatome" ? "dermatomes" : target.id === "myotome" ? "myotomes" : "peripheral nerves";
      other.innerHTML = `${hidden.length} marked region${hidden.length === 1 ? " is" : "s are"} shown on the ${targetLabel} view: ${hidden.map((r) => esc(r.label)).join(", ")}. <button type="button" class="linkish" id="snapSwitchView">Show</button>`;
      other.querySelector("#snapSwitchView").addEventListener("click", () => { ui.view = lastView = target.id; paintViews(); paintFigures(); });
    } else other.innerHTML = ui.editable ? "Hover a region for what the note says there; click it to mark or clear." : "Hover a region for what the note says there.";
  }
  function hit(id, r) {
    const f = ui.focus;
    if (!f) return true;
    const cs = activeCats(r);
    if (f.kind === "complaint") return cs.some((c) => r.cats[c].complaints.includes(f.id) || (f.id === "manual" && r.cats[c].manual));
    if (f.kind === "category") return cs.includes(f.id);
    return id === f.id;
  }
  function paintMap() {
    const info = regionInfo();
    for (const el of ui.host.querySelectorAll(".bodymap .region")) {
      const id = el.dataset.id, r = info[id], cs = activeCats(r);
      el.classList.toggle("on", cs.length > 0);
      el.classList.toggle("multi", cs.length > 1);
      el.classList.toggle("unsided", cs.length > 0 && cs.every((c) => r.cats[c].unsided));
      el.style.setProperty("--c", cs[0] ? color(cs[0]) : "transparent");
      el.style.setProperty("--c2", cs[1] ? color(cs[1]) : (cs[0] ? color(cs[0]) : "transparent"));
      const h = cs.length > 0 && hit(id, r);
      el.classList.toggle("dim", cs.length > 0 && !!ui.focus && !h);
      el.classList.toggle("hit", !!ui.focus && h);
    }
  }
  function paintRegions(d) {
    const box = ui.host.querySelector(".snap-regions");
    const info = regionInfo();
    const rows = (d.regions || []).filter((r) => hit(r.id, info[r.id]) || !ui.focus).map((r) => `<div class="snap-region" data-region="${esc(r.id)}">
      <span class="snap-region-label">${esc(r.label)}${r.unsided ? ` <span class="snap-unsided" title="The note does not say which side">side?</span>` : ""}</span>
      <span class="snap-region-cats">${r.categories.map((c) => `<span class="snap-cat">${dot(c)}${esc(catLabel(c))}</span>`).join("")}</span>
      <span class="snap-region-src muted">${esc(r.sources.join(", "))}</span>
    </div>`);
    const removed = (d.removed || []).map((x) => `<div class="snap-region removed" data-region="${esc(x.region)}">
      <span class="snap-region-label">${esc(regionLabel(x.region))}</span>
      <span class="snap-region-cats muted">${x.category ? esc(catLabel(x.category)) : "everything"} cleared by you</span>
      ${ui.editable ? `<button type="button" class="linkish" data-restore="${esc(x.region)}" data-restore-cat="${esc(x.category || "")}">Restore</button>` : ""}
    </div>`);
    box.innerHTML = (rows.join("") || `<div class="muted">${ui.focus ? "Nothing on the map for this." : "No distribution found in the note" + (ui.editable ? " - click the map to mark one." : ".")}</div>`)
      + (removed.length ? `<div class="subhead">Cleared</div>${removed.join("")}` : "");
    box.querySelectorAll(".snap-region[data-region]:not(.removed)").forEach((row) => {
      row.addEventListener("mouseenter", () => ui.host.querySelectorAll(`.bodymap .region[data-id="${row.dataset.region}"]`).forEach((el) => el.classList.add("hover")));
      row.addEventListener("mouseleave", () => ui.host.querySelectorAll(".bodymap .region.hover").forEach((el) => el.classList.remove("hover")));
    });
    box.querySelectorAll("[data-restore]").forEach((b) => b.addEventListener("click", () => restoreRegion(b.dataset.restore, b.dataset.restoreCat)));
  }

  // ------------------------------------------------------------------ hover and click on the map
  function tipHtml(id) {
    const r = regionInfo()[id], cs = activeCats(r);
    const lines = cs.map((c) => { const s = r.cats[c]; const from = s.manual && !s.sources.length ? "marked by you" : s.sources.join(", ") + (s.manual ? ", and by you" : "");
      return `<div class="snap-tip-line">${dot(c)}<b>${esc(catLabel(c))}</b><span class="muted">${esc(from)}</span></div>`; });
    const description = DDOS.bodymap.detail ? DDOS.bodymap.detail(id, ui.view) : regionLabel(id);
    return `<div class="snap-tip-title">${esc(description)}</div>${lines.join("") || `<div class="muted">Not marked${ui.editable ? " - click to mark" : ""}</div>`}`;
  }
  function showTip(el, e) { ui.tip.innerHTML = tipHtml(el.dataset.id); ui.tip.hidden = false; placeTip(e, el); }
  function placeTip(e, el) {
    const box = ui.tip.parentElement.getBoundingClientRect();
    let x, y;
    if (e && e.clientX != null && e.type !== "focus") { x = e.clientX - box.left + 14; y = e.clientY - box.top + 14; }
    else { const b = (el || e.target).getBoundingClientRect(); x = b.right - box.left + 8; y = b.top - box.top; }
    const w = ui.tip.offsetWidth || 200, h = ui.tip.offsetHeight || 60;
    ui.tip.style.left = Math.max(0, Math.min(x, box.width - w)) + "px";
    ui.tip.style.top = Math.max(0, Math.min(y, box.height - h)) + "px";
  }
  function clickRegion(el, e) {
    const id = el.dataset.id;
    if (ui.tip) ui.tip.hidden = true;
    if (!ui.editable) {
      ui.focus = ui.focus && ui.focus.kind === "region" && ui.focus.id === id ? null : { kind: "region", id };
      paintComplaints(ui.data.distributions || {}); paintLegend(); paintMap(); paintRegions(ui.data.distributions || {});
      return;
    }
    const box = ui.host.querySelector(".snap-map").getBoundingClientRect();
    const b = el.getBoundingClientRect();
    const x = (e && e.clientX != null && e.type === "click" ? e.clientX : b.left + b.width / 2) - box.left, y = (e && e.clientY != null && e.type === "click" ? e.clientY : b.bottom) - box.top;
    openPopover(id, x, y);
  }
  function openPopover(region, x, y) {
    closePopover(false);
    const card = ui.host.querySelector(".snap-map");
    const r = regionInfo()[region] || { cats: {} };
    const pop = document.createElement("div");
    const description = DDOS.bodymap.detail ? DDOS.bodymap.detail(region, ui.view) : regionLabel(region);
    pop.className = "snap-pop"; pop.setAttribute("role", "dialog"); pop.setAttribute("aria-label", "Mark " + description);
    const chips = cats().map((c) => {
      const s = r.cats[c.id], on = !!(s && !s.removed), auto = !!(s && (s.complaints.length || s.sources.length));
      return `<button type="button" class="chip small${on ? " on" : ""}" data-cat="${esc(c.id)}" style="--c:${esc(c.color)}" aria-pressed="${on}" title="${auto ? "From the note: " + esc(s.sources.join(", ")) : (on ? "Marked by you" : "Mark " + esc(c.label.toLowerCase()) + " here")}">${esc(c.label)}${auto ? `<small>note</small>` : ""}</button>`;
    }).join("");
    pop.innerHTML = `<div class="snap-pop-head"><b>${esc(description)}</b><button type="button" class="icon-btn" data-close aria-label="Close">${DDOS.icon("x")}</button></div>
      <div class="chips">${chips}</div>
      <div class="snap-pop-foot"><span class="muted">Click a category to mark or clear it here.</span>${Object.keys(r.cats).length ? `<button type="button" class="linkish" data-clear>Clear all here</button>` : ""}</div>`;
    card.append(pop);
    const w = pop.offsetWidth || 300, h = pop.offsetHeight || 140;
    pop.style.left = Math.max(8, Math.min(x + 8, card.clientWidth - w - 8)) + "px";
    pop.style.top = Math.max(8, Math.min(y + 8, card.clientHeight - h - 8)) + "px";
    ui.pop = { region, x, y, el: pop };
    pop.querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => toggleMark(region, b.dataset.cat)));
    const clear = pop.querySelector("[data-clear]"); if (clear) clear.addEventListener("click", () => { ui.pop = null; clearRegion(region); });
    pop.querySelector("[data-close]").addEventListener("click", () => closePopover(true));
    ui.host.querySelectorAll(`.bodymap .region[data-id="${region}"]`).forEach((el) => el.classList.add("picked"));
    setTimeout(() => {
      docClick = (ev) => { if (!pop.contains(ev.target) && !ev.target.closest(`.bodymap .region[data-id="${region}"]`)) closePopover(true); };
      document.addEventListener("click", docClick);
    }, 0);
  }
  function closePopover(forget) {
    if (docClick) { document.removeEventListener("click", docClick); docClick = null; }
    if (ui && ui.pop && ui.pop.el) ui.pop.el.remove();
    if (ui) { ui.host.querySelectorAll(".bodymap .region.picked").forEach((el) => el.classList.remove("picked")); if (forget) ui.pop = null; }
  }

  DDOS.register("snapshot", { mount, unmount() { closePopover(true); ui = null; } });
})();
