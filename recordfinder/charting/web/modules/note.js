/* The progress note. Fields are bound to the PDF form by name. NoteWriter.mount() puts this
   inside the shell's Note activity; it does not take over the window. */
(() => {
  let scope = document;
  const $ = (s, el = scope) => el.querySelector(s);
  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) if (k != null && k !== false) el.append(k.nodeType ? k : document.createTextNode(k));
    return el;
  };
  let api = {
    get: (p) => fetch(p, { credentials: "same-origin" }).then(r => r.json()),
    post: (p, body) => fetch(p, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(r => r.json()),
  };
  const hooks = { onUpdate: null };
  // Line icons from the shell (no emoji).
  const icon = (name) => { const t = document.createElement("template"); t.innerHTML = (window.DDOS && DDOS.icon) ? DDOS.icon(name) : ""; return t.content.firstChild || document.createTextNode(""); };

  // ------------------------------------------------------------------ state
  const S = { schema: null, phrases: null, values: {}, path: "", patient: null, tab: "build", at: "patient", scroll: 0, rejected: new Set(), focus: "", section: "hpi",
              inputs: new Map(), coding: null, undo: null, undoStack: [], redoStack: [], historyGroup: null, generated: {}, dirty: false, clock: { start: Date.now(), paused: 0, pausedAt: 0 }, status: {}, prep: null, prepDone: new Set() };
  const F = () => S.schema.fields;

  // Every change to the form participates in one note-wide history. Consecutive keystrokes in the same field are
  // one step; clicking another field, a checkbox, Build, dictation or a transcript starts a new step.
  const historySnapshot = () => JSON.stringify({ values: S.values, generated: S.generated });
  function syncHistory() {
    S.undo = S.undoStack && S.undoStack.length ? S.undoStack[S.undoStack.length - 1] : null;
    const ub = $("#undoBtn"), rb = $("#redoBtn");
    if (ub) ub.disabled = !(S.undoStack && S.undoStack.length);
    if (rb) rb.disabled = !(S.redoStack && S.redoStack.length);
  }
  function checkpoint(group = "change", force = false) {
    S.undoStack = Array.isArray(S.undoStack) ? S.undoStack : [];
    S.redoStack = Array.isArray(S.redoStack) ? S.redoStack : [];
    const now = Date.now(), recent = S.historyGroup;
    if (!force && recent && recent.group === group && now - recent.at < 850) { recent.at = now; return; }
    S.undoStack.push(historySnapshot());
    if (S.undoStack.length > 100) S.undoStack.shift();
    S.redoStack.length = 0;
    S.historyGroup = { group, at: now };
    syncHistory();
  }
  function clearHistory() { S.undoStack = []; S.redoStack = []; S.historyGroup = null; S.undo = null; syncHistory(); }
  function restoreHistory(raw) {
    const prev = JSON.parse(raw || "{}");
    if (prev && prev.values) { S.values = prev.values; S.generated = prev.generated || {}; }
    else if (prev) S.values = prev;
    S.dirty = true;
  }

  function setValue(name, val, { silent = false } = {}) {
    const spec = F()[name];
    const original = S.values[name];
    if (spec && spec.type === "checkbox") {
      val = !!val;
      if (!silent && original !== val) checkpoint(`field:${name}`);
      if (val && spec.exclusive != null) for (const [n, sp] of Object.entries(F())) if (n !== name && sp.exclusive === spec.exclusive && S.values[n]) { S.values[n] = false; refresh(n); }
    } else {
      if (val == null) val = "";
      if (!silent && original !== val) checkpoint(`field:${name}`);
    }
    if (name === "Allergies" && String(val).trim() && S.values.nkda) { S.values.nkda = false; refresh("nkda"); }
    if (name === "nkda" && val && String(S.values.Allergies || "").trim()) { toast("Clear the allergy list before ticking NKDA."); val = false; }
    if (name === "referral-for") {
      const hasText = !!String(val || "").trim();
      if (!!S.values.PTcheck !== hasText) {
        S.values.PTcheck = hasText;
        refresh("PTcheck");
      }
    }
    if (name === "PTcheck") {
      if (val && !String(S.values["referral-for"] || "").trim()) {
        const defaultText = (S.phrases?.PT_REFERRAL && (S.phrases.PT_REFERRAL[1] || S.phrases.PT_REFERRAL[0])) || "Vestibular PT. Dx: gait and balance difficulties. 2x/week for 6-8 weeks.";
        S.values["referral-for"] = defaultText;
        refresh("referral-for");
      } else if (!val && String(S.values["referral-for"] || "").trim()) {
        S.values["referral-for"] = "";
        refresh("referral-for");
      }
    }
    const before = S.values[name];
    if (S.defaulted instanceof Set && S.defaulted.size) { const twin = name.endsWith("-denies") ? name : name + "-denies"; if (S.defaulted.has(twin) && SYMPTOMS.some(x => x.deny === twin)) S.defaulted.delete(twin); }
    S.values[name] = val;
    S.dirty = true;
    refresh(name);
    if (name === "DOB" || name === "Date") recomputeAge(name === "DOB" && !!parseDob(before) && !parseDob(val));
    if (name === "Date" && S.tab === "note") { const sec = $("#sec-meds"); if (sec) meds().render(sec); renderNav(); }
    if (!silent) onChange(name);
  }
  function refresh(name) {
    for (const el of S.inputs.get(name) || []) el._refresh && el._refresh();
  }
  let changeTimer = null;
  function onChange(name) {
    clearTimeout(changeTimer);
    changeTimer = setTimeout(() => { updateStoryboard(); recode(); keepDraft(); }, 250);
    if (["Name", "DOB", "Date"].includes(name)) updateStoryboard();
  }
  function bind(name, el, read, write) {
    if (!S.inputs.has(name)) S.inputs.set(name, []);
    S.inputs.get(name).push(el);
    el._refresh = () => write(S.values[name]);
    write(S.values[name]);
    return el;
  }

  // ------------------------------------------------------------------ widgets
  const label = (name, text) => h("label", { for: "f-" + name }, text ?? F()[name]?.label ?? name);
  function textField(name, { span = 4, placeholder = "", list = null, labelText } = {}) {
    const inp = h("input", { id: "f-" + name, type: "text", placeholder, autocomplete: "off", spellcheck: "false" });
    if (typeof list === "string") inp.setAttribute("list", list);
    else if (list) { const dl = h("datalist", { id: "dl-" + name }, list.map(v => h("option", { value: v }))); inp.setAttribute("list", "dl-" + name); wrap_dl(dl); }
    bind(name, inp, () => inp.value, v => { if (document.activeElement !== inp) inp.value = v ?? ""; });
    inp.addEventListener("input", () => setValue(name, inp.value));
    inp.addEventListener("focus", () => chooseBox(name, inp));
    return h("div", { class: `f s${span}` }, labelText === "" ? null : label(name, labelText), inp);
  }
  // A date typed any common way ("8/15/26", "081526", "Aug 15 2026", "today") or picked from the calendar, tidied to
  // mm/dd/yyyy when the field is left. `loose` keeps what was typed when it isn't a whole date ("2024", "~3/2024").
  function dateField(name, { span = 3, labelText, dob = false, loose = false, placeholder = "mm/dd/yyyy" } = {}) {
    const inp = h("input", { id: "f-" + name, type: "text", placeholder, autocomplete: "off", spellcheck: "false", "aria-haspopup": "dialog", "aria-expanded": "false" });
    const btn = h("button", { class: "date-btn", type: "button", tabindex: "-1", title: "Pick from the calendar (Alt+Down)", "aria-label": "Pick " + (labelText || F()[name]?.label || "the date") + " from the calendar" }, icon("calendar"));
    const wrap = h("div", { class: "date-input" }, inp, btn);
    const paintBad = () => {
      const v = inp.value.trim(), ok = !v || DDOS.dates.parse(v, { dob }) !== null;
      wrap.classList.toggle("bad", !ok && !loose);
      inp.title = ok || loose ? "" : (dob ? "Not a date of birth. Type it as mm/dd/yyyy or pick it from the calendar." : "Not a date. Type it as mm/dd/yyyy or pick it from the calendar.");
    };
    bind(name, inp, () => inp.value, v => { if (document.activeElement !== inp) { inp.value = v ?? ""; paintBad(); } });
    inp.addEventListener("input", () => { setValue(name, inp.value); wrap.classList.remove("bad"); });
    const tidy = () => { const d = DDOS.dates.parse(inp.value, { dob }); if (d && d !== inp.value.trim()) { inp.value = d; setValue(name, d); } paintBad(); };
    inp.addEventListener("blur", tidy);
    const pick = (keyboard) => DDOS.dates.open(inp, btn, { dob, keyboard, onPick: d => { inp.value = d; setValue(name, d); paintBad(); } });
    inp.addEventListener("keydown", e => {
      if (e.key === "Enter") tidy();
      else if (e.key === "ArrowDown" && e.altKey) { e.preventDefault(); pick(true); }
      else if (e.key === "Escape") DDOS.dates.close();
    });
    inp.addEventListener("focus", () => chooseBox(name, inp));
    btn.addEventListener("click", (e) => pick(e.detail === 0));            // Enter / Space on the button: keyboard
    return h("div", { class: `f s${span}` }, labelText === "" ? null : label(name, labelText), wrap);
  }
  // Age isn't typed: it follows from the date of birth and the date of service. With no DOB it shows the age the
  // story or dictation gave.
  function ageField({ span = 2 } = {}) {
    const out = h("output", { class: "age-out", id: "f-HPI-age", "aria-live": "polite" }), how = h("span", { class: "age-how" });
    const paint = () => {
      const dob = parseDob(S.values.DOB), a = dob ? ageOn(dob, parseDate(S.values.Date) || new Date()) : (S.values["HPI-age"] || "");
      out.textContent = a !== "" && a >= 0 ? String(a) : "\u2014";
      how.textContent = dob ? "from DOB" : (a !== "" ? "from the story" : "add DOB");
    };
    bind("HPI-age", out, () => 0, paint); bind("DOB", out, () => 0, paint); bind("Date", out, () => 0, paint);
    return h("div", { class: `f s${span}` }, h("label", { for: "f-HPI-age" }, "Age"), h("div", { class: "age-box", title: "Worked out from the date of birth and the date of service" }, out, how));
  }
  // Every drug name the medication list knows, for the boxes that name medications.
  function medNamesList() {
    const id = "dl-allmeds";
    if (!document.getElementById(id)) {
      const dl = h("datalist", { id });
      document.body.append(dl);
      meds().load().then(() => { const favs = ["Gabapentin", "Nurtec", "Topamax", "Memantine", "Meclizine", "Lyrica"]; for (const n of [...new Set([...favs, ...meds().names().slice().sort()])]) dl.append(h("option", { value: n })); });
    }
    return id;
  }
  // The box dictation fills: the one the provider put the cursor in, while they're still in that part of the note.
  function chooseBox(name, el) {
    if (autoFocusing) return;                              // a jump to a part isn't choosing a box
    S.focus = name;
    S.focusSec = (el && el.closest && el.closest(".note-sec")?.dataset.sec) || S.tab;
  }
  function dictationTarget() {
    const a = document.activeElement, sec = a && a.closest ? a.closest(".note-sec") : null;
    const here = sec ? sec.dataset.sec : (S.tab === "note" ? S.at : S.tab);
    return { section: DICTATE_SECTION[here] || S.section || "hpi", focus: S.focus && S.focusSec === here ? S.focus : "" };
  }
  let autoFocusing = false;
  // Show `el` only while `test()` is true. Anything already filled in stays visible, so nothing written is hidden.
  function when(el, names, test, own = []) {
    const has = () => own.some(n => { const v = S.values[n]; return v === true || (typeof v === "string" && v.trim() !== ""); });
    const sync = () => { el.hidden = !(test() || has()); };
    for (const n of [...names, ...own]) bind(n, el, () => 0, sync);
    return el;
  }
  const on = (n) => !!S.values[n];
  const dls = new Set();
  function wrap_dl(dl) { if (!dls.has(dl.id)) { dls.add(dl.id); document.body.append(dl); } }
  function expandTextArea(name, source) {
    const title = F()[name]?.label || name;
    const edit = h("textarea", { class: "long-edit-text", spellcheck: "true", "aria-label": title });
    edit.value = String(S.values[name] || "").replace(/\r/g, "\n");
    const overlay = h("div", { class: "modal long-edit", role: "dialog", "aria-modal": "true", "aria-label": `Expanded editor: ${title}` });
    const close = () => { overlay.remove(); source.focus({ preventScroll: true }); };
    edit.addEventListener("input", () => setValue(name, edit.value));
    overlay.append(h("div", { class: "modal-box long-edit-box" },
      h("div", { class: "long-edit-head" }, h("div", {}, h("h2", {}, title), h("p", {}, "This is the same PDF field, with room to work. Long text is added to a continuation page when the note is filed.")),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Close expanded editor", onclick: close }, icon("x"))),
      edit,
      h("div", { class: "modal-actions row" }, h("span", { class: "muted" }, "Changes are saved into the note as you type."), h("span", { class: "spacer" }), h("button", { class: "btn primary", type: "button", onclick: close }, "Done"))));
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
    overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } });
    document.body.append(overlay);
    setTimeout(() => { edit.focus(); edit.setSelectionRange(edit.value.length, edit.value.length); }, 0);
  }
  function textArea(name, { span = 12, cls = "", phrases = [], labelText, hint } = {}) {
    const ta = h("textarea", { id: "f-" + name, class: cls, spellcheck: "true" });
    bind(name, ta, () => ta.value, v => { if (document.activeElement !== ta) ta.value = (v ?? "").replace(/\r/g, "\n"); });
    ta.addEventListener("input", () => setValue(name, ta.value));
    ta.addEventListener("focus", () => chooseBox(name, ta));
    const text = labelText ?? F()[name]?.label ?? name;
    const head = h("div", { class: "f-label" }, text ? label(name, labelText) : null, h("span", { class: "spacer" }),
      phrases.length ? h("button", { class: "tool", type: "button", title: "The practice's standard lines", onclick: (e) => phrasePopover(e.currentTarget, name, phrases) }, icon("list"), "Phrases") : null,
      h("button", { class: "tool", type: "button", title: "Open a large editor for this PDF field", onclick: () => expandTextArea(name, ta) }, "Expand"),
      h("button", { class: "tool", type: "button", title: "Dictate into this box (F9)", "aria-label": "Dictate into " + (text || "this box"), onclick: () => { chooseBox(name, ta); toggleMic(); } }, icon("mic")));
    return h("div", { class: `f s${span}` }, head, ta, hint ? h("div", { class: "hint" }, hint) : null);
  }
  // A box with no label of its own: its Phrases / mic buttons go up into the card head, not on an empty row of their own.
  function liftTools(field) {
    const head = field.querySelector(".f-label");
    if (!head || head.querySelector("label")) return [];
    const tools = [...head.querySelectorAll(".tool")];
    head.remove();
    return tools;
  }
  function selectField(name, { span = 4, labelText, blank = true } = {}) {
    const sel = h("select", { id: "f-" + name }, blank ? h("option", { value: "" }, "—") : null, (F()[name].options || []).map(o => h("option", { value: o }, o.trim())));
    bind(name, sel, () => sel.value, v => { sel.value = v ?? ""; if (sel.value !== (v ?? "") && v) { sel.append(h("option", { value: v }, v)); sel.value = v; } });
    sel.addEventListener("change", () => { setValue(name, sel.value); sel.title = sel.value; });
    sel.title = sel.value;
    return h("div", { class: `f s${span}` }, labelText === "" ? null : label(name, labelText), sel);
  }
  // Epic-style colour for a ticked box: red = a positive / abnormal finding, green = negative / normal, blue = neutral
  // (visit type, timing, plan). Unticked boxes are grey.
  const POSITIVE = new Set(["dizziness", "hearingloss", "tinnitus", "gaitataxia", "headache", "ga-falls-yes", "debris-kerat", "debris-cerumen",
    "social-tobacco", "social-etoh", "social-drugs", "social-etoh-history"]);
  function tone(name) {
    if (/-(denies|normal|notab|none)$/.test(name) || ["nkda", "ga-falls-no", "social-denies"].includes(name)) return "neg";
    if (POSITIVE.has(name) || /^(dixhall|supine|gaze)-/.test(name) || (/^imp-/.test(name) && name !== "imp-mp")) return "pos";
    if (/^hist-/.test(name) && !/^hist-(pq|nochange|lastmens|other)$/.test(name)) return "pos";
    if (/^(assoc|hlassoc|t-assoc|ga-assoc|h-assoc)-/.test(name) && !/^assoc-(as|ad|au)$/.test(name)) return "pos";
    return "";
  }
  function check(name, { text, deny = false } = {}) {
    const b = h("button", { class: ["chip", tone(name), deny ? "deny" : ""].filter(Boolean).join(" "), type: "button" }, text ?? F()[name]?.label ?? name);
    bind(name, b, () => S.values[name], v => { b.classList.toggle("on", !!v); b.setAttribute("aria-pressed", String(!!v)); });
    b.addEventListener("click", () => setValue(name, !S.values[name]));
    return b;
  }
  // A square tick box, for lines the form prints with a [ ] in front (separate time, attestation).
  function box(name, ariaLabel) {
    const b = h("button", { class: "box", type: "button", role: "checkbox", "aria-label": ariaLabel || F()[name]?.label || name }, icon("check"));
    bind(name, b, () => S.values[name], v => { b.classList.toggle("on", !!v); b.setAttribute("aria-checked", String(!!v)); });
    b.addEventListener("click", () => setValue(name, !S.values[name]));
    return b;
  }
  function chips(names, { group } = {}) {
    const wrap = h("div", { class: "chips" }, names.map(n => check(n)));
    return group ? h("div", { class: "group" }, h("div", { class: "group-title" }, group), wrap) : wrap;
  }
  function segmented(names, { labels } = {}) {
    const seg = h("div", { class: "seg" });
    names.forEach((n, i) => {
      const b = h("button", { type: "button", class: tone(n) }, labels ? labels[i] : F()[n].label);
      bind(n, b, () => S.values[n], v => { b.classList.toggle("on", !!v); b.setAttribute("aria-pressed", String(!!v)); });
      b.addEventListener("click", () => { names.forEach(m => { if (m !== n && S.values[m]) { S.values[m] = false; refresh(m); } }); setValue(n, !S.values[n]); });
      seg.append(b);
    });
    return seg;
  }
  function card(title, body, { span = "", tools = [] } = {}) {
    return h("div", { class: `card ${span}` }, title ? h("div", { class: "card-head" }, h("h2", {}, title), tools.length ? h("span", { class: "card-tools" }, ...tools) : null) : null, ...[body].flat());
  }

  // ------------------------------------------------------------------ visit type
  // One set of visit types (visits.py) with one colour each, the same on the calendar, the tab, the header and Build.
  // The type ticks the form's box (Consult / Evaluation / Progress note / Urgent add-on) and is kept on the note as
  // visit-kind, with where it came from as visit-source: the schedule, Scope, the note's own boxes, or chosen here.
  const VT = () => (S.schema && S.schema.visit_types) || [];
  const VISIT_BOXES = ["neuro-consult", "neuro-eval", "neuro-progress", "neuro-urgent"];
  const BOX_KIND = { "neuro-consult": "consult", "neuro-eval": "eval", "neuro-progress": "followup", "neuro-urgent": "urgent" };
  const KIND_FIELDS = [...VISIT_BOXES, "visit-kind", "visit-source"];
  function visitKind() {
    const v = S.values, kind = String(v["visit-kind"] || ""), t = VT().find(x => x.id === kind);
    if (t && (!t.box || v[t.box])) return { id: kind, source: String(v["visit-source"] || "note"), type: t };
    const box = VISIT_BOXES.find(n => v[n]), k = box ? BOX_KIND[box] : "";
    return { id: k, source: k ? "note" : "", type: VT().find(x => x.id === k) || null };
  }
  const visitSource = (src) => ((S.schema && S.schema.visit_sources) || {})[src] || "";
  function setVisitKind(kind, source = "override", { silent = false } = {}) {
    const t = VT().find(x => x.id === kind);
    if (!t) return;
    for (const n of VISIT_BOXES) if (t.box && !!S.values[n] !== (n === t.box)) setValue(n, n === t.box, { silent: true });
    S.values["visit-kind"] = kind; S.values["visit-source"] = source; S.dirty = true;
    refresh("visit-kind"); refresh("visit-source");
    if (!silent) onChange("visit-kind");
    updateStoryboard();
  }
  // The chips: one per type in its colour, the chosen one filled; above them what is chosen and where it came from.
  function visitPicker({ compact = false, onPick = null } = {}) {
    const now = h("div", { class: "visit-now" });
    const chips = h("div", { class: "chips visit-chips", role: "radiogroup", "aria-label": "Visit type" }, VT().map(t => {
      const b = h("button", { type: "button", class: "chip kind", role: "radio", "data-kind": t.id, title: t.hint || t.label, style: `--kind:${t.color}` }, h("i", { "aria-hidden": "true" }), t.short);
      b.addEventListener("click", () => { setVisitKind(t.id, "override"); if (onPick) onPick(t); });
      return b;
    }));
    const wrap = h("div", { class: "visit-pick" + (compact ? " compact" : "") }, compact ? null : now, chips);
    const paint = () => {
      const { id, source, type } = visitKind();
      for (const b of chips.children) { const on = b.dataset.kind === id; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); }
      now.innerHTML = "";
      if (type) now.append(h("i", { style: `--kind:${type.color}` }), h("b", {}, type.label), h("span", { class: "muted" }, visitSource(source)),
                           type.family ? h("span", { class: "tag" }, type.family === "new" ? "New-patient E/M (9920x)" : "Established E/M (9921x)") : null);
      else now.append(h("span", { class: "muted" }, "Not chosen yet: the HPI opening and the visit level depend on it."));
    };
    for (const n of KIND_FIELDS) bind(n, wrap, () => 0, paint);
    return wrap;
  }
  // The header's badge: the type in its colour (painted with the rest of the header); a click goes to Patient to change it.
  function paintVisitBadge() {
    const b = $("#sbVisit");
    if (!b) return;
    const { type, source } = visitKind();
    b.hidden = !type;
    if (!type) return;
    b.style.setProperty("--kind", type.color);
    b.title = `${type.label}${source ? " · " + visitSource(source) : ""}. Change it on Patient or Build.`;
    b.replaceChildren(h("i", { "aria-hidden": "true" }), type.short, ...(source && source !== "note" ? [h("small", {}, visitSource(source))] : []));
  }
  // A patient opened from the schedule brings the booking's visit type along; a note that already has one keeps it.
  function applyScheduleKind() {
    const p = S.patient;
    if (!p || !p.visit_kind || !VT().some(t => t.id === p.visit_kind)) return;
    if (S.values["visit-kind"] && S.values["visit-source"] === "override") return;
    setVisitKind(p.visit_kind, "schedule", { silent: true });
  }
  const sectionFields = (id) => S.schema.sections.find(s => s.id === id).fields;
  const byPrefix = (p, exclude = []) => Object.keys(F()).filter(n => n.startsWith(p) && !exclude.includes(n));
  const grouped = (prefix) => { const out = {}; for (const n of byPrefix(prefix)) if (F()[n].type === "checkbox") (out[F()[n].group || ""] ||= []).push(n); return out; };

  // ------------------------------------------------------------------ phrase popover
  function fillTokens(text) {
    const v = S.values, dob = parseDate(v.DOB), dos = parseDate(v.Date) || new Date();
    const [last] = splitName(v.Name);
    const sex = v["hpi-female"] ? "female" : (v["hpi-male"] ? "male" : "***");
    const title = v["hpi-female"] ? "Ms." : (v["hpi-male"] ? "Mr." : "Mr./Ms.");
    const map = { title, last: last || "****", age: v["HPI-age"] || (dob ? ageOn(dob, dos) : "***"), hand: "R", sex, reason: "*****", pmhx: "****",
                  last_seen: lastSeen() || "**/**/**", date: fmtDate(dos), initials: S.status.initials || "**",
                  n: "#", minutes: "**", pa: "**", doctor: "", companion: "***", score: "**", value: "**" };
    return text.replace(/\{(\w+)\}/g, (m, k) => (k in map ? map[k] : m));
  }
  function lastSeen() {
    const dos = parseDate(S.values.Date) || new Date();
    const n = (S.patientNotes || []).find(x => x.kind === "final" && x.dos && x.dos < dos.toISOString().slice(0, 10));
    return n ? fmtDate(new Date(n.dos + "T12:00:00")) : "";
  }
  function phrasePopover(anchor, name, groups) {
    const pop = $("#phrasePop");
    pop.innerHTML = "";
    for (const g of groups) {
      pop.append(h("div", { class: "pop-title" }, g.title));
      for (const p of g.items) pop.append(h("button", { onclick: () => { insertText(name, fillTokens(p), g.mode || "line"); pop.hidden = true; } }, fillTokens(p)));
    }
    const r = anchor.getBoundingClientRect();
    pop.style.left = Math.min(r.left, innerWidth - 580) + "px"; pop.style.top = (r.bottom + 6) + "px";
    pop.hidden = false;
    setTimeout(() => document.addEventListener("click", function off(e) { if (!pop.contains(e.target)) { pop.hidden = true; document.removeEventListener("click", off); } }), 0);
  }
  function insertText(name, text, mode = "line", options = {}) {
    const cur = (S.values[name] || "").replace(/\r/g, "\n");
    const els = S.inputs.get(name) || [];
    const ta = els.find(e => e.tagName === "TEXTAREA" || e.tagName === "INPUT");
    let next;
    if (ta && document.activeElement === ta && ta.selectionStart != null) {
      const a = ta.selectionStart, b = ta.selectionEnd;
      next = cur.slice(0, a) + text + cur.slice(b);
    } else if (mode === "replace" || !cur.trim()) next = text;
    else next = cur.replace(/\s+$/, "") + (mode === "inline" ? " " : "\n") + text;
    setValue(name, next, options);
    if (ta) { ta.value = next; ta.focus(); }
  }

  // ------------------------------------------------------------------ the note: the PDF's four pages in one scroll
  // [id, label, page]. The left side lists the parts the way the PDF prints them; clicking one scrolls there and
  // scrolling lights up the part you're in. Build, Check and PDF are views of their own.
  const SECTIONS = [["patient", "Patient", 1], ["story", "Story", 1], ["meds", "Medications", 1], ["dizziness", "Dizziness", 1], ["hearing", "Hearing loss", 1],
                    ["tinnitus", "Tinnitus", 2], ["gait", "Gait", 2], ["headache", "Headache", 2], ["history", "History", 2],
                    ["exam", "Exam", 3], ["oculography", "Oculography", 3], ["studies", "Studies", 4], ["plan", "Plan", 4]];
  const PAGES = { 1: "HPI, medications", 2: "Symptoms, history", 3: "Exam, testing", 4: "Impression, plan" };
  // Build is the note's home: the HPI at its top decides the rest. "Full note" is the PDF's four pages to work through.
  const VIEWS = [["build", "Build"], ["note", "Full note"], ["billing", "Check"], ["superbill", "Superbill"], ["preview", "PDF"]];
  // The patient's chart, shown in the same panel: [id, label, shell module]
  const CHART = [["chart:snapshot", "SnapShot", "snapshot"], ["chart:review", "Chart review", "review"], ["chart:testing", "Results / testing", "testing"]];
  // What dictation fills in each part (dictation.py's sections), and the parts' older names (saved tabs, links).
  const DICTATE_SECTION = { patient: "hpi", story: "hpi", meds: "meds", dizziness: "dizziness", hearing: "hearing", tinnitus: "tinnitus", gait: "gait",
                            headache: "headache", history: "pmh", exam: "exam", oculography: "goggles", studies: "studies", plan: "impression",
                            build: "impression", billing: "plan", superbill: "plan", preview: "impression" };
  const OLD_STEPS = { summary: "patient", hpi: "story", symptoms: "dizziness", testing: "oculography" };
  const isSection = (id) => SECTIONS.some(([k]) => k === id);

  function renderNav() {
    // Note, Build, Check, PDF: one switch at the top, so the whole list fits on a laptop screen
    const sw = $("#viewSwitch");
    if (sw) {
      sw.innerHTML = "";
      for (const [id, label] of VIEWS) {
        const on = S.tab === id;
        const b = h("button", { type: "button", class: on ? "on" : "", "data-view": id, "aria-current": on ? "page" : false,
          onclick: () => { if (id === "note") goSection(S.at, { focus: false }); else if (S.tab !== id) showTab(id); } }, label);
        if (id === "billing" && S.coding?.warnings?.length) b.append(h("span", { class: "step-badge", title: "Checks to look at before filing" }, S.coding.warnings.length));
        if (id === "build" && openWarnings()) b.append(h("span", { class: "step-badge", title: "Points to settle on Build" }, String(openWarnings())));
        sw.append(b);
      }
    }
    const list = $("#pageList"); if (!list) return; list.innerHTML = "";
    list.classList.remove("dim");
    if (S.tab === "build") {
      const parts = [...(($("#panel") || document).querySelectorAll("[data-outline]"))];
      list.append(h("div", { class: "nav-page" }, h("b", {}, "Build node"), h("span", {}, "from HPI")));
      for (const el of parts) {
        const b = h("button", { class: "step build-step", type: "button", onclick: () => scrollBuildTo(el) }, h("span", {}, el.dataset.outline));
        if (el.dataset.mark) b.append(h("span", { class: "sec-mark " + el.dataset.mark, "aria-hidden": "true" }));
        if (el.dataset.outline === "Needs you" && openWarnings()) b.append(h("span", { class: "step-badge" }, String(openWarnings())));
        list.append(b);
      }
      list.append(h("div", { class: "nav-page nav-full", style: "margin-top:14px" }, h("b", {}, "Full note"), h("span", {}, "4 pages")));
      list.append(h("button", { class: "step", type: "button", onclick: () => goSection(S.at || "patient", { focus: false }) },
        h("span", {}, "View full note by page \u2192")));
    } else {
      list.append(h("button", { class: "step", type: "button", style: "margin-bottom:6px; font-weight:600; color:var(--accent)", onclick: () => showTab("build") },
        h("span", {}, "\u2190 Back to Build (HPI)")));
      let page = 0;
      for (const [id, label, pg] of SECTIONS) {
        if (pg !== page) { page = pg; list.append(h("div", { class: "nav-page", title: PAGES[pg] }, h("b", {}, "Page " + pg))); }
        const on = S.tab === "note" && S.at === id;
        const b = h("button", { class: "step" + (on ? " active" : ""), type: "button", "data-sec": id, "aria-current": on ? "location" : false, onclick: () => goSection(id) }, h("span", {}, label));
        const mark = sectionMark(id);
        if (mark) b.append(mark);
        list.append(b);
      }
    }
    const chart = $("#chartList"); if (!chart) return; chart.innerHTML = "";
    for (const [id, name] of CHART) chart.append(h("button", { class: "step" + (id === S.tab ? " active" : ""), type: "button", "aria-current": id === S.tab ? "page" : false,
      onclick: () => showTab(id) }, h("span", {}, name)));
  }
  // A dot beside a part: red = a symptom present, green = denied, orange = medications not reviewed this visit.
  function sectionMark(id) {
    if (!S.schema) return null;
    if (id === "meds" && !medsReviewed()) return h("span", { class: "step-dot", title: "Medication list not reviewed this visit", "aria-label": "not reviewed" });
    const sym = SYMPTOMS.find(x => x.id === id);
    if (sym && S.values[sym.on]) return h("span", { class: "sec-mark pos", title: "Present", "aria-label": "present" });
    if (sym && S.values[sym.deny]) return h("span", { class: "sec-mark neg", title: "Patient denies", "aria-label": "denied" });
    return null;
  }
  // SnapShot, Chart review and Results / testing are the shell's chart screens, drawn in this panel.
  function renderChart(id) {
    const p = $("#panel"), mod = DDOS.modules[(CHART.find(([k]) => k === id) || [])[2]];
    const host = h("div", { class: "chart-host" });
    p.append(host);
    if (!S.patient || !DDOS.context) host.append(h("div", { class: "stack" }, h("div", { class: "empty" }, h("h2", {}, "No patient open"), h("p", {}, "Find a patient in the search box at the top to see their chart."))));
    else if (mod) mod.mount(host, DDOS.context());
  }
  const RENDER = {
    patient: (p) => renderPatient(p), story: (p) => renderStory(p), meds: (p) => meds().render(p),
    dizziness: (p) => renderSymptom(p, "dizziness"), hearing: (p) => renderSymptom(p, "hearing"), tinnitus: (p) => renderSymptom(p, "tinnitus"),
    gait: (p) => renderSymptom(p, "gait"), headache: (p) => renderSymptom(p, "headache"), history: (p) => renderHistory(p),
    exam: (p) => renderExam(p), oculography: (p) => renderOculography(p), studies: (p) => renderStudies(p), plan: (p) => renderPlan(p),
  };
  // The whole note, page by page. `keepScroll` redraws it where the provider is (after a change elsewhere).
  function renderNote(target = null, { keepScroll = false, scrollTop = null } = {}) {
    const p = $("#panel");
    if (!p) return;
    const top = scrollTop != null ? scrollTop : (keepScroll && p.dataset.mode === "note" ? p.scrollTop : null);
    S.tab = "note"; S.focus = "";
    p.dataset.mode = "note"; p.innerHTML = ""; S.inputs.clear();
    const doc = h("div", { class: "note-doc" });
    p.append(doc);
    const prep = prepCard();
    if (prep) doc.append(prep);
    let sheet = null, page = 0;
    for (const [id, label, pg] of SECTIONS) {
      if (pg !== page) {
        page = pg;
        sheet = h("div", { class: "sheet", "data-page": String(pg) }, h("div", { class: "sheet-head" }, h("b", {}, "Page " + pg), h("span", {}, PAGES[pg])));
        doc.append(sheet);
      }
      const sec = h("section", { class: "note-sec", id: "sec-" + id, "data-sec": id, "aria-label": label, tabindex: "-1" });
      sheet.append(sec);
      RENDER[id](sec);
    }
    S.at = S.at && isSection(S.at) ? S.at : SECTIONS[0][0];
    renderNav();
    if (top != null) { p.scrollTop = top; spyScroll(); }
    else if (target && target !== SECTIONS[0][0]) goSection(target, { focus: false, smooth: false });
    else { p.scrollTop = 0; markActive(SECTIONS[0][0]); }
  }
  let navLock = 0;
  function markActive(id) {
    S.at = id; S.section = DICTATE_SECTION[id] || "hpi";
    for (const b of (($("#pageList") || {}).querySelectorAll ? $("#pageList").querySelectorAll(".step") : [])) {
      const on = b.dataset.sec === id;
      b.classList.toggle("active", on);
      if (on) b.setAttribute("aria-current", "location"); else b.removeAttribute("aria-current");
    }
  }
  // Scrolling lights up the part at the top of the panel (the last one once the page can't scroll further).
  function spyScroll() {
    const p = $("#panel");
    if (!p || Date.now() < navLock) return;
    if (S.tab === "build") {
      const parts = [...p.querySelectorAll("[data-outline]")];
      if (!parts.length) return;
      const line = p.getBoundingClientRect().top + 52;
      let cur = null;
      for (const el of parts) { if (el.getBoundingClientRect().top <= line) cur = el; else break; }
      if (p.scrollTop + p.clientHeight >= p.scrollHeight - 2 && parts.length) cur = parts[parts.length - 1];
      const target = cur || parts[0];
      for (const b of ($("#pageList")?.querySelectorAll(".build-step") || [])) {
        const on = b.textContent.trim().startsWith(target.dataset.outline);
        b.classList.toggle("active", on);
        if (on) b.setAttribute("aria-current", "location"); else b.removeAttribute("aria-current");
      }
      return;
    }
    if (S.tab !== "note") return;
    const secs = [...p.querySelectorAll(".note-sec")];
    const line = p.getBoundingClientRect().top + 48;
    let cur = null;
    for (const sec of secs) { if (sec.getBoundingClientRect().top <= line) cur = sec; else break; }
    if (p.scrollTop + p.clientHeight >= p.scrollHeight - 2 && secs.length) cur = secs[secs.length - 1];
    const id = (cur || secs[0] || {}).dataset?.sec;
    if (id && id !== S.at) markActive(id);
  }
  function goSection(id, { focus = true, smooth = true } = {}) {
    id = OLD_STEPS[id] || id;
    if (!isSection(id)) id = SECTIONS[0][0];
    const p = $("#panel");
    if (!p) return;
    if (S.tab !== "note" || !p.querySelector(".note-doc")) { S.at = id; renderNote(id); if (focus) focusPart(id); return; }
    const sec = $("#sec-" + id);
    if (!sec) return;
    navLock = Date.now() + (smooth ? 700 : 50);
    // the first part opens at the very top, with the last visit's follow-ups above it
    const top = id === SECTIONS[0][0] ? 0 : Math.max(0, p.scrollTop + sec.getBoundingClientRect().top - p.getBoundingClientRect().top - 12);
    p.scrollTo({ top, behavior: smooth ? "smooth" : "auto" });
    markActive(id);
    if (focus) focusPart(id);
  }
  // Keyboard at the start of a part: Tab goes on from there. It doesn't pick a box for dictation.
  function focusPart(id) {
    const sec = $("#sec-" + id);
    if (!sec) return;
    autoFocusing = true;
    try { sec.focus({ preventScroll: true }); } finally { autoFocusing = false; }
  }
  function showTab(id) {
    id = OLD_STEPS[id] || id;
    const p = $("#panel");
    if (!p) return;
    if (id === "note" || isSection(id)) { goSection(id === "note" ? S.at : id, { focus: false }); renderNav(); return; }
    S.tab = id; S.section = DICTATE_SECTION[id] || "hpi"; S.focus = "";
    p.innerHTML = ""; S.inputs.clear();
    renderNav();
    if (id.startsWith("chart:")) { closeReport(); p.dataset.mode = "chart"; renderChart(id); p.scrollTop = 0; return; }   // chart screens have their own viewer
    p.dataset.mode = id;
    ({ billing: renderBilling, preview: renderPreview, build: renderBuild, superbill: renderSuperbill })[id]?.();
    p.scrollTop = 0;
  }
  // Redraw whatever is on screen after a change made elsewhere, without losing the place.
  function refreshView() { if (S.tab === "note") renderNote(null, { keepScroll: true }); else showTab(S.tab); }

  // ------------------------------------------------------------------ Summary
  // Last-visit reference (grayed out) + follow-ups due. Reference only: nothing here writes into
  // the new note until the provider picks "Pull into plan". Checked items stay checked on this tab.
  function prepCard() {
    const prep = S.prep;
    if (!prep || (!prep.followups?.length && !prep.impressions?.length && !studyDocs().some(newSinceLastVisit))) return null;
    const done = S.prepDone instanceof Set ? S.prepDone : (S.prepDone = new Set());
    const open = (prep.followups || []).filter((f) => !done.has(f.id));
    const ref = (title, body) => h("div", { class: "prep-ref-block" }, h("div", { class: "prep-ref-title" }, title), h("div", { class: "prep-ref-body" }, body));
    const imps = (prep.impressions || []).map((i) => h("div", { class: "prep-line" }, h("b", {}, `${i.n}) ${i.dx}`), i.plan ? h("span", {}, ` — ${i.plan}`) : null));
    const studies = (prep.studies || []).slice(0, 5).map((s) => h("div", { class: "prep-line" }, h("b", {}, s.name), h("span", {}, ` ${s.date || ""}${s.result ? ": " + s.result.slice(0, 140) : " — no result on file"}`)));
    // Pulling one follow-up appends it to the plan box; "Pull all" takes every open one that is about this visit.
    // Follow-ups about the NEXT visit ("return in 3 months", "repeat VNG at next visit", "follow up after MRI") are
    // kept apart and not pulled unless the provider picks them, so last time's booking doesn't become today's plan.
    const pullOne = (f) => {
      const cur = (S.values["plan-descr"] || "").replace(/\s+$/, "");
      setValue("plan-descr", cur ? cur + "\n- Follow up: " + f.text : "- Follow up: " + f.text);
      done.add(f.id);
    };
    const thisVisit = (prep.followups || []).filter((f) => !nextVisitItem(f)), nextVisit = (prep.followups || []).filter(nextVisitItem);
    const pullAll = (items, what) => {
      const todo = items.filter((f) => !done.has(f.id));
      if (!todo.length) { toast(`Nothing open to pull${what ? " " + what : ""}.`); return; }
      todo.forEach(pullOne); S.dirty = true; toast(`${todo.length} follow-up${todo.length > 1 ? "s" : ""} added to the plan box`); refreshView();
    };
    const followRow = (f) => {
      const checked = done.has(f.id);
      const cb = h("input", { type: "checkbox", "aria-label": "Addressed: " + f.text });
      cb.checked = checked;
      cb.addEventListener("change", () => { checked ? done.delete(f.id) : done.add(f.id); S.dirty = true; refreshView(); });
      const pull = h("button", { class: "linkish", type: "button", title: "Append to the plan box" }, "Pull into plan");
      pull.addEventListener("click", () => { pullOne(f); S.dirty = true; toast("Added to the plan box"); refreshView(); });
      return h("label", { class: "prep-follow" + (checked ? " is-done" : "") }, cb, h("span", { class: "prep-follow-text" }, f.text), h("span", { class: "prep-follow-acts" }, h("span", { class: "prep-source" }, f.source), pull));
    };
    const followRows = thisVisit.map(followRow);
    const openNext = nextVisit.filter((f) => !done.has(f.id)).length;
    const nextRows = nextVisit.length ? h("details", { class: "more prep-next" },
      h("summary", {}, icon("right"), `${openNext} about the next visit`, h("span", { class: "muted" }, " — not pulled with the rest")),
      h("div", { class: "prep-follows compact" }, nextVisit.map(followRow),
        openNext > 1 ? h("div", { class: "row" }, h("button", { class: "linkish", type: "button", onclick: () => pullAll(nextVisit, "about the next visit") }, "Pull these too")) : null)) : null;
    const details = [
      ref("Reason for last visit", `${prep.chief_complaint || "\u2014"}${prep.onset ? " \u00b7 since " + prep.onset : ""}`),
      !followRows.length && imps.length ? ref("Assessment last time", imps) : null,
      studies.length ? ref("Studies last time", studies) : null,
      (prep.next || prep.next_note) ? ref("Follow-up booked then", [prep.next, prep.next_note].filter(Boolean).join(" \u00b7 ")) : null,
    ].filter(Boolean);
    const total = (prep.followups || []).length;
    const summary = total
      ? `${open.length} open follow-up${open.length === 1 ? "" : "s"} from the last visit`
      : "Review the last visit";
    const openHere = thisVisit.filter((f) => !done.has(f.id)).length;
    const pullAllBtn = openHere > 1 ? h("button", { class: "linkish prep-pull-all", type: "button", title: "Append every open follow-up about this visit to the plan box", onclick: () => pullAll(thisVisit) }, "Pull all into plan") : null;
    // Reports filed on the drive since then (a Zeto EEG, a VNG) sit above the fold: where each landed in the study
    // rows, or one click to put it there, and the report itself beside the note. Nothing to hunt for in other tabs.
    const freshDocs = studyDocs().filter(newSinceLastVisit);
    const rowOf = (d) => { const name = reportName(d), date = reportDate(d); return [1, 2, 3, 4, 5].find((i) => String(S.values[`x-study${i}`] || "").trim() === name && String(S.values[`x-study${i}-date`] || "").trim() === date) || 0; };
    const freshRows = freshDocs.length ? h("div", { class: "prep-fresh" },
      h("div", { class: "prep-ref-title" }, "New since last visit"),
      freshDocs.map((d) => { const r = rowOf(d); return h("div", { class: "prep-line prep-fresh-row" },
        h("span", { class: "tag orange" }, reportName(d)), h("span", { class: "prep-fresh-name", title: d.name }, d.name), h("span", { class: "muted" }, d.modified || ""),
        h("span", { class: "prep-follow-acts" },
          r ? h("button", { class: "linkish", type: "button", title: "Go to the study rows", onclick: () => goSection("studies") }, `In study row ${r}`)
            : h("button", { class: "linkish", type: "button", title: "Put it in the next empty study row with the report's impression", onclick: () => addStudy(d) }, "Add to studies"),
          d.path ? h("button", { class: "linkish", type: "button", onclick: (e) => viewReport(d, e.currentTarget.closest(".prep-fresh-row")) }, "Open") : null)); })) : null;
    return card(`Last visit ${prep.date || ""}`.trim(), [freshRows, h("details", { class: "prep-visit" },
      h("summary", {}, icon("right"), h("span", {}, summary), h("span", { class: "muted" }, "Click to expand")),
      h("div", { class: "prep-visit-body" },
        followRows.length ? h("div", { class: "prep-follows compact" }, followRows, pullAllBtn ? h("div", { class: "row" }, pullAllBtn) : null) : null,
        nextRows,
        details.length ? h("details", { class: "more prep-more" }, h("summary", {}, icon("right"), "Reason, studies and follow-up from last time"), h("div", { class: "prep-ref-grid" }, details)) : null))],
    { span: "prep-ref", tools: [freshDocs.length ? h("span", { class: "tag orange" }, `${freshDocs.length} new report${freshDocs.length > 1 ? "s" : ""}`) : null, h("span", { class: "tag" }, total ? `${open.length} of ${total} open` : "Reference only")].filter(Boolean) });
  }
  // A follow-up that is about the next visit rather than something to do today: the booking that was made, "return in
  // 3 months", "repeat the VNG at the next visit", "follow up after the MRI". Pending studies and referrals to chase, and
  // the assessments, are about this visit even when they say "RTC with worsening".
  const NEXT_VISIT_RE = /\b(?:next (?:visit|appointment|appt)|at (?:the )?(?:next|follow[- ]?up)|(?:return|rtc|follow[- ]?up|f\/u|see (?:him|her|them)? ?back|recheck|re-?evaluate|reassess)\s+(?:to clinic\s+)?(?:in|after|once|when|following|with)\b|\bin \d{1,2}(?:\s*-\s*\d{1,2})? ?(?:days?|weeks?|months?)\b|\b(?:or )?sooner (?:prn|if|as needed)|\bafter (?:the |her |his )?(?:mri|mra|ct|cta|vng|vemp|cdp|emg|eeg|imaging|study|studies|results?|testing)\b|\brepeat\b.*\b(?:next|in \d))/i;
  function nextVisitItem(f) {
    if (f.source === "Follow-up") return true;                                   // "Next visit was: 3-4 months"
    if (f.source === "Study" || f.source === "Referral") return false;          // something to chase today
    const text = String(f.text || "");
    if (f.source === "Last assessment") return /^\s*(?:return|rtc|follow[- ]?up|f\/u|recheck)\b/i.test(text.split(" — ").pop() || "");
    return NEXT_VISIT_RE.test(text);
  }
  function renderPatient(p) {
    const P = S.phrases;
    p.append(card("Patient & visit", [
      h("div", { class: "grid" },
        textField("Name", { span: 6, placeholder: "Last, First" }), dateField("DOB", { span: 3, dob: true }), dateField("Date", { span: 3, labelText: "Date of service" }),
        h("div", { class: "f s12" }, h("label", {}, "Visit type"), visitPicker()),
        h("div", { class: "f s3" }, h("label", {}, "Sex"), segmented(["hpi-male", "hpi-female"])),
        allergySummary(),
        textField("ChiefComplaint", { span: 6, list: ["Dizziness", "Vertigo", "Multiple complaints", "Headaches", "Migraines", "Imbalance", "Neuropathy", "Cognitive difficulties", "Tinnitus", "Hearing loss"] }),
        textField("OnsetDate", { span: 3, list: P.ONSET_WORDS }), ageField({ span: 3 }),
      ),
    ]));
    const consult = ["NeuroConsultReqBy", "NeuroConsultPMD", "NeuroConsultCC-1", "NeuroConsultCC-2", "NeuroConsultCC-3"];
    p.append(when(card("Consult / referral", h("div", { class: "grid" },
      textField("NeuroConsultReqBy", { span: 6, list: ["Self", "Friend"] }), textField("NeuroConsultPMD", { span: 6 }),
      textArea("NeuroConsultCC-1", { span: 4, labelText: "CC 1" }), textArea("NeuroConsultCC-2", { span: 4, labelText: "CC 2" }), textArea("NeuroConsultCC-3", { span: 4, labelText: "CC 3" }),
    )), ["neuro-consult", "neuro-eval"], () => on("neuro-consult") || on("neuro-eval"), consult));
  }

  // Allergies live on Meds; the Patient step shows them and links there.
  function allergySummary() {
    const b = h("button", { class: "allergy-link", type: "button", onclick: () => goSection("meds") });
    const paint = () => {
      b.innerHTML = "";
      const a = (S.values.Allergies || "").trim();
      if (S.values.nkda) b.append(h("span", { class: "tag green" }, "NKDA"));
      else if (a) b.append(h("span", { class: "tag red" }, a.length > 40 ? a.slice(0, 38) + "\u2026" : a));
      else b.append(h("span", { class: "muted" }, "Not recorded"));
      b.append(h("span", { class: "linkish" }, "Meds"));
    };
    bind("nkda", b, () => 0, paint); bind("Allergies", b, () => 0, paint);
    return h("div", { class: "f s3" }, h("label", {}, "Allergies"), b);
  }

  // ------------------------------------------------------------------ HPI
  function hpiPhrases() {
    const P = S.phrases;
    return [
      { title: "Opening", items: [P.HPI_OPENING, P.HPI_OPENING_FOLLOWUP], mode: "line" },
      { title: "Interim", items: P.HPI_INTERIM }, { title: "Pertinent negatives", items: P.HPI_NEGATIVES },
      { title: "Companion", items: P.HPI_COMPANION }, { title: "Closing", items: P.HPI_CLOSERS },
    ];
  }
  const CC_CHOICES = ["Dizziness", "Vertigo", "Imbalance", "Headaches", "Tinnitus", "Hearing loss", "Multiple complaints"];
  function renderStory(p) {
    const story = textArea("ChiefComplaint-descr", { cls: "big", labelText: "", phrases: hpiPhrases() });
    story.querySelector("textarea").placeholder = "Type or dictate the story any way: fragments and abbreviations are fine. Build note writes it in the clinic's words.";
    p.append(card("History of present illness", story,
      { tools: [h("button", { class: "tool", type: "button", title: "Paste a visit transcript: Build reads it and keeps it out of the note", onclick: pasteTranscript }, icon("paste"), "Paste a transcript"), ...liftTools(story)] }));
  }

  // ------------------------------------------------------------------ Symptoms
  const SYMPTOMS = [
    { id: "dizziness", title: "Dizziness and lack of coordination", on: "dizziness", deny: "dizziness-denies", prefix: ["assoc-", "worse-", "course-", "occur-", "duration-", "better-", "adl-"], texts: [["DizzOnsetDate", 3], ["DizzFreq", 3], ["DizzyDesc", 6]], extra: [["better-thesemeds", 6], ["better-factors", 6]] },
    { id: "hearing", title: "Hearing loss", on: "hearingloss", deny: "hearingloss-denies", prefix: ["hl-", "hlcourse-", "hlbetter-", "hlworse-", "hlassoc-", "hldur-"], texts: [["HLOnsetDate", 3], ["HLdesc", 9]], extra: [["hl-betterif-other-descr", 6], ["hl-worseif-other-descr", 6]] },
    { id: "tinnitus", title: "Tinnitus", on: "tinnitus", deny: "tinnitus-denies", prefix: ["t-"], texts: [["TinnitusOnsetDate", 3], ["tinnitus-descr", 9]], extra: [["t-symptomsbetterif-other-descr", 4], ["t-symptomsworseif-other-descr", 4], ["t-other-descr", 12]] },
    { id: "gait", title: "Gait ataxia", on: "gaitataxia", deny: "gaitataxia-denies", prefix: ["ga-"], texts: [["GAOnsetDate", 3], ["gaitataxia-descr", 9]], extra: [["ga-weak-descr", 3], ["ga-sens-descr", 3], ["ga-bowel-descr", 3], ["ga-blad-descr", 3], ["ga-usecanedur-descr", 3], ["ga-usewalkerdur-descr", 3], ["ga-usewheelchairdur-descr", 3], ["ga-worseother-descr", 3], ["ga-other-descr", 12]] },
    { id: "headache", title: "Headache", on: "headache", deny: "headache-denies", prefix: ["h-"], texts: [["HOnsetDate", 3], ["headache-descr", 5], ["h-scale", 2], ["h-freq-month", 1], ["h-freq-wk", 1]], extra: [["h-better-thesemeds", 4], ["h-better-other-descr", 4], ["h-symptomsworseif-descr", 4]] },
  ];
  // The text box that explains a box, shown once the box is ticked.
  const EXPLAINS = {
    "better-thesemeds": "better-meds", "better-factors": "better-other", "hl-betterif-other-descr": "hlbetter-other", "hl-worseif-other-descr": "hlworse-other",
    "t-symptomsbetterif-other-descr": "t-better-other", "t-symptomsworseif-other-descr": "t-worse-other",
    "ga-weak-descr": "ga-assoc-weakness", "ga-sens-descr": "ga-assoc-sensory", "ga-bowel-descr": "ga-assoc-bowel", "ga-blad-descr": "ga-assoc-bladder",
    "ga-usecanedur-descr": "ga-better-cane", "ga-usewalkerdur-descr": "ga-better-walker", "ga-usewheelchairdur-descr": "ga-better-chair",
    "ga-worseother-descr": "ga-worse-other", "h-better-thesemeds": "h-better-meds", "h-better-other-descr": "h-better-other",
  };
  // `open`: the details show even before the symptom is ticked (Build, when the HPI says the patient has it).
  function renderSymptom(p, id, { open = false } = {}) {
    const P = S.phrases;
    for (const sym of SYMPTOMS.filter(x => x.id === id)) {
      const body = h("div", { class: "sym-body" });
      const texts = h("div", { class: "grid sym-texts" }, sym.texts.map(([n, s]) => textField(n, { span: s, list: n.toLowerCase().includes("onset") ? P.ONSET_WORDS : (n === "DizzFreq" ? P.DIZZ_FREQ : (n === "gaitataxia-descr" ? P.GAIT_DESCR : null)) })));
      body.append(texts);
      const groups = {};
      for (const pre of sym.prefix) for (const n of byPrefix(pre)) {
        const sp = F()[n];
        if (sp.type !== "checkbox" || n === sym.on || n === sym.deny) continue;
        if (sym.id === "headache" && (n.startsWith("hl") || n.startsWith("hist") || n.startsWith("hpi"))) continue;
        if (sym.id === "hearing" && !n.startsWith("hl")) continue;
        if (sym.id === "tinnitus" && !n.startsWith("t-")) continue;
        (groups[sp.group || "Other"] ||= []).push(n);
      }
      const order = ["Associated with", "Attacks associated with", "Side", "Worse with", "Worse if", "Symptoms worse if", "Symptoms better if", "Symptom course", "Symptom course / character", "Occurs when", "Duration", "Affects ADLs", "Falls", "Other"];
      const gwrap = h("div", { class: "sym-groups" });
      for (const g of Object.keys(groups).sort((a, b) => order.indexOf(a) - order.indexOf(b))) {
        const names = groups[g];
        const exclusiveIds = new Set(names.map(n => F()[n].exclusive).filter(x => x != null));
        const parts = [];
        for (const ex of exclusiveIds) { const set = names.filter(n => F()[n].exclusive === ex); if (set.length > 1) parts.push(segmented(set)); }
        const rest = names.filter(n => F()[n].exclusive == null || names.filter(m => F()[m].exclusive === F()[n].exclusive).length < 2);
        if (rest.length) parts.push(h("div", { class: "chips" }, rest.map(n => check(n))));
        gwrap.append(h("div", {}, h("div", { class: "group-title" }, g), h("div", { class: "row" }, ...parts)));
      }
      body.append(gwrap);
      if (sym.extra.length) body.append(h("div", { class: "grid" }, sym.extra.map(([n, s]) => {
        const el = F()[n].type === "textarea" ? textArea(n, { span: s, phrases: n === "ga-other-descr" ? [{ title: "Falls", items: P.GAIT_FALLS }] : [] }) : textField(n, { span: s, list: /thesemeds$/.test(n) ? medNamesList() : null });
        const parent = EXPLAINS[n];
        return parent ? when(el, [parent], () => on(parent), [n]) : el;
      })));
      const dflt = h("span", { class: "sym-default", title: "Every symptom starts as denied; mark it present if the patient has it" }, "default");
      const head = h("div", { class: "sym-head" }, h("h2", {}, sym.title), dflt, segmented([sym.on, sym.deny], { labels: ["Present", "Patient denies"] }));
      bind(sym.deny, dflt, () => 0, () => { dflt.hidden = !(S.defaulted instanceof Set && S.defaulted.has(sym.deny) && S.values[sym.deny]); });
      const c = h("div", { class: "card sym-card" }, head, body);
      // The details open when the symptom is present (or already has some filled in); denied or not asked, the card
      // is one line.
      const details = [...Object.values(groups).flat(), ...sym.texts.map(([n]) => n), ...sym.extra.map(([n]) => n)];
      const sync = () => {
        const used = details.some(n => { const v = S.values[n]; return v === true || (typeof v === "string" && v.trim() !== ""); });
        const denied = S.values[sym.deny] && !(S.defaulted instanceof Set && S.defaulted.has(sym.deny));
        body.hidden = !S.values[sym.on] && !used && !(open && !denied);
        c.classList.toggle("collapsed", body.hidden);
      };
      bind(sym.on, c, () => 0, sync); bind(sym.deny, c, () => 0, sync);
      p.append(c);
      return c;
    }
  }

  // ------------------------------------------------------------------ History
  function renderHistory(p) {
    const P = S.phrases;
    const hist = byPrefix("hist-").filter(n => F()[n].type === "checkbox");
    p.append(card("Past medical / surgical history", [
      h("div", { class: "chips" }, hist.filter(n => !n.endsWith("-denies") && !["hist-trauma", "hist-ear", "hist-ototox"].includes(n)).map(n => check(n))),
      h("div", { class: "grid", style: "margin-top:10px" },
        h("div", { class: "f s4" }, h("label", {}, "Head trauma w/ LOC"), segmented(["hist-trauma", "hist-trauma-denies"], { labels: ["Yes", "Denies"] })),
        h("div", { class: "f s4" }, h("label", {}, "Severe ear infections"), segmented(["hist-ear", "hist-ear-denies"], { labels: ["Yes", "Denies"] })),
        h("div", { class: "f s4" }, h("label", {}, "Ototoxic drug exposure"), segmented(["hist-ototox", "hist-ototox-denies"], { labels: ["Yes", "Denies"] })),
        when(dateField("hist-lastmens-date", { span: 3 }), ["hist-lastmens"], () => on("hist-lastmens"), ["hist-lastmens-date"])),
    ], { span: "half" }));
    p.append(card("Family history", [
      h("div", { class: "chips" }, check("family-pq"), check("family-nochange")),
      h("div", { class: "grid", style: "margin-top:10px" },
        h("div", { class: "f s6" }, h("label", {}, "Mother"), segmented(["family-mother-alive", "family-mother-dec"], { labels: ["Alive", "Deceased"] })),
        when(textField("family-mother-age", { span: 2, labelText: "Age" }), ["family-mother-alive", "family-mother-dec"], () => on("family-mother-alive") || on("family-mother-dec"), ["family-mother-age"]),
        when(textField("family-mother-hist", { span: 4, labelText: "History" }), ["family-mother-alive", "family-mother-dec"], () => on("family-mother-alive") || on("family-mother-dec"), ["family-mother-hist"]),
        h("div", { class: "f s6" }, h("label", {}, "Father"), segmented(["family-father-alive", "family-father-dec"], { labels: ["Alive", "Deceased"] })),
        when(textField("family-father-age", { span: 2, labelText: "Age" }), ["family-father-alive", "family-father-dec"], () => on("family-father-alive") || on("family-father-dec"), ["family-father-age"]),
        when(textField("family-father-hist", { span: 4, labelText: "History" }), ["family-father-alive", "family-father-dec"], () => on("family-father-alive") || on("family-father-dec"), ["family-father-hist"])),
    ], { span: "half" }));
    p.append(card("Social history", [
      h("div", { class: "chips" }, check("social-pq"), check("social-nochange"), check("social-denies")),
      // one row per habit: each detail box sits on its own habit's row
      h("div", { class: "social-rows" },
        h("div", { class: "grid" },
          h("div", { class: "f s6" }, h("label", {}, "Tobacco"), h("div", { class: "row" }, check("social-tobacco"), check("social-tobacco-remote", { text: "Remote" }))),
          when(textField("social-tobacco-freq", { span: 3 }), ["social-tobacco", "social-tobacco-remote"], () => on("social-tobacco") || on("social-tobacco-remote"), ["social-tobacco-freq"]),
          when(textField("d-tobacco", { span: 3 }), ["social-tobacco", "social-tobacco-remote"], () => on("social-tobacco") || on("social-tobacco-remote"), ["d-tobacco"])),
        h("div", { class: "grid" },
          h("div", { class: "f s6" }, h("label", {}, "Alcohol"), h("div", { class: "row" }, check("social-etoh"), check("social-etoh-history", { text: "Abuse history" }), check("social-etoh-remote", { text: "Remote" }))),
          when(textField("d-etoh", { span: 3 }), ["social-etoh", "social-etoh-history", "social-etoh-remote"], () => on("social-etoh") || on("social-etoh-history") || on("social-etoh-remote"), ["d-etoh"])),
        h("div", { class: "grid" },
          h("div", { class: "f s6" }, h("label", {}, "Drugs"), h("div", { class: "row" }, check("social-drugs"), check("social-drugs-remote", { text: "Remote" }))),
          when(textField("d-drugs", { span: 3 }), ["social-drugs", "social-drugs-remote"], () => on("social-drugs") || on("social-drugs-remote"), ["d-drugs"]))),
      h("div", { class: "subhead" }, "Review of systems"), h("div", { class: "chips" }, check("review-pq-today"), check("review-full10")),
    ], { span: "half" }));
    const labs = textArea("labs", { cls: "big", labelText: "", phrases: [{ title: "Lines", items: P.LABS_LINES }], hint: "If this outgrows the PDF box, the filed note adds a continuation page." });
    p.append(card("Labs", labs, { span: "half", tools: liftTools(labs) }));
    const cont = textArea("family-other-descr", { cls: "big", labelText: "", phrases: [{ title: "Dividers", items: ["----------IMAGING & STUDIES CONTINUED----------", "PMHx noncontributory per pt report."] }, { title: "Reviewed", items: P.REVIEWED_BY }], hint: "If this outgrows the PDF box, the filed note adds a continuation page." });
    p.append(card("History / imaging continued (prints under Family History)", cont, { span: "half", tools: liftTools(cont) }));
  }

  // ------------------------------------------------------------------ Exam
  // Vitals as the measurements they are: BP as systolic / diastolic, units beside each number, BMI worked out from weight
  // and height, height from two lists, pain on a 0-10 scale. A value outside the usual range is coloured.
  const num = (v) => { const n = parseFloat(String(v || "").replace(",", ".")); return isFinite(n) ? n : null; };
  function numBox(name, { unit = "", width = 64, min = null, max = null, warn = null, step = "1" } = {}) {
    const inp = h("input", { id: "f-" + name, type: "text", inputmode: step === "1" ? "numeric" : "decimal", autocomplete: "off", class: "num", style: `width:${width}px`, "aria-label": (F()[name]?.label || name) + (unit ? " (" + unit + ")" : "") });
    const paint = () => { const n = num(S.values[name]); inp.classList.toggle("out", n != null && !!warn && warn(n)); };
    bind(name, inp, () => inp.value, v => { if (document.activeElement !== inp) inp.value = v ?? ""; paint(); });
    inp.addEventListener("input", () => { inp.value = inp.value.replace(step === "1" ? /[^\d]/g : /[^\d.]/g, ""); setValue(name, inp.value); paint(); });
    inp.addEventListener("focus", () => chooseBox(name, inp));
    return h("span", { class: "num-wrap" }, inp, unit ? h("span", { class: "unit" }, unit) : null);
  }
  function bpBox() {
    const [sys, dia] = [h("input", { type: "text", inputmode: "numeric", class: "num", style: "width:52px", "aria-label": "Systolic blood pressure" }),
                        h("input", { type: "text", inputmode: "numeric", class: "num", style: "width:52px", "aria-label": "Diastolic blood pressure" })];
    const paint = () => {
      const [a, b] = String(S.values["const-bp"] || "").split("/");
      if (document.activeElement !== sys) sys.value = (a || "").trim();
      if (document.activeElement !== dia) dia.value = (b || "").trim();
      const s1 = num(sys.value), d1 = num(dia.value);
      sys.classList.toggle("out", s1 != null && (s1 >= 140 || s1 < 90));
      dia.classList.toggle("out", d1 != null && (d1 >= 90 || d1 < 60));
    };
    const write = () => { sys.value = sys.value.replace(/\D/g, "").slice(0, 3); dia.value = dia.value.replace(/\D/g, "").slice(0, 3);
                          setValue("const-bp", sys.value || dia.value ? `${sys.value}/${dia.value}` : ""); paint(); };
    sys.addEventListener("input", () => { write(); if (sys.value.length === 3 || (sys.value.length === 2 && +sys.value >= 30 && sys.value[0] !== "1")) dia.focus(); });
    sys.addEventListener("keydown", e => { if (e.key === "/") { e.preventDefault(); dia.focus(); } });
    dia.addEventListener("input", write);
    dia.addEventListener("keydown", e => { if (e.key === "Backspace" && !dia.value) { e.preventDefault(); sys.focus(); } });
    const wrap = h("span", { class: "num-wrap bp" }, sys, h("span", { class: "slash" }, "/"), dia, h("span", { class: "unit" }, "mmHg"));
    bind("const-bp", wrap, () => 0, paint);
    return wrap;
  }
  function heightBox() {
    const ft = h("select", { "aria-label": "Height, feet" }, h("option", { value: "" }, "—"), [3, 4, 5, 6, 7].map(n => h("option", { value: String(n) }, String(n))));
    const inch = h("select", { "aria-label": "Height, inches" }, h("option", { value: "" }, "—"), Array.from({ length: 12 }, (_, i) => h("option", { value: String(i) }, String(i))));
    bind("HEIGHT-ft", ft, () => ft.value, v => { ft.value = v ?? ""; });
    bind("HEIGHT-in", inch, () => inch.value, v => { inch.value = v ?? ""; });
    ft.addEventListener("change", () => { setValue("HEIGHT-ft", ft.value); if (ft.value && !S.values["HEIGHT-in"]) setValue("HEIGHT-in", "0"); bmi(); });
    inch.addEventListener("change", () => { setValue("HEIGHT-in", inch.value); bmi(); });
    return h("span", { class: "num-wrap" }, ft, h("span", { class: "unit" }, "ft"), inch, h("span", { class: "unit" }, "in"));
  }
  // BMI follows weight and height (it isn't typed)
  function bmi() {
    const w = num(S.values.WEIGHT), ft = num(S.values["HEIGHT-ft"]), inch = num(S.values["HEIGHT-in"]) || 0;
    const val = w && ft ? (703 * w / Math.pow(ft * 12 + inch, 2)).toFixed(1) : "";
    if (val !== String(S.values.BMI || "")) setValue("BMI", val, { silent: true });
  }
  function bmiBox() {
    const out = h("output", { class: "bmi-out", "aria-label": "BMI" });
    bind("BMI", out, () => 0, v => { out.textContent = v || "—"; const n = num(v); out.classList.toggle("out", n != null && (n >= 30 || n < 18.5)); });
    return h("span", { class: "num-wrap" }, out);
  }
  function painBox() {
    const seg = h("div", { class: "seg pain", role: "radiogroup", "aria-label": "Pain, 0 to 10" });
    for (let i = 0; i <= 10; i++) {
      const b = h("button", { type: "button", role: "radio", "data-v": String(i) }, String(i));
      b.addEventListener("click", () => setValue("const-pain", String(S.values["const-pain"]) === String(i) ? "" : String(i)));
      seg.append(b);
    }
    bind("const-pain", seg, () => 0, v => [...seg.children].forEach(b => { const on = b.dataset.v === String(v ?? ""); b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); }));
    return seg;
  }
  // ---------- the exam as a clinical grid
  // Paired findings get a side: Normal / Right / Left / Both is one click, then a one-click finding that carries the
  // side ("Right facial droop"). It all still writes the form's own Normal box, Abnormal X and findings line.
  const SIDED = {
    findings23: ["visual field deficit", "decreased acuity"], findings24: ["gaze-evoked nystagmus", "ophthalmoparesis", "ptosis", "diplopia"],
    findings25: ["decreased facial sensation", "decreased corneal reflex"], findings26: ["facial droop", "LMN facial weakness", "UMN facial weakness"],
    findings27: ["decreased hearing to finger rub and 128 Hz tuning fork", "Weber lateralizes"], findings28: ["uvula deviates", "decreased gag", "palatal weakness"],
    findings29: ["weak shoulder shrug", "weak head turn"], findings30: ["tongue deviates", "tongue fasciculations"],
    findings31: ["4/5 upper extremity", "4/5 lower extremity", "3/5 weakness", "pronator drift"], findings32: ["increased tone", "decreased tone", "rigidity", "spasticity"],
    findings36: ["upgoing toe (Babinski)"], findings37: ["dysmetria on FTN", "dysmetria on HTS", "intention tremor"], findings38: ["dysdiadochokinesia", "slowed rapid movements"],
  };
  const SIDE_WORDS = [["Right", "Right"], ["Left", "Left"], ["Bilateral", "Both"]];
  const SIDE_RX = /^(Right|Left|Bilateral)\b\s*/i;
  function sidedRow(row) {
    const tr = h("div", { class: "exam-row sided" });
    const seg = h("div", { class: "seg exam-side", role: "group", "aria-label": row.label });
    const nb = h("button", { type: "button", class: "neg" }, "Normal");
    const sideBtns = SIDE_WORDS.map(([word, label]) => h("button", { type: "button", class: "pos", "data-side": word }, label));
    seg.append(nb, ...sideBtns);
    const text = () => String(S.values[row.findings] || "");
    const sideOf = () => (SIDE_RX.exec(text()) || [])[1] || "";
    const rest = () => text().replace(SIDE_RX, "");
    nb.addEventListener("click", () => { if (row.normal) setValue(row.normal, true); setValue(row.abnormal, ""); if (SIDE_RX.test(text())) setValue(row.findings, ""); });
    sideBtns.forEach(b => b.addEventListener("click", () => {
      setValue(row.abnormal, "X"); if (row.normal) setValue(row.normal, false);
      setValue(row.findings, (b.dataset.side + " " + rest()).trim() + (rest() ? "" : " "));
    }));
    const chips = h("div", { class: "chips exam-choices" }, (SIDED[row.findings] || []).map(t => {
      const c = h("button", { type: "button", class: "chip small pos", "data-t": t }, t);
      c.addEventListener("click", () => {
        const side = sideOf() || "Right";
        const parts = rest().split(/,\s*/).map(x => x.trim()).filter(Boolean);
        const has = parts.some(x => x.toLowerCase() === t.toLowerCase());
        const next = has ? parts.filter(x => x.toLowerCase() !== t.toLowerCase()) : [...parts, t];
        setValue(row.abnormal, "X"); if (row.normal) setValue(row.normal, false);
        setValue(row.findings, next.length ? `${side} ${next.join(", ")}` : side + " ");
      });
      return c;
    }));
    const inp = h("input", { type: "text", placeholder: "Finding, in your words", autocomplete: "off", "aria-label": row.label + " finding" });
    bind(row.findings, inp, () => inp.value, v => { if (document.activeElement !== inp) inp.value = (v ?? "").trimEnd(); });
    inp.addEventListener("input", () => setValue(row.findings, inp.value));
    inp.addEventListener("focus", () => chooseBox(row.findings, inp));
    const normalText = (S.phrases.EXAM_NORMAL_TEXT || {})[row.findings] || "";
    const normalNote = h("span", { class: "exam-normal" }, normalText);
    const detail = h("div", { class: "exam-detail" }, normalNote, chips, inp);
    const sync = () => {
      const abn = !!String(S.values[row.abnormal] || "").trim(), nor = row.normal ? !!S.values[row.normal] : false, side = sideOf();
      nb.classList.toggle("on", nor && !abn); nb.setAttribute("aria-pressed", String(nor && !abn));
      sideBtns.forEach(b => { const on = abn && b.dataset.side.toLowerCase() === side.toLowerCase(); b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
      if (abn && !side) sideBtns.forEach(b => b.classList.add("maybe"));
      else sideBtns.forEach(b => b.classList.remove("maybe"));
      tr.classList.toggle("abn", abn);
      detail.classList.toggle("open", abn || !!text().trim());
      normalNote.hidden = abn || !!text().trim() || !nor || !normalText;
      chips.hidden = !abn;
      const parts = rest().toLowerCase().split(/,\s*/);
      for (const c of chips.children) c.classList.toggle("on", parts.includes(c.dataset.t.toLowerCase()));
    };
    tr.append(h("div", { class: "lbl" }, row.label), seg, detail);
    bind(row.abnormal, tr, () => 0, sync); if (row.normal) bind(row.normal, tr, () => 0, sync); bind(row.findings, tr, () => 0, sync);
    return tr;
  }
  // Reflexes with - and + : each tap moves one grade (0, 1-, 1, 1+, 2-, 2, 2+, 3, 3+, 4); the first tap starts at 2.
  // When all ten are in and symmetric at 2-/2/2+, "DTRs symmetric" is ticked normal; asymmetric or out of range, abnormal.
  function dtrGrid(row) {
    const scale = S.phrases.DTR_VALUES;
    const cells = [];
    const grid = h("div", { class: "dtr-grid" }, h("div"), ["Biceps", "Triceps", "Brachio\u00adrad.", "Patellar", "Achilles"].map(x => h("div", { class: "h" }, x)));
    const judge = () => {
      const vals = cells.map(n => String(S.values[n] || ""));
      if (vals.some(v => !v)) return;
      const idx = (v) => scale.indexOf(v), lo = idx("2-"), hi = idx("2+");
      const sym = ["bi", "tri", "br", "pat", "ach"].every(k => S.values[`dtr-r-${k}`] === S.values[`dtr-l-${k}`]);
      const inRange = vals.every(v => idx(v) >= lo && idx(v) <= hi);
      if (sym && inRange) { setValue(row.normal, true, { silent: true }); setValue(row.abnormal, "", { silent: true }); }
      else { setValue(row.abnormal, "X", { silent: true }); setValue(row.normal, false, { silent: true }); }
    };
    for (const side of ["r", "l"]) {
      grid.append(h("div", { class: "h side" }, side === "r" ? "Right" : "Left"));
      for (const k of ["bi", "tri", "br", "pat", "ach"]) {
        const n = `dtr-${side}-${k}`; cells.push(n);
        const out = h("output", { class: "dtr-val", "aria-live": "polite" });
        const step = (d) => {
          const cur = String(S.values[n] || ""), i = scale.indexOf(cur);
          const next = i < 0 ? "2" : scale[Math.max(0, Math.min(scale.length - 1, i + d))];
          setValue(n, next); judge();
        };
        const minus = h("button", { type: "button", class: "dtr-step", "aria-label": `Lower ${side === "r" ? "right" : "left"} ${k}` }, "−");
        const plus = h("button", { type: "button", class: "dtr-step", "aria-label": `Raise ${side === "r" ? "right" : "left"} ${k}` }, "+");
        minus.addEventListener("click", () => step(-1)); plus.addEventListener("click", () => step(1));
        out.addEventListener("dblclick", () => setValue(n, ""));
        const cell = h("div", { class: "dtr-cell", title: "Double-click the grade to clear it" }, minus, out, plus);
        bind(n, cell, () => 0, v => {
          const i = scale.indexOf(String(v || ""));
          out.textContent = v || "—";
          cell.classList.toggle("lo", i >= 0 && i < scale.indexOf("2-"));
          cell.classList.toggle("hi", i > scale.indexOf("2+"));
          cell.classList.toggle("ok", i >= scale.indexOf("2-") && i <= scale.indexOf("2+"));
        });
        grid.append(cell);
      }
    }
    return grid;
  }
  // Sensory: tap where it is decreased on the figure and which senses; the finding is written with its distribution
  // ("Decreased vibration sensation in a stocking-glove distribution").
  const BODY = [ // region, label, svg shape (figure facing you: the patient's right is on your left)
    ["face-r", "R face", "M44 14 a14 16 0 0 0 0 32 z"], ["face-l", "L face", "M44 14 a14 16 0 0 1 0 32 z"],
    ["arm-r", "R arm", "M22 54 h10 v52 h-10 z"], ["arm-l", "L arm", "M56 54 h10 v52 h-10 z"],
    ["hand-r", "R hand", "M27 116 m-7 0 a7 8 0 1 0 14 0 a7 8 0 1 0 -14 0"], ["hand-l", "L hand", "M61 116 m-7 0 a7 8 0 1 0 14 0 a7 8 0 1 0 -14 0"],
    ["trunk", "trunk", "M34 50 h20 v58 h-20 z"],
    ["leg-r", "R leg", "M34 112 h9 v66 h-9 z"], ["leg-l", "L leg", "M45 112 h9 v66 h-9 z"],
    ["foot-r", "R foot", "M30 182 h13 v8 h-13 z"], ["foot-l", "L foot", "M45 182 h13 v8 h-13 z"],
  ];
  const SENSES = ["light touch", "pinprick", "vibration", "temperature", "proprioception", "monofilament"];
  function distribution(regions) {
    const has = (r) => regions.has(r), both = (a) => has(a + "-r") && has(a + "-l");
    if (both("foot") && both("hand") && ![...regions].some(r => /^(face|trunk|arm)/.test(r))) return "in a stocking-glove distribution";
    if (both("foot") && regions.size === 2) return "in a stocking distribution (bilateral feet)";
    if (both("leg") && both("foot") && regions.size === 4) return "in BLE";
    if (both("arm") && both("hand") && regions.size === 4) return "in BUE";
    const names = BODY.filter(([r]) => regions.has(r)).map(([, label]) => label);
    return names.length ? "in the " + names.join(", ") : "";
  }
  function sensoryRow(row) {
    const tr = h("div", { class: "exam-row sensory" });
    const seg = h("div", { class: "seg" });
    const nb = h("button", { type: "button", class: "neg" }, "Normal"), ab = h("button", { type: "button", class: "pos" }, "Decreased");
    seg.append(nb, ab);
    const map = S.sensoryMap || (S.sensoryMap = {});
    const st = map[row.findings] || (map[row.findings] = { regions: [], senses: [] });
    const regions = new Set(st.regions), senses = new Set(st.senses);
    const write = () => {
      st.regions = [...regions]; st.senses = [...senses];
      if (!regions.size && !senses.size) return;
      const sense = senses.size ? [...senses].join(" and ") + " " : "";
      setValue(row.abnormal, "X"); if (row.normal) setValue(row.normal, false);
      setValue(row.findings, `Decreased ${sense}sensation ${distribution(regions)}`.replace(/\s+/g, " ").trim());
    };
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 88 196"); svg.setAttribute("class", "body-figure"); svg.setAttribute("role", "group"); svg.setAttribute("aria-label", "Where sensation is decreased");
    for (const [id, label, d] of BODY) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d); path.setAttribute("class", "region" + (regions.has(id) ? " on" : "")); path.setAttribute("tabindex", "0");
      path.setAttribute("role", "checkbox"); path.setAttribute("aria-label", label); path.setAttribute("aria-checked", String(regions.has(id)));
      const t = document.createElementNS("http://www.w3.org/2000/svg", "title"); t.textContent = label; path.append(t);
      const flip = () => { regions.has(id) ? regions.delete(id) : regions.add(id); path.classList.toggle("on", regions.has(id)); path.setAttribute("aria-checked", String(regions.has(id))); write(); };
      path.addEventListener("click", flip);
      path.addEventListener("keydown", e => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); flip(); } });
      svg.append(path);
    }
    const head = document.createElementNS("http://www.w3.org/2000/svg", "text");
    head.setAttribute("x", "6"); head.setAttribute("y", "12"); head.setAttribute("class", "fig-side"); head.textContent = "R";
    const headL = head.cloneNode(); headL.setAttribute("x", "76"); headL.textContent = "L";
    svg.append(head, headL);
    const senseChips = h("div", { class: "chips exam-choices" }, SENSES.map(t => {
      const c = h("button", { type: "button", class: "chip small pos" + (senses.has(t) ? " on" : "") }, t);
      c.addEventListener("click", () => { senses.has(t) ? senses.delete(t) : senses.add(t); c.classList.toggle("on", senses.has(t)); write(); });
      return c;
    }));
    const inp = h("input", { type: "text", placeholder: "Finding, in your words", autocomplete: "off", "aria-label": row.label + " finding" });
    bind(row.findings, inp, () => inp.value, v => { if (document.activeElement !== inp) inp.value = v ?? ""; });
    inp.addEventListener("input", () => setValue(row.findings, inp.value));
    inp.addEventListener("focus", () => chooseBox(row.findings, inp));
    const normalText = (S.phrases.EXAM_NORMAL_TEXT || {})[row.findings] || "";
    const normalNote = h("span", { class: "exam-normal" }, normalText);
    const picker = h("div", { class: "sensory-pick" }, svg, h("div", { class: "sensory-side" }, h("div", { class: "group-title" }, "Which senses"), senseChips, inp));
    const detail = h("div", { class: "exam-detail" }, normalNote, picker);
    nb.addEventListener("click", () => { if (row.normal) setValue(row.normal, true); setValue(row.abnormal, ""); });
    ab.addEventListener("click", () => { setValue(row.abnormal, "X"); if (row.normal) setValue(row.normal, false); });
    const sync = () => {
      const abn = !!String(S.values[row.abnormal] || "").trim(), nor = row.normal ? !!S.values[row.normal] : false;
      nb.classList.toggle("on", nor && !abn); ab.classList.toggle("on", abn); tr.classList.toggle("abn", abn);
      nb.setAttribute("aria-pressed", String(nor && !abn)); ab.setAttribute("aria-pressed", String(abn));
      detail.classList.toggle("open", abn || !!String(S.values[row.findings] || "").trim());
      picker.hidden = !abn; normalNote.hidden = abn || !nor || !normalText || !!String(S.values[row.findings] || "").trim();
    };
    tr.append(h("div", { class: "lbl" }, row.label), seg, detail);
    bind(row.abnormal, tr, () => 0, sync); if (row.normal) bind(row.normal, tr, () => 0, sync); bind(row.findings, tr, () => 0, sync);
    return tr;
  }
  const vital = (labelText, control, span = 2) => h("div", { class: `f s${span} vital` }, h("label", {}, labelText), control);
  function renderExam(p) {
    // CN VIII's sub-rows: ear canals, Weber, Rinne (A > B and B > A side by side)
    const earRows = () => [
      h("div", { class: "exam-row sub" }, h("div", { class: "lbl" }, "Ear canals"), h("div", { class: "exam-wide" }, h("div", { class: "chips" }, check("debris-kerat"), check("debris-cerumen")))),
      h("div", { class: "exam-row sub" }, h("div", { class: "lbl" }, "Weber"), h("div", { class: "exam-wide" }, selectField("weber", { span: 12, labelText: "" }))),
      h("div", { class: "exam-row sub" }, h("div", { class: "lbl" }, "Rinne"), h("div", { class: "exam-wide exam-pair" }, selectField("rinne-ab", { span: 6, labelText: "A > B" }), selectField("rinne-ba", { span: 6, labelText: "B > A" }))),
    ];
    const P = S.phrases;
    const vitals = h("div", { class: "grid vitals-grid" },
      vital("Blood pressure", bpBox(), 3),
      vital("Heart rate", numBox("const-hr", { unit: "bpm", warn: n => n > 100 || n < 50 }), 2),
      vital("Resp. rate", numBox("const-rr", { unit: "/min", width: 52, warn: n => n > 20 || n < 10 }), 2),
      vital("Temperature", numBox("TEMP", { unit: "°F", step: "0.1", width: 60, warn: n => n >= 100.4 || n < 95 }), 2),
      vital("Weight", numBox("WEIGHT", { unit: "lb", width: 60 }), 3),
      vital("Height", heightBox(), 3), vital("BMI", bmiBox(), 2),
      vital("Pain", painBox(), 7));
    bind("WEIGHT", vitals, () => 0, () => bmi());
    p.append(card("Vitals", vitals));
    const table = h("div");
    let lastGroup = null;
    for (const row of S.schema.exam_rows) {
      if (row.group !== lastGroup) {
        const groupRows = S.schema.exam_rows.filter(r => r.group === row.group && r.normal);
        const allNormal = h("button", { type: "button", class: "linkish", onclick: () => {
          for (const r of groupRows) { if (String(S.values[r.abnormal] || "").trim()) continue; setValue(r.normal, true, { silent: true }); }
          onChange("normal1");
        } }, "All normal");
        table.append(h("div", { class: "exam-group" }, h("span", {}, row.group), groupRows.length > 1 ? allNormal : null));
        lastGroup = row.group;
      }
      if (row.findings && SIDED[row.findings]) { table.append(sidedRow(row)); if (row.normal === "normal27") table.append(...earRows()); continue; }
      if (row.normal === "normal33" || row.normal === "normal34") { table.append(sensoryRow(row)); continue; }
      const tr = h("div", { class: "exam-row" });
      const seg = h("div", { class: "seg" });
      const nb = h("button", { type: "button", class: "neg" }, "Normal"), ab = h("button", { type: "button", class: "pos" }, "Abnormal");
      // Normal shows what will print; Abnormal opens the common findings to click, plus your own words.
      const normalText = row.findings ? (P.EXAM_NORMAL_TEXT || {})[row.findings] || "" : "";
      const choices = row.findings ? ((P.EXAM_ABNORMAL || {})[row.findings] || (P.EXAM_NORMALS[row.findings] || []).filter(t => t !== normalText && !/^\(-\)|^No |intact|^Normal/i.test(t))) : [];
      let detail = h("div", { class: "exam-detail" });
      const syncRow = () => {
        const abn = !!(S.values[row.abnormal] || "").trim(); const nor = row.normal ? !!S.values[row.normal] : false;
        nb.classList.toggle("on", nor && !abn); ab.classList.toggle("on", abn); tr.classList.toggle("abn", abn);
        nb.setAttribute("aria-pressed", String(nor && !abn)); ab.setAttribute("aria-pressed", String(abn));
        if (row.findings) {
          const typed = String(S.values[row.findings] || "").trim();
          detail.classList.toggle("open", abn || !!typed);
          normalNote.hidden = abn || !!typed || !nor || !normalText;
          chipRow.hidden = !abn || !choices.length;
          for (const c of chipRow.children) c.classList.toggle("on", typed.toLowerCase().includes(c.dataset.t.toLowerCase()));
        }
      };
      nb.addEventListener("click", () => { if (row.normal) setValue(row.normal, true); setValue(row.abnormal, ""); });
      ab.addEventListener("click", () => { setValue(row.abnormal, "X"); if (row.normal) setValue(row.normal, false); if (row.findings) { const el = (S.inputs.get(row.findings) || [])[0]; el && el.focus(); } });
      seg.append(nb, ab);
      tr.append(h("div", { class: "lbl" }, row.label), seg);
      let normalNote = h("span"), chipRow = h("div");
      if (row.findings) {
        const inp = h("input", { type: "text", placeholder: "Finding, in your words", autocomplete: "off", "aria-label": row.label + " finding" });
        bind(row.findings, inp, () => inp.value, v => { if (document.activeElement !== inp) inp.value = v ?? ""; });
        inp.addEventListener("input", () => setValue(row.findings, inp.value));
        inp.addEventListener("focus", () => chooseBox(row.findings, inp));
        normalNote = h("span", { class: "exam-normal", title: "What prints for a normal row" }, normalText);
        chipRow = h("div", { class: "chips exam-choices" }, choices.map(t => {
          const c = h("button", { type: "button", class: "chip small pos", "data-t": t }, t);
          c.addEventListener("click", () => {
            const cur = String(S.values[row.findings] || "").trim();
            const has = cur.toLowerCase().includes(t.toLowerCase());
            setValue(row.findings, has ? cur.replace(new RegExp("\\s*" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\.?", "i"), "").replace(/^[.\s]+/, "").trim() : (cur ? cur.replace(/\.?\s*$/, ". ") : "") + t);
          });
          return c;
        }));
        detail = h("div", { class: "exam-detail" }, normalNote, chipRow, inp);
        tr.append(detail);
      } else if (row.normal === "normal36") {
        tr.append(dtrGrid(row));
      } else tr.append(h("div"));
      bind(row.abnormal, tr, () => 0, syncRow); if (row.normal) bind(row.normal, tr, () => 0, syncRow);
      if (row.findings) bind(row.findings, tr, () => 0, syncRow);
      table.append(tr);
      if (row.normal === "normal27") table.append(...earRows());
    }
    const tools = [h("button", { class: "tool", type: "button", onclick: () => { for (const r of S.schema.exam_rows) { if (S.values[r.abnormal]) continue; if (r.normal) setValue(r.normal, true, { silent: true }); } onChange("normal1"); toast("Rows not marked abnormal set to normal"); } }, icon("check"), "Rest normal"),
                   h("button", { class: "tool", type: "button", onclick: () => { for (const side of ["r", "l"]) for (const k of ["bi", "tri", "br", "pat", "ach"]) setValue(`dtr-${side}-${k}`, "2", { silent: true }); onChange("dtr-r-bi"); } }, "DTRs 2 throughout")];
    p.append(card("Otoneurologic exam", table, { tools }));
  }

  // ------------------------------------------------------------------ Testing (goggles + studies)
  function renderOculography(p) {
    const P = S.phrases;
    const posRow = (title, prefix) => h("div", { class: "pos-row" }, h("div", { class: "lbl" }, title), h("div", { class: "chips" }, byPrefix(prefix).map(n => check(n))));
    p.append(card("IR oculography / positional testing", [
      h("div", { class: "grid" }, textField("goggle-dr", { span: 4, list: ["Jake Perkins, PA-C", "Touraj Yari, PA-C", "Carter Copeland, PA-C", "Evan Camarillo, PA-C", "Ian Purcell, MD, PhD", "Monali Patel, MD"] }), dateField("goggle-date", { span: 3 }), h("div", { class: "f s5", style: "justify-content:flex-end" }, h("div", { class: "row" }, check("see-epley")))),
      h("div", { class: "subhead" }, "Dix-Hallpike"), posRow("Right", "dixhall-right-"), posRow("Left", "dixhall-left-"),
      h("div", { class: "subhead" }, "Supine positional"), posRow("Head center", "supine-center-"), posRow("Head right", "supine-right-"), posRow("Head left", "supine-left-"),
      h("div", { class: "subhead" }, "Gaze"), posRow("Gaze right", "gaze-right-"), posRow("Gaze left", "gaze-left-"),
      h("div", { class: "subhead" }, "Study impression"), h("div", { class: "chips" }, byPrefix("imp-", ["imp-mp"]).map(n => check(n))),
      h("div", { class: "grid", style: "margin-top:12px" }, textArea("goggle-treatment-x", { labelText: "Treatment", phrases: [{ title: "Treatment", items: P.GOGGLE_TREATMENT }] })),
    ]));
  }
  // Past studies on file for this patient (EMG/NCV, EEG, VNG, VEMP, chair reports): read the report beside the note,
  // or put it in the next empty study row. Nothing is written into the note until "Add to studies".
  const STUDY_GROUPS = ["Neuro testing", "Vestibular testing"];
  function studyDocs() {
    const chart = DDOS.context ? DDOS.context().chart : null;
    return (chart?.documents || []).filter(d => STUDY_GROUPS.includes(d.group))
      .sort((a, b) => STUDY_GROUPS.indexOf(a.group) - STUDY_GROUPS.indexOf(b.group));
  }
  function reportDate(d) {
    const m = /(\d{1,2})[-_./ ](\d{1,2})[-_./ ](\d{2}(?:\d{2})?)(?!\d)/.exec(d.name || "");
    const found = m ? parseDate(`${m[1]}/${m[2]}/${m[3]}`) : null;
    const when = found || parseDate(d.modified);
    return when ? `${String(when.getMonth() + 1).padStart(2, "0")}/${String(when.getDate()).padStart(2, "0")}/${String(when.getFullYear()).slice(2)}` : "";
  }
  function reportName(d) {
    const n = String(d.name || "");
    if (d.kind === "EMG/NCV") return /\bBUE\b|upper/i.test(n) ? "EMG/NCV BUE" : /\bBLE\b|lower/i.test(n) ? "EMG/NCV BLE" : "EMG/NCV";
    if (d.kind === "EEG") return /zeto/i.test(`${d.folder || ""} ${d.path || ""} ${n}`) ? "Zeto EEG" : "EEG";
    return d.kind === "Chair report" ? "Chair" : (d.kind || "Study");
  }
  // Chart prep: reports filed since the last note (a Zeto EEG, a VNG, an EMG) go into the study rows before the visit,
  // with the report's impression when it has one, so the provider reads rather than types. Rows already there stay.
  function samePatient(p) {
    if (!p) return false;
    if (!S.patient) return true;
    const same = (a, b) => String(a || "").trim().toLowerCase() === String(b || "").trim().toLowerCase();
    return (p.id != null && S.patient.id != null && String(p.id) === String(S.patient.id)) || (same(p.last, S.patient.last) && same(p.first, S.patient.first));
  }
  async function prefillStudies(since) {
    const chart = DDOS.context ? DDOS.context().chart : null;
    if (!chart || !Array.isArray(chart.documents) || !samePatient(chart.patient)) return;      // wait for this patient's chart
    S.prepStudies = "done";
    const docs = studyDocs().filter(d => d.path);
    const after = parseDate(since);
    const fresh = docs.filter(d => { const when = parseDate(d.modified) || parseDate(reportDate(d)); return !after || !when || when > after; });
    let n = 0;
    for (const d of fresh) {
      const name = reportName(d), date = reportDate(d);
      if ([1, 2, 3, 4, 5].some(i => String(S.values[`x-study${i}`] || "").trim() === name && String(S.values[`x-study${i}-date`] || "").trim() === date)) continue;
      let result = "";
      try { const r = await api.get("/api/report?path=" + encodeURIComponent(d.path)); result = (r && r.result) || ""; } catch { result = ""; }
      if (placeStudy({ name, date, result: result || "see report" }) !== "same") n++;
    }
    if (n) toast(`${n} report${n > 1 ? "s" : ""} on file since the last visit added to the studies.`);
    if (fresh.length && S.tab === "note") refreshView();       // the "New since last visit" block above the note shows where each went
  }
  // One click puts a report in the next empty study row with its bottom line (a Zeto EEG's impression, a VNG's
  // interpretation) read from the PDF; a report without one leaves the result box focused to type.
  async function addStudy(d) {
    const name = reportName(d), date = reportDate(d), v = S.values;
    const cols = (i) => [`x-study${i}`, `x-study${i}-date`, `x-study${i}-result`];
    for (let i = 1; i <= 5; i++) if (String(v[`x-study${i}`] || "").trim() === name && String(v[`x-study${i}-date`] || "").trim() === date) { toast(`${name} ${date} is already in the studies.`); return; }
    const i = [1, 2, 3, 4, 5].find(k => cols(k).every(n => !String(v[n] || "").trim()));
    if (!i) { toast("All five study rows are in use. Clear one first."); return; }
    let result = "";
    if (d.path) { try { const r = await api.get("/api/report?path=" + encodeURIComponent(d.path)); result = String((r && r.result) || "").trim(); } catch { result = ""; } }
    setValue(`x-study${i}`, name); setValue(`x-study${i}-date`, date);
    if (result) setValue(`x-study${i}-result`, result);
    requestAnimationFrame(() => { const el = $("#f-x-study" + i + "-result"); if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); if (!result) el.focus(); } });
    toast(result ? `${name} added with the report's impression.` : `${name} added. Type the result.`);
  }
  // The last visit's date: the note this one was started from, else the chart's last seen visit.
  function lastVisitDate() {
    const fromPrep = parseDate(S.prep && S.prep.date);
    if (fromPrep) return fromPrep;
    const chart = DDOS.context ? DDOS.context().chart : null;
    const iso = chart && samePatient(chart.patient) ? String(chart.last_seen || "") : "";
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }
  // A report filed after the last visit: the provider hasn't seen it in a note yet.
  function newSinceLastVisit(d) {
    const since = lastVisitDate();
    if (!since) return false;
    const when = parseDate(d.modified) || parseDate(reportDate(d));
    return !!when && when > since;
  }
  function onFileStrip() {
    const docs = studyDocs();
    const wrap = h("div", { class: "on-file", style: "grid-column:1/-1" });
    if (!docs.length) { wrap.hidden = true; return wrap; }
    const fresh = docs.filter(newSinceLastVisit), stale = docs.filter(d => !newSinceLastVisit(d));
    const row = (d, isNew) => h("div", { class: "on-file-row" + (isNew ? " is-new" : "") },
      h("span", { class: "tag" + (d.group === "Vestibular testing" ? " blue" : "") }, reportName(d)),
      h("span", { class: "on-file-name", title: d.name }, isNew ? h("span", { class: "tag orange on-file-new", title: "Filed since the last visit" }, "New since last visit") : null, d.name),
      h("span", { class: "muted on-file-date" }, d.modified || ""),
      h("span", { class: "on-file-acts" },
        d.path ? h("button", { class: "tool", type: "button", onclick: (e) => viewReport(d, e.currentTarget.closest(".on-file-row")) }, icon("file"), "View") : h("span", { class: "muted" }, "No file"),
        h("button", { class: "tool", type: "button", title: "Put it in the next empty study row with the report's impression", onclick: () => addStudy(d) }, icon("plus"), "Add to studies")));
    wrap.append(h("div", { class: "subhead" }, "Reports on file", fresh.length ? h("span", { class: "muted" }, ` · ${fresh.length} new since the last visit`) : null),
      h("div", { class: "on-file-list" }, fresh.map(d => row(d, true)), stale.map(d => row(d, false))));
    return wrap;
  }
  function refreshOnFile() {
    const old = document.querySelector("#panel .on-file");
    if (old) old.replaceWith(onFileStrip());
  }
  // A read-only drawer beside the note: the report stays open while the result is typed.
  let lastReportRow = null;
  function viewReport(d, from) {
    closeReport();
    const body = h("div", { class: "report-body" });
    const drawer = h("aside", { class: "report-drawer", role: "dialog", "aria-label": d.name || "Report" },
      h("div", { class: "report-head" }, h("span", { class: "tag" }, reportName(d) || "Report"), h("b", { title: d.name }, d.name || "Report"), h("span", { class: "muted report-ro" }, "Read only"), h("span", { class: "spacer" }),
        h("button", { class: "tool", type: "button", onclick: () => addStudy(d) }, icon("plus"), "Add to studies"),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Close the report", title: "Close (Esc)", onclick: closeReport }, icon("x"))),
      body);
    document.body.append(drawer);
    lastReportRow = from || null;
    keepPlace(() => document.body.classList.add("report-open"), lastReportRow);
    const review = DDOS.modules && DDOS.modules.review;
    if (review && review.show) review.show(d, body, api);
    else body.append(h("div", { class: "muted" }, "The viewer isn't loaded."));
    drawer.querySelector(".icon-btn").focus();
  }
  function closeReport() {
    const open = document.querySelectorAll(".report-drawer");
    if (!open.length) return;
    open.forEach(x => x.remove());
    keepPlace(() => document.body.classList.remove("report-open"), lastReportRow);
    lastReportRow = null;
  }
  // The note reflows when the drawer opens or closes; keep the part of the note that was on screen where it was.
  function keepPlace(change, el) {
    const p = $("#panel");
    let anchor = null;
    if (p && p.dataset.mode === "note") {
      const pr = p.getBoundingClientRect();
      const seen = (x) => { if (!x || !x.isConnected || !p.contains(x)) return false; const r = x.getBoundingClientRect(); return r.bottom > pr.top + 8 && r.top < pr.bottom - 8; };
      anchor = seen(el) ? el : [...p.querySelectorAll(".card")].find(c => c.getBoundingClientRect().bottom > pr.top + 8) || null;
    }
    const before = anchor ? anchor.getBoundingClientRect().top : 0;
    change();
    if (anchor) p.scrollTop += anchor.getBoundingClientRect().top - before;
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.querySelector(".report-drawer")) closeReport(); });

  function renderStudies(p) {
    const P = S.phrases;
    const rows = [];
    const filled = (i) => [`x-study${i}`, `x-study${i}-date`, `x-study${i}-result`].some(n => String(S.values[n] || "").trim());
    for (let i = 1; i <= 5; i++) {
      const row = h("div", { class: "grid study-row", style: "grid-column:1/-1" }, textField(`x-study${i}`, { span: 3, labelText: i === 1 ? "Study" : "", list: P.STUDY_NAMES }), dateField(`x-study${i}-date`, { span: 2, labelText: i === 1 ? "Date" : "", loose: true }), textField(`x-study${i}-result`, { span: 7, labelText: i === 1 ? "Result" : "", list: P.STUDY_RESULTS.concat(P.REVIEWED_BY) }));
      const prev = i - 1;
      rows.push(i === 1 ? row : when(row, [`x-study${prev}`, `x-study${prev}-date`, `x-study${prev}-result`], () => filled(prev), [`x-study${i}`, `x-study${i}-date`, `x-study${i}-result`]));
    }
    p.append(card("Imaging / studies", h("div", { class: "grid" }, rows, onFileStrip())));
  }

  // ------------------------------------------------------------------ Impression & plan
  function renderPlan(p) {
    const P = S.phrases;
    imagingOrdersList();
    const dxItems = P.DIAGNOSES.map(([d]) => d);
    const nextNumber = () => { const t = S.values["impression-plan-descr"] || ""; const nums = [...t.matchAll(/^\s*(\d{1,2})\)/gm)].map(m => +m[1]); return (Math.max(0, ...nums) || 0) + 1; };
    const addDx = (e) => phrasePopover(e.currentTarget, "impression-plan-descr", [{ title: "Add impression", items: dxItems.map(d => `${nextNumber()}) ${d} - `), mode: "line" }]);
    const impBox = textArea("impression-plan-descr", { cls: "big", labelText: "", phrases: [{ title: "Plan sentences", items: P.IMPRESSION_SENTENCES, mode: "inline" }, { title: "See plan", items: P.IMPRESSION_SEE_PLAN.concat([P.IMPRESSION_SECONDARY]), mode: "inline" }], hint: "One line each: 1) Diagnosis - plan." });
    p.append(card("Impression / plan", impBox,
      { span: "twothirds", tools: [h("button", { class: "tool", type: "button", onclick: addDx }, icon("plus"), "Impression"), ...liftTools(impBox)] }));
    // Separate time and the attestation print as lines with a [ ] in front: tick the box, pick the line.
    const lineRow = (boxName, field, aria) => {
      const row = h("div", { class: "row line-row" }, box(boxName, aria),
        when(selectField(field, { span: 12, labelText: "" }), [boxName], () => on(boxName), [field]), when(h("span", { class: "muted line-hint" }, aria), [boxName], () => !on(boxName) && !String(S.values[field] || "").trim()));
      const full = h("div", { class: "line-full" });
      const paint = () => {
        const v = String(S.values[field] || "").trim(); full.textContent = v;
        const sel = row.querySelector("select"); let cut = true;
        if (v && sel && sel.clientWidth) {
          const cs = getComputedStyle(sel), c = paint.ctx || (paint.ctx = document.createElement("canvas").getContext("2d"));
          c.font = cs.font || `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
          cut = c.measureText(v).width > sel.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 24;   // 24: the arrow
        }
        full.hidden = !v || !on(boxName) || !cut;
      };
      bind(field, full, () => 0, paint); bind(boxName, full, () => 0, paint);
      if (window.ResizeObserver) new ResizeObserver(() => paint()).observe(row);
      return h("div", { class: "line-wrap" }, row, full);
    };
    p.append(card("Encounter time", [
      h("div", { class: "grid" }, textField("appt-time", { span: 12, labelText: "Total time (minutes)" }),
        h("div", { class: "f s12" }, h("div", { class: "row" }, P.ENCOUNTER_TIMES.map(t => h("button", { class: "chip", type: "button", onclick: () => setValue("appt-time", t) }, t + " min")), S.status.visit_timer ? h("button", { class: "chip", type: "button", onclick: useClock }, icon("clock"), "From timer") : null))),
      h("div", { class: "subhead" }, "Separate time"),
      lineRow("time-proc1", "addnl-time", "Print separate-time line 1"),
      h("div", { style: "height:8px" }),
      lineRow("time-proc2", "addnl-time-2", "Print separate-time line 2"),
      h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "tool", type: "button", onclick: () => { const slot = S.values["time-proc1"] && S.values["addnl-time"] ? ["time-proc2", "addnl-time-2"] : ["time-proc1", "addnl-time"]; setValue(slot[1], P.LONGITUDINAL); setValue(slot[0], true); } }, icon("plus"), "G2211 longitudinal line")),
      h("div", { class: "subhead" }, "Attending"), lineRow("imp-mp", "Imp/MP", "Print the attending statement"),
    ], { span: "third" }));
    p.append(card("Plan", [
      // the birth-control line is hidden for a male patient (unless it was already ticked)
      h("div", { class: "chips" }, ["plan-balancelog", "plan-halog", "plan-fall", "plan-continue", "plan-meds", "plan-prophy", "plan-horizontal", "plan-sleep", "plan-return", "plan-driving"].map(n =>
        n === "plan-prophy" ? when(h("span", { class: "chip-slot" }, check(n)), ["hpi-male", "hpi-female"], () => !on("hpi-male"), [n]) : check(n))),
      h("div", { class: "grid", style: "margin-top:12px" }, h("div", { class: "f s4", style: "justify-content:flex-end" }, h("div", { class: "row" }, check("plan-adverse", { text: "Discussed effects of" }))), when(textField("plan-adverse-descr", { span: 8, labelText: "Medications discussed", list: medNamesList() }), ["plan-adverse"], () => on("plan-adverse"), ["plan-adverse-descr"]),
        textArea("plan-descr", { span: 12, cls: "big", phrases: [{ title: "Staff", items: P.PLAN_STAFF }], hint: "The PDF's bottom plan box is short; longer notes automatically continue on an added page." })),
    ], { span: "full" }));
    p.append(card("Next appointment, referrals, orders", [
      h("div", { class: "grid" }, textField("next-appt", { span: 3, labelText: "Next visit in", list: P.NEXT_APPT }), h("div", { class: "f s4" }, h("label", {}, "Unit"), segmented(["next-appt-days", "next-appt-weeks", "next-appt-months"])), textField("nextappt-descr", { span: 5, labelText: "Note", list: [P.NEXT_APPT_NOTE] })),
      h("div", { class: "subhead" }, "Referrals"),
      h("div", { class: "grid three" }, textField("referral1-doc", { span: 4 }), textField("referral1-spec", { span: 4, list: P.REFERRAL_SPECIALTIES }), textField("referral1-diag", { span: 4 }),
        when(h("div", { class: "grid three", style: "grid-column:1/-1" }, textField("referral2-doc", { span: 4 }), textField("referral2-spec", { span: 4, list: P.REFERRAL_SPECIALTIES }), textField("referral2-diag", { span: 4 })),
          ["referral1-doc", "referral1-spec", "referral1-diag"], () => ["referral1-doc", "referral1-spec", "referral1-diag"].some(n => String(S.values[n] || "").trim()), ["referral2-doc", "referral2-spec", "referral2-diag"])),
      h("div", { class: "subhead" }, "Imaging / orders"),
      // The PDF has two order lines, as editable combo boxes: type to search the clinic's list or write any order.
      // A third order isn't lost: it goes into the plan notes (which continue onto an added page when long).
      h("div", { class: "grid" }, textField("Imaging", { span: 12, list: "dl-imaging-orders", placeholder: "Type to search orders (MRI, MRA, CTA, carotid, DaTscan…)" }),
        textField("Imaging-2", { span: 12, list: "dl-imaging-orders", placeholder: "Second order" }),
        moreOrdersField(),
        h("div", { class: "f s2", style: "justify-content:flex-end" }, h("div", { class: "row" }, check("PTcheck"))), textField("referral-for", { span: 10, list: P.PT_REFERRAL })),
    ], { span: "full" }));
  }
  const ORDER_BOXES = ["Imaging", "Imaging-2"];
  function imagingOrdersList() {
    if (!document.getElementById("dl-imaging-orders")) {
      const opts = [...new Set([...(S.phrases.IMAGING_ORDERS || []), ...(F().Imaging?.options || [])])];
      wrap_dl(h("datalist", { id: "dl-imaging-orders" }, opts.map(v => h("option", { value: v }))));
    }
  }
  // An imaging order into the next free order line; with both lines taken it goes to the plan notes instead.
  function placeImaging(name, { quiet = false, silent = false } = {}) {
    name = String(name || "").trim();
    if (!name) return "";
    const same = (a, b) => String(a || "").trim().toLowerCase() === b.toLowerCase();
    if (ORDER_BOXES.some(n => same(S.values[n], name))) { if (!quiet) toast(`${name} is already ordered.`); return "same"; }
    const free = ORDER_BOXES.find(n => !String(S.values[n] || "").trim());
    if (free) { setValue(free, name, { silent }); return free; }
    const cur = (S.values["plan-descr"] || "").replace(/\r/g, "\n");
    if (!cur.toLowerCase().includes(name.toLowerCase())) setValue("plan-descr", (cur.replace(/\s+$/, "") + "\n- Imaging ordered: " + name).replace(/^\n/, ""), { silent });
    if (!quiet) toast("Both order lines are in use: the order was added to the plan notes.");
    return "plan";
  }
  function moreOrdersField() {
    const inp = h("input", { id: "f-imaging-more", type: "text", list: "dl-imaging-orders", autocomplete: "off", spellcheck: "false", placeholder: "Another order (kept in the plan notes)" });
    const take = () => { const v = inp.value.trim(); if (!v) return; const where = placeImaging(v, { quiet: true }); inp.value = ""; toast(where === "plan" ? `${v}: added to the plan notes (the PDF has two order lines).` : where === "same" ? `${v} is already ordered.` : `${v}: put on order line ${where === "Imaging" ? 1 : 2}.`); };
    inp.addEventListener("change", take);
    inp.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); take(); } });
    const f = h("div", { class: "f s12" }, h("label", { for: "f-imaging-more" }, "More orders"), inp);
    return when(f, ORDER_BOXES, () => ORDER_BOXES.every(n => String(S.values[n] || "").trim()));
  }
  // A reviewed study into the next free study row; with all five taken it goes to "History / imaging continued".
  function placeStudy(s, { silent = false } = {}) {
    const name = String(s.name || "").trim(), date = String(s.date || "").trim(), result = String(s.result || "").trim();
    if (!name) return "";
    const v = S.values, cols = (i) => [`x-study${i}`, `x-study${i}-date`, `x-study${i}-result`];
    for (let i = 1; i <= 5; i++) {
      if (String(v[`x-study${i}`] || "").trim().toLowerCase() === name.toLowerCase() && (!date || String(v[`x-study${i}-date`] || "").trim() === date)) {
        if (result && !String(v[`x-study${i}-result`] || "").trim()) setValue(`x-study${i}-result`, result, { silent });
        return "same";
      }
    }
    const i = [1, 2, 3, 4, 5].find(k => cols(k).every(n => !String(v[n] || "").trim()));
    if (i) { setValue(`x-study${i}`, name, { silent }); setValue(`x-study${i}-date`, date, { silent }); setValue(`x-study${i}-result`, result, { silent }); return i; }
    const line = `${name}${date ? " " + date : ""}: ${result || "see report"}`;
    const cur = (v["family-other-descr"] || "").replace(/\r/g, "\n");
    if (!cur.includes(line)) setValue("family-other-descr", (cur.replace(/\s+$/, "") + "\n" + line).replace(/^\n/, ""), { silent });
    return "more";
  }

  // ------------------------------------------------------------------ Build (write the note out)
  // Type or paste: the provider's own notes go into the story as written (Build then reads them, as it reads dictation);
  // a visit transcript is read for what the conversation makes plain and stays out of the note.
  function looksLikeVisit(t) {
    const lines = t.split(/\n/).filter(l => l.trim());
    const speaker = /^\s*(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*)?(?:dr\.?(?:\s+\w+)?|doctor|provider|physician|clinician|pa|np|nurse|ma|patient|pt|speaker\s*\d+|wife|husband|spouse|partner|daughter|son|mother|mom|father|dad|caregiver|companion|family|friend|sister|brother)\s*(?:\(\w+\))?\s*:/i;
    if (lines.filter(l => speaker.test(l)).length >= 2) return true;
    const count = (rx) => (t.match(rx) || []).length;
    return count(/\?/g) >= 3 && count(/\b(?:you|your|you're|you've)\b/gi) >= 3 && count(/\b(?:i|i'm|i've|my|me)\b/gi) >= 3;
  }
  function looksLikeScope(t) {
    if (/DIZZYDOCTOR\s+SCOPE\s+TRANSFER\s+V1/i.test(t)) return true;
    const heads = ["CHIEF COMPLAINT", "HISTORY OF PRESENT ILLNESS", "NEUROLOGIC EXAM", "ASSESSMENT", "NEEDS REVIEW"];
    return heads.filter(x => new RegExp("(?:^|\\n)\\s*(?:#{1,6}\\s*)?(?:\\*{1,2})?" + x + "(?:\\*{1,2})?\\s*:?(?:\\n|$)", "i").test(t)).length >= 3;
  }
  // Paste anything (rough notes, a Scope note, a visit conversation) and it is reformatted into the note straight
  // away; typed text goes in with the wand. What kind of text it is is worked out, not asked.
  function pasteCard() {
    const P = S.paste || (S.paste = { text: "", kind: "auto" });
    const ta = h("textarea", { class: "paste-text", rows: 3, spellcheck: "true", "aria-label": "Paste or type notes, a Scope note or a visit conversation",
      placeholder: "Paste or type notes, a Scope note or a visit conversation" });
    ta.value = P.text;
    const go = h("button", { class: "btn primary wand", type: "button", title: "Show proposed changes without applying them (Ctrl+Enter)" }, icon("wand"), "Review changes");
    const kindOf = (t) => (looksLikeScope(t) ? "scope" : looksLikeVisit(t) ? "visit" : "notes");
    const paint = () => { go.disabled = !ta.value.trim(); };
    const run = () => { const t = ta.value.trim(); if (t) readPasted(t, kindOf(t)); };
    ta.addEventListener("input", () => { P.text = ta.value; paint(); });
    // Pasting only stages the source. The provider explicitly asks to review it; nothing changes behind the paste.
    ta.addEventListener("paste", () => setTimeout(() => { P.text = ta.value; paint(); }, 0));
    ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run(); } });
    go.addEventListener("click", run);
    paint();
    return h("details", { class: "paste-card", open: !!P.text || P.kind === "visit" },
      h("summary", {}, icon("right"), icon("paste"), "Paste notes, a Scope note or a visit conversation"), h("div", { class: "paste-row" }, ta, go),
      h("div", { class: "paste-hint" }, "Pasting does not change the note. Click Review changes to see removals in red and additions in green before writing anything."));
  }

  async function readPasted(text, kind) {
    text = (text || "").trim();
    if (!text) return;
    if (kind === "auto") kind = looksLikeScope(text) ? "scope" : (looksLikeVisit(text) ? "visit" : "notes");
    if (kind === "notes") {
      checkpoint("paste:notes", true);
      const cur = (S.values["ChiefComplaint-descr"] || "").replace(/\r/g, "\n").replace(/\s+$/, "");
      setValue("ChiefComplaint-descr", cur ? cur + "\n\n" + text : text, { silent: true });
      onChange("paste");
      S.paste = { text: "", kind: "auto" };
      toast("Reformatted into the story");
      renderBuild();
      return;
    }
    if (kind === "scope") {
      let r;
      try { r = await api.post("/api/scope/import", { text, values: S.values }); }
      catch (e) { toast("Couldn't import the Scope note: " + (e.message || e)); return; }
      if (!r || r.error) { toast((r && r.error) || "Couldn't import the Scope note."); return; }
      // The Scope note's HPI is the story: it goes into the story box now (Undo is one click) so Build drafts the
      // HPI from it in the clinic's wording, the same as typed or dictated notes. Offered as a card of its own it
      // would be written over by the builder's HPI, and never built from.
      const hpi = (r.proposals || []).find(pr => pr.id === "scope-hpi");
      if (hpi && hpi.text) {
        checkpoint("paste:scope", true);
        const cur = (S.values["ChiefComplaint-descr"] || "").replace(/\r/g, "\n").replace(/\s+$/, "");
        const already = cur.includes(hpi.text.trim());
        if (!already) setValue("ChiefComplaint-descr", cur ? cur + "\n\n" + hpi.text : hpi.text, { silent: true });
        if (!already) onChange("paste");
        r.proposals = r.proposals.filter(pr => pr.id !== "scope-hpi");
        if (!already) r.notes = [...(r.notes || []), "HPI put into the story"];
      }
      S.heard = { mode: "scope", pasted: true, text, reading: r, notes: r.notes || [], unclear: r.unclear || [] };
      S.paste = { text: "", kind: "auto" };
      toast(hpi ? "Scope note read: the HPI is in the story, the rest is below to confirm" : "Scope note reformatted: check the parts below");
      renderBuild();
      return;
    }
    let r;
    try { r = await api.post("/api/read", { text, kind: "visit", values: composeValues(), defaults: [...(S.defaulted || [])] }); }
    catch (e) { toast("Couldn't read the transcript: " + (e.message || e)); return; }
    if (!r || r.error) { toast((r && r.error) || "Couldn't read the transcript."); return; }
    const rd = r.reading || {};
    S.heard = { mode: "visit", pasted: true, text, reading: rd, notes: rd.notes || [], unclear: rd.unclear || [] };
    S.paste = { text: "", kind: "auto" };
    renderBuild();
  }
  // ------------------------------------------------------------------ Build: what the note still needs, asked in order
  // Each missing piece is a field right here, in the order the note prints it (like filling in an address): answer it
  // and it goes into the note and into the drafted text below, without leaving Build.
  const ASKED_TODO = [/visit type/i, /HPI opening still needs/i, /^Impression #\d+ .*write the plan/i, /^Exam: .*marked abnormal/i,
                      /^Oculography: write the treatment/i, /^Medications: go over the list/i];
  function needsCard(rows, r) {
    const hpiRow = rows.find(x => x.pr.id === "hpi" && x.editor), impRow = rows.find(x => x.pr.id === "impression" && x.editor);
    const items = [];
    const ask = (key, label, control, done, deps = []) => items.push({ key, label, control, done, deps });
    // the drafted opening line follows each answer
    const opening = (fn) => {
      const ed = hpiRow && hpiRow.editor;
      if (!ed) return;
      const lines = ed.value.split("\n");
      lines[0] = fn(lines[0]);
      ed.value = lines.join("\n");
    };
    const VISIT = { "neuro-consult": "a consultation on", "neuro-eval": "an evaluation of", "neuro-progress": "a follow-up on", "neuro-urgent": "an evaluation of" };
    const visitNames = Object.keys(VISIT);
    if (!visitKind().id) {
      // the drafted opening follows the choice straight away; the full Visit type card above has the same chips
      const picker = visitPicker({ compact: true, onPick: () => requestAnimationFrame(() => {
        const kind = VISIT[visitNames.find(on)] || "";
        opening(l => l.replace(/(presenting to the clinic today for )(?:a consultation on |an evaluation of |a follow-up on )?/, `$1${kind ? kind + " " : ""}`));
      }) });
      ask("visit", "Visit type", picker, () => !!visitKind().id, KIND_FIELDS);
    }
    if (!on("hpi-male") && !on("hpi-female")) {
      const seg = segmented(["hpi-male", "hpi-female"]);
      seg.addEventListener("click", () => requestAnimationFrame(() => {
        const sex = on("hpi-female") ? "female" : on("hpi-male") ? "male" : "";
        if (sex) opening(l => l.replace(/^(?:Mr\.\/Ms\.|Mr\.|Ms\.)/, sex === "female" ? "Ms." : "Mr.").replace(/(HD )(?:\*\*\*|female|male)( presenting)/, `$1${sex}$2`));
      }));
      ask("sex", "Sex", seg, () => on("hpi-male") || on("hpi-female"), ["hpi-male", "hpi-female"]);
    }
    if (!parseDob(S.values.DOB) && !String(S.values["HPI-age"] || "").trim()) {
      const d = dateField("DOB", { span: 12, labelText: "", dob: true });
      d.querySelector("input").addEventListener("blur", () => { const age = String(S.values["HPI-age"] || ""); if (age) opening(l => l.replace(/ \*\*\* y\/o/, ` ${age} y/o`)); });
      ask("dob", "Date of birth", d, () => !!parseDob(S.values.DOB), ["DOB"]);
    }
    const hpiNow = () => (hpiRow ? hpiRow.editor.value : String(S.values["ChiefComplaint-descr"] || ""));
    if (/\*\*\*HD\b/.test(hpiNow())) {
      const seg = h("div", { class: "seg", role: "radiogroup", "aria-label": "Handedness" });
      let picked = "";
      for (const [code, label] of [["R", "Right-handed"], ["L", "Left-handed"]]) {
        const b = h("button", { type: "button", role: "radio" }, label);
        b.addEventListener("click", () => {
          picked = code;
          [...seg.children].forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", String(x === b)); });
          if (hpiRow) opening(l => l.replace(/(?:\*\*\*|[RL])HD\b/, code + "HD"));
          else setValue("ChiefComplaint-descr", String(S.values["ChiefComplaint-descr"] || "").replace(/\*\*\*HD\b/, code + "HD"));
          markDone("hand");
        });
        seg.append(b);
      }
      ask("hand", "Handedness", seg, () => !!picked);
    }
    // the abnormal exam rows nobody has described
    for (const row of S.schema.exam_rows) {
      if (row.findings && String(S.values[row.abnormal] || "").trim() && !String(S.values[row.findings] || "").trim())
        ask("exam-" + row.findings, "Exam: " + row.label, textField(row.findings, { span: 12, labelText: "", list: (S.phrases.EXAM_ABNORMAL || {})[row.findings] || S.phrases.EXAM_NORMALS[row.findings] || [] }),
            () => !!String(S.values[row.findings] || "").trim(), [row.findings]);
    }
    if (["imp-rightpost", "imp-leftpost", "imp-rightlateral", "imp-leftlateral", "imp-rightanterior", "imp-leftanterior", "imp-rightcupu", "imp-leftcupu"].some(on) && !String(S.values["goggle-treatment-x"] || "").trim())
      ask("tx", "Oculography treatment", textField("goggle-treatment-x", { span: 12, labelText: "", list: S.phrases.GOGGLE_TREATMENT }), () => !!String(S.values["goggle-treatment-x"] || "").trim(), ["goggle-treatment-x"]);
    // each impression that still has no plan: typed here, written into the drafted impression / plan below
    const impText = () => (impRow ? impRow.editor.value : String(S.values["impression-plan-descr"] || ""));
    for (const m of impText().matchAll(/^\s*(\d{1,2})\)\s*(.+?)\s*-\s*\*{3}\s*$/gm)) {
      const n = m[1], dx = m[2];
      const inp = h("input", { type: "text", class: "ask-input", placeholder: "Plan", "aria-label": `Plan for impression ${n}, ${dx}`, list: "dl-plan-sentences", autocomplete: "off" });
      wrap_dl(h("datalist", { id: "dl-plan-sentences" }, (S.phrases.IMPRESSION_SENTENCES || []).map(v => h("option", { value: v }))));
      inp.addEventListener("input", () => {
        const rx = new RegExp(`^(\\s*${n}\\)\\s*${dx.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*-\\s*).*$`, "m");
        const plan = inp.value.trim() || "***";
        if (impRow) impRow.editor.value = impRow.editor.value.replace(rx, `$1${plan}`);
        else setValue("impression-plan-descr", String(S.values["impression-plan-descr"] || "").replace(rx, `$1${plan}`));
        markDone("imp" + n, !!inp.value.trim());
      });
      ask("imp" + n, `Plan for #${n} ${dx}`, inp, () => !!inp.value.trim());
    }
    if (!medsReviewed()) {
      const btn = h("button", { class: "btn small", type: "button", onclick: () => { meds().markReviewed(); markDone("meds"); renderNav(); } }, icon("check"), "Reviewed with the patient");
      items.push({ key: "meds", wide: true, label: h("b", {}, "Medication list"), done: () => medsReviewed(), deps: [],
        control: h("div", { class: "row" }, h("button", { class: "linkish", type: "button", onclick: () => goSection("meds") }, "Open Medications"), btn) });
    }
    reviewItems(r, items, rows);
    if (!items.length) return null;
    const list = h("div", { class: "asks" });
    for (const it of items) {
      const mark = h("span", { class: "ask-mark", "aria-hidden": "true" }, icon("check"));
      const rowEl = h("div", { class: "ask" + (it.wide ? " wide" : ""), "data-ask": it.key }, h("div", { class: "ask-label" }, mark, h("span", {}, it.label)), h("div", { class: "ask-control" }, it.control));
      const sync = () => rowEl.classList.toggle("done", !!it.done());
      for (const d of it.deps) bind(d, rowEl, () => 0, sync);
      sync();
      list.append(rowEl);
    }
    return card("Needs you", [h("div", { class: "muted ask-intro" }, "Answer each one here, in order; Tab moves to the next. Answers go straight into the note."), list], { span: "asks-card" });
  }
  // ---------- what the provider has settled
  // Every point Build or a pasted note raises (said both ways, unsure, marked for review) is settled once: accept a
  // side, dismiss it or mark it done. The answer is kept on the note itself (build-resolved), so it survives a
  // rebuild, a tab switch and a restart with the draft, and the point doesn't come back.
  function resolved(create = false) {
    const r = S.values["build-resolved"];
    if (r && typeof r === "object" && !Array.isArray(r)) return r;
    return create ? (S.values["build-resolved"] = {}) : {};
  }
  const isResolved = (key) => !!resolved()[key];
  function resolve(key, action = "ok") {
    resolved(true)[key] = { action, at: todayStr() };
    S.dirty = true; markDone(key); keepDraft(); paintBuildBadge();
  }
  function unresolve(key) { delete resolved(true)[key]; S.dirty = true; keepDraft(); paintBuildBadge(); }
  // How many points are still open on Build: shown on its tab like the Check count.
  function openWarnings() {
    const lines = new Set([...((S.lastBuild && S.lastBuild.check) || []), ...((S.heard && S.heard.unclear) || [])]);
    return [...lines].filter(t => !isResolved("check|" + t)).length;
  }
  function paintBuildBadge() { const b = $("#viewSwitch [data-view='build']"); if (!b) return; const n = openWarnings(); const old = b.querySelector(".step-badge"); if (old) old.remove(); if (n) b.append(h("span", { class: "step-badge", title: "Points to settle on Build" }, String(n))); }
  // The boxes a "said both ways" / "is uncertain" line is about: a symptom's present / denies pair, falls, a history
  // box and its denial, or any one box by its label.
  const UNCERTAIN = { "Dizziness": ["dizziness", "dizziness-denies"], "Hearing loss": ["hearingloss", "hearingloss-denies"], "Tinnitus": ["tinnitus", "tinnitus-denies"],
                      "Gait": ["gaitataxia", "gaitataxia-denies"], "Headache": ["headache", "headache-denies"], "Falls": ["ga-falls-yes", "ga-falls-no"] };
  function subjectBoxes(subject) {
    const s = String(subject || "").trim().toLowerCase();
    for (const [name, pair] of Object.entries(UNCERTAIN)) if (name.toLowerCase() === s) return pair;
    const pmh = Object.keys(F()).find(n => n.startsWith("hist-") && F()[n].type === "checkbox" && F()[n].label.toLowerCase() === s);
    if (pmh) return [pmh, F()[pmh + "-denies"] ? pmh + "-denies" : ""];
    const any = Object.keys(F()).filter(n => F()[n].type === "checkbox" && F()[n].label.toLowerCase() === s);
    return any.length === 1 ? [any[0], ""] : null;
  }
  // "Said both ways: falls: “Denies any falls.”" -> kind, subject and the quoted words; a sentence that goes on after
  // the quote ("Your words say “X” but the box says “Y”.") is kept whole.
  const splitWarn = (t) => {
    const m = /^([^\u201c]*?)(?::\s*)?\u201c(.+?)\u201d\s*(.*)$/.exec(t);
    if (!m || m[3].replace(/[.\s]/g, "")) return { quote: "", kind: t.trim(), subject: "" };
    const head = m[1].replace(/[:\s]+$/, ""), i = head.indexOf(":");
    return { quote: m[2], kind: i > 0 ? head.slice(0, i).trim() : head.trim(), subject: i > 0 ? head.slice(i + 1).trim() : "" };
  };
  // The buttons that settle one line. `after` runs once it is settled (the list redraws).
  function warnActions(t, key, after) {
    const { kind, subject, quote } = splitWarn(t);
    const btn = (label, action, fn, cls = "btn small") => h("button", { class: cls, type: "button", onclick: () => { if (fn) fn(); resolve(key, action); if (after) after(); } }, label);
    const done = (label = "OK") => btn(label, "ok");
    const sym = /^(.+?) (?:is|are) uncertain$/.exec(kind);
    const pair = sym ? subjectBoxes(sym[1]) : /^Said (?:both|two) ways$/i.test(kind) ? subjectBoxes(subject) : null;
    if (pair) {
      const [yes, no] = pair;
      const set = (v) => { setValue(yes, v === "yes"); if (no && F()[no]) setValue(no, v === "no"); };
      return [btn(sym ? "Has it" : "Yes", "yes", () => set("yes")), (no && F()[no]) || !sym ? btn(sym ? "Denies" : "No", "no", () => set("no")) : null,
              done(sym ? "Leave as possible" : "Leave both off")].filter(Boolean);
    }
    if (/review/i.test(kind)) {
      const toPlan = () => { const cur = (S.values["plan-descr"] || "").replace(/\s+$/, ""); const line = "- Review: " + (subject || quote || t); setValue("plan-descr", cur ? cur + "\n" + line : line); toast("Added to the plan notes"); };
      return [btn("Add to plan notes", "plan", toPlan), done("Resolved")];
    }
    if (/medication|meds?\b|bp med/i.test(quote || subject)) return [h("button", { class: "linkish", type: "button", onclick: () => goSection("meds") }, "Medications"), done()];
    return [done()];
  }
  // A pasted note's open points, grouped (said both ways, unsure, marked for review, not placed) with a count on each
  // group, closed when long. Each line has its buttons; settled lines fold away under "resolved".
  const WARN_GROUPS = [["Said both ways", /^Said (?:both|two) ways/i], ["Unsure", /^Unsure|is uncertain$|are uncertain$|^Check$/i],
                       ["Marked for review", /marked for review/i], ["Unfinished", /^Unfinished/i], ["Not placed", /./]];
  function unclearList(lines, { title = "Needs review" } = {}) {
    const wrap = h("div", { class: "warn-groups" });
    const draw = () => {
      wrap.innerHTML = "";
      const groups = new Map();
      for (const t of lines) { const g = (WARN_GROUPS.find(([, rx]) => rx.test(splitWarn(t).kind)) || ["Not placed"])[0]; (groups.get(g) || groups.set(g, []).get(g)).push(t); }
      let open = 0;
      for (const [name, items] of groups) {
        const todo = items.filter(t => !isResolved("check|" + t)), done = items.filter(t => isResolved("check|" + t));
        open += todo.length;
        const det = h("details", { class: "warn-group" + (todo.length ? "" : " all-done"), open: todo.length > 0 && todo.length <= 3 });
        const all = todo.length > 1 ? h("button", { class: "linkish", type: "button", onclick: (e) => { e.preventDefault(); for (const t of todo) resolve("check|" + t, "ok"); draw(); } }, "Dismiss all") : null;
        det.append(h("summary", {}, icon("right"), icon("alert"), h("b", {}, name), h("span", { class: "step-badge" + (todo.length ? "" : " zero") }, String(todo.length)),
                     done.length ? h("span", { class: "muted" }, `${done.length} resolved`) : null, h("span", { class: "spacer" }), all));
        const list = h("div", { class: "list warn-list" });
        for (const t of todo) {
          const { kind, subject, quote } = splitWarn(t), key = "check|" + t;
          list.append(h("div", { class: "check-line" }, h("span", { class: "check-text" }, h("b", {}, kind + (subject ? ": " + subject : "")), quote ? " \u201c" + quote + "\u201d" : ""),
                                h("span", { class: "check-acts row" }, warnActions(t, key, draw))));
        }
        if (done.length) {
          const more = h("details", { class: "warn-done" }, h("summary", {}, `Resolved (${done.length})`), done.map(t => {
            const { kind, subject, quote } = splitWarn(t), key = "check|" + t, how = (resolved()[key] || {}).action || "ok";
            return h("div", { class: "check-line done" }, h("span", { class: "check-text" }, h("b", {}, kind + (subject ? ": " + subject : "")), quote ? " \u201c" + quote + "\u201d" : "",
                     h("span", { class: "tag" }, { yes: "accepted yes", no: "accepted no", plan: "in plan notes", ok: "dismissed" }[how] || how)),
                     h("button", { class: "linkish", type: "button", onclick: () => { unresolve(key); draw(); } }, "Undo"));
          }));
          list.append(more);
        }
        det.append(list);
        wrap.append(det);
      }
      wrap.prepend(h("div", { class: "group-title warn-title" }, title, " ", h("span", { class: "muted" }, open ? `${open} open` : "all settled")));
      paintBuildBadge();
    };
    draw();
    return wrap;
  }
  // The points Build couldn't settle on its own, each with the one or two actions that settle it; settled ones stay
  // settled on the next Build (see resolved()).
  function reviewItems(r, items, rows = []) {
    const quote = (t) => (/\u201c(.+?)\u201d/.exec(t) || [])[1] || "";
    const todos = (r.todo || []).filter(t => !ASKED_TODO.some(rx => rx.test(t)));
    const medQuotes = new Set(todos.map(quote).filter(Boolean));
    const seen = { has: isResolved };
    const put = (key) => resolve(key, "ok");
    const okBtn = (key, label = "OK") => h("button", { class: "btn small", type: "button", onclick: () => put(key) }, label);
    for (const t of r.check || []) {
      const key = "check|" + t, { kind, subject, quote: q } = splitWarn(t);
      if (seen.has(key) || (q && medQuotes.has(q))) continue;
      const label = h("span", { class: "ask-quote" }, h("b", {}, kind + (subject ? ": " + subject : "")), q ? " \u201c" + q + "\u201d" : "");
      const sym = /^(.+?) (?:is|are) uncertain$/.exec(kind);
      const pair = sym ? subjectBoxes(sym[1]) : /^Said (?:both|two) ways$/i.test(kind) ? subjectBoxes(subject) : null;
      if (pair) {
        const [yes, no] = pair;
        const act = (v) => { setValue(yes, v === "yes"); if (no && F()[no]) setValue(no, v === "no"); resolve(key, v); };
        items.push({ key, wide: true, label, done: () => seen.has(key), deps: [],
          control: h("div", { class: "row" }, h("button", { class: "btn small", type: "button", onclick: () => act("yes") }, sym ? "Has it" : "Yes"),
            (no && F()[no]) || !sym ? h("button", { class: "btn small", type: "button", onclick: () => act("no") }, sym ? "Denies" : "No") : null,
            okBtn(key, sym ? "Leave as possible" : "Leave both off")) });
      } else if (/^Unfinished/.test(kind) && /\*\*\*|\.\.\.|\u2026/.test(q)) {
        const cut = ["***", "...", "\u2026"].find(m => q.includes(m));
        const inp = h("input", { type: "text", class: "ask-input", placeholder: "Finish the sentence", "aria-label": "Finish: " + q });
        const finish = () => {
          const words = inp.value.trim(); if (!words) return;
          const before = q.slice(0, q.indexOf(cut));
          // the story may hold the raw words ("to...") or the reformatted ones ("to ***")
          const swap = (txt) => { for (const m of ["***", "...", "\u2026"]) { const at = txt.indexOf(before.trimEnd() + (m === "***" ? " " : "") + m); if (at >= 0) { const old = before.trimEnd() + (m === "***" ? " " : "") + m; return txt.slice(0, at) + before.trimEnd() + " " + words.replace(/\.$/, "") + "." + txt.slice(at + old.length); } } return null; };
          const ed = ((rows || []).find(x => x.pr.id === "hpi" && x.editor) || {}).editor;
          const cur = String(S.values["ChiefComplaint-descr"] || "");
          let found = false;
          if (ed && swap(ed.value) != null) { ed.value = swap(ed.value); found = true; }
          if (swap(cur) != null) { setValue("ChiefComplaint-descr", swap(cur)); found = true; }
          if (!found) toast("Couldn't find that sentence any more; finish it in Story.");
          put(key);
        };
        inp.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); finish(); } });
        inp.addEventListener("change", finish);
        items.push({ key, wide: true, label, done: () => seen.has(key), deps: [], control: h("div", { class: "row ask-finish" }, h("span", { class: "muted" }, "\u2026" + q.slice(0, q.indexOf(cut)).trim().split(" ").slice(-3).join(" ")), inp) });
      } else {
        items.push({ key, wide: true, label, done: () => seen.has(key), deps: [],
          control: h("div", { class: "row" }, /medication|meds?\b|bp med/i.test(q) ? h("button", { class: "linkish", type: "button", onclick: () => goSection("meds") }, "Medications") : null,
            okBtn(key)) });
      }
    }
    for (const t of todos) {
      const key = "todo|" + t;
      if (seen.has(key)) continue;
      const missing = /^Your notes mention (.+?), not on the medication list/.exec(t);
      if (missing) {
        // One button per drug: it goes on the list with the dose the notes give, without leaving Build.
        const names = missing[1].split(/,\s*(?:and\s+)?|\s+and\s+/).map(x => x.trim()).filter(Boolean);
        const context = [S.paste && S.paste.text, S.values["ChiefComplaint-descr"], S.heard && S.heard.text].filter(Boolean).join("\n");
        const left = new Set(names);
        const buttons = names.map(n => {
          const b = h("button", { class: "btn small", type: "button" }, icon("plus"), "Add " + n);
          b.addEventListener("click", () => {
            const added = meds().addMentioned(n, context);
            toast(added ? `${n} added to the medication list.` : `${n} is already on the medication list.`);
            b.disabled = true; b.replaceChildren(icon("check"), n + " on the list");
            left.delete(n); if (!left.size) put(key);
            renderNav();
          });
          return b;
        });
        items.push({ key, wide: true, label: h("span", { class: "ask-quote" }, t.replace(/\s*\(Meds\)/, "")), done: () => seen.has(key), deps: [],
          control: h("div", { class: "row" }, buttons, okBtn(key, "Leave off")) });
        continue;
      }
      const step = TODO_STEPS.find(([rx]) => rx.test(t));
      const text = step ? t.replace(/\s*\((?:Meds|on Patient)\)/, "") : t;
      items.push({ key, wide: true, label: h("span", { class: "ask-quote" }, text), done: () => seen.has(key), deps: [],
        control: h("div", { class: "row" }, step ? h("button", { class: "linkish", type: "button", onclick: () => goSection(step[1]) }, step[2]) : null, okBtn(key, "Done")) });
    }
  }
  function markDone(key, done = true) { const el = $(`#panel .ask[data-ask="${key}"]`); if (el) el.classList.toggle("done", done); }

  // "(Meds)", "on Patient", "Exam:" in a to-do: a button that goes there.
  const TODO_STEPS = [[/\(Meds\)|medication/i, "meds", "Medications"], [/on Patient|\(on Patient\)|visit type/i, "patient", "Patient"], [/^Exam:/i, "exam", "Exam"],
                      [/Oculography/i, "oculography", "Oculography"], [/Impression #|plan\b/i, "plan", "Plan"], [/opening|HPI/i, "story", "Story"]];
  function todoLine(t, stepId) {
    const step = stepId ? TODO_STEPS.find(([, id]) => id === stepId) : TODO_STEPS.find(([rx]) => rx.test(t));
    const text = step ? t.replace(/\s*\((?:Meds|on Patient)\)/, "") : t;
    return h("div", { class: "warn todo-line" }, h("span", {}, text), step ? h("button", { class: "linkish", type: "button", onclick: () => goSection(step[1]) }, step[2]) : null);
  }
  // Build, top to bottom: the HPI (what the provider types, dictates or pastes), then what follows from it: the visit
  // type, the complaints the HPI brings up with their boxes, the HPI as the clinic writes it, what still needs the
  // provider, exam and plan. The HPI card stays put while the rest is rebuilt from it, so typing never loses its place.
  function renderBuild() {
    const p = $("#panel"); p.innerHTML = "";
    p.append(hpiCard(), h("div", { id: "buildBelow", style: "display:contents" }));
    return buildBelow();
  }
  function hpiCard() {
    const story = textArea("ChiefComplaint-descr", { cls: "big hpi-input", labelText: "", phrases: hpiPhrases() });
    const ta = story.querySelector("textarea");
    ta.placeholder = "Start here. Type, dictate (F9) or paste the story: fragments and abbreviations are fine. What it says decides the complaints and boxes below.";
    ta.addEventListener("input", scheduleBuild);
    const cc = textField("ChiefComplaint", { span: 12, labelText: "Chief complaint", list: CC_CHOICES });
    cc.querySelector("input").addEventListener("input", scheduleBuild);
    const el = card("HPI", [cc, story, pasteCard()], { span: "hpi-card",
      tools: [h("button", { class: "tool", type: "button", title: "Paste a visit transcript: Build reads it and keeps it out of the note", onclick: pasteTranscript }, icon("paste"), "Paste a transcript"), ...liftTools(story)] });
    el.dataset.outline = "HPI";
    return el;
  }
  let buildTimer = null, buildSeq = 0;
  function scheduleBuild() { clearTimeout(buildTimer); buildTimer = setTimeout(() => { if (S.tab === "build") buildBelow(); }, 900); }
  async function buildBelow() {
    const p = $("#panel"), below = $("#buildBelow");
    if (!below) return renderBuild();
    const seq = ++buildSeq;
    if (!below.childElementCount) below.append(card("", h("div", { class: "loading" }, h("span", { class: "spinner" }), "Reading the HPI")));
    else p.classList.add("rebuilding");
    // a visit transcript is read again against the note as it is now, so boxes changed by hand since aren't offered again
    const visitAgain = S.heard && S.heard.mode === "visit" && S.heard.text
      ? api.post("/api/read", { text: S.heard.text, kind: "visit", values: composeValues(), defaults: [...(S.defaulted || [])] }).catch(() => null) : null;
    let r;
    try { r = await api.post("/api/compose", { values: composeValues(), previous: S.generated, last_seen: lastSeen(), defaults: [...(S.defaulted || [])] }); }
    catch (e) { r = { error: e.message || "The note builder isn't available." }; }
    if (visitAgain) { const v = await visitAgain; if (v && v.reading && S.heard) S.heard.reading = v.reading; }
    if (S.tab !== "build" || seq !== buildSeq || !below.isConnected) return;
    p.classList.remove("rebuilding");
    below.innerHTML = "";
    for (const [k, els] of S.inputs) S.inputs.set(k, els.filter(el => el.isConnected));
    if (!r || r.error || !Array.isArray(r.proposals)) { below.append(card("Build the note", h("div", { class: "warn" }, (r && r.error) || "The note builder isn't available."))); renderNav(); return; }
    S.lastBuild = { check: r.check || [], todo: r.todo || [] };
    const rows = [];
    // a pasted Scope note's own visit type is offered on the Visit type card, not as a card of its own
    const heardProps = ((S.heard && S.heard.reading && S.heard.reading.proposals) || []).filter(pr => pr.id !== "scope-visit");
    const scopeVisit = ((S.heard && S.heard.reading && S.heard.reading.proposals) || []).find(pr => pr.id === "scope-visit");
    const visit = visitCard(scopeVisit); visit.dataset.outline = "Visit type";
    below.append(visit);
    const prep = prepCard();
    if (prep) { prep.dataset.outline = "Last visit"; below.append(prep); }
    if (S.heard) below.append(heardCard());
    // one text box, one card: what a pasted note gives a box outright (the chief complaint) stands; the builder's own
    // guess for the same box steps aside. The HPI is never both: a pasted HPI goes into the story and is built from.
    const heardFields = new Set(heardProps.filter(pr => pr.field && !pr.requires_confirmation).map(pr => pr.field));
    for (const pr of heardProps) proposalCard(pr, rows, below);
    const props = r.proposals.filter(pr => !(pr.field && heardFields.has(pr.field)));
    complaintBlocks(r.topics || [], props.find(pr => pr.id === "story"), rows, below);
    const status = h("div", { class: "muted" });
    const tools = [h("button", { class: "tool", type: "button", onclick: buildBelow }, icon("refresh"), "Rebuild")];
    if (S.undo) tools.push(h("button", { class: "tool", type: "button", onclick: undoLast }, icon("undo"), "Undo"));
    below.append(card("Built from the HPI", status, { tools }));
    const needsSlot = h("div", { style: "display:contents" });
    for (const pr of props) if (pr.id === "cc" || pr.id === "hpi") proposalCard(pr, rows, below);
    below.append(needsSlot);
    for (const pr of props) if (!["story", "cc", "hpi"].includes(pr.id)) proposalCard(pr, rows, below);
    const needs = needsCard(rows, r);
    if (needs) { needs.dataset.outline = "Needs you"; needsSlot.append(needs); }
    status.textContent = rows.length ? "Drafted in the clinic's wording from the HPI, your boxes and dictation. Untick or edit anything, then write it in."
      : needs ? "The note is up to date with the HPI. A few things below need you." : "The note is up to date with the HPI, your boxes and dictation.";
    if (rows.length) {
      const n = () => rows.reduce((a, x) => a + (x.items ? x.items.filter(i => i.cb.checked).length : (x.use.checked ? 1 : 0)), 0);
      const count = h("span", { class: "hint" });
      const paint = () => { const k = n(); count.textContent = k ? `${k} change${k > 1 ? "s" : ""} ready. Undo is one click.` : "Nothing ticked."; };
      below.addEventListener("change", paint);
      paint();
      below.append(h("div", { class: "step-nav build-write" },
        h("button", { class: "btn primary", type: "button", onclick: () => applyBuild(rows) }, icon("check"), "Write into the note"), count));
    }
    renderNav();
  }
  // The complaints, as the HPI has them. A row of chips says which the HPI brings up (present, denied, mentioned, not
  // in it); each complaint it brings up gets its card of boxes, with what the HPI says already ticked to write in. The
  // ones it doesn't mention stay one line, each one click from its boxes; Present / Patient denies overrides the HPI.
  function complaintBlocks(topics, storyPr, rows, below) {
    const items = (storyPr && storyPr.items) || [];
    const bySec = (sec) => items.filter(it => (it.section || "") === sec);
    const state = Object.fromEntries(topics.map(t => [t.id, t]));
    const relevant = SYMPTOMS.filter(sym => {
      const st = (state[sym.id] || {}).state;
      return st === "present" || st === "mentioned" || S.values[sym.on] || bySec(sym.id).some(it => !it.changes[sym.deny]);
    });
    const absent = SYMPTOMS.filter(sym => !relevant.includes(sym));
    const LABEL = { present: "in the HPI", mentioned: "mentioned", denied: "denied", absent: "not in the HPI" };
    const strip = h("div", { class: "hpi-topics" }, SYMPTOMS.map(sym => {
      const t = state[sym.id] || { state: "absent", why: "" };
      const st = relevant.includes(sym) && t.state === "absent" ? "present" : t.state;
      return h("button", { class: "hpi-topic " + st, type: "button", title: t.why ? "\u201c" + t.why + "\u201d" : LABEL[st],
        onclick: () => { const el = $(`#panel [data-sec-block="${sym.id}"]`); if (el) scrollBuildTo(el); else addComplaint(sym); } },
        h("b", {}, (t.title || sym.title)), h("span", {}, LABEL[st]));
    }));
    const head = card("Complaints from the HPI", [h("div", { class: "muted" }, topics.some(t => t.state !== "absent")
      ? "What the HPI says decides which boxes come next. Click one that isn't there to add it."
      : "Type the story above: the complaints it names come up here with their boxes."), strip], { span: "topics-card" });
    head.dataset.outline = "Complaints";
    below.append(head);
    for (const sym of relevant) {
      const t = state[sym.id] || {};
      const own = bySec(sym.id);
      let first = null;
      if (own.length) first = proposalCard({ ...storyPr, id: "story-" + sym.id, title: `${sym.title}: from the HPI`, why: t.why ? "\u201c" + t.why + "\u201d" : "", items: own }, rows, below);
      const c = renderSymptom(below, sym.id, { open: t.state === "present" });
      const seg = c.querySelector(".sym-head .seg");
      if (seg) seg.addEventListener("click", scheduleBuild);
      first = first || c;
      first.dataset.secBlock = sym.id;
      first.dataset.outline = t.title || sym.title;
      first.dataset.mark = S.values[sym.on] || t.state === "present" ? "pos" : t.state === "denied" ? "neg" : "";
    }
    const rest = items.filter(it => !relevant.some(sym => sym.id === it.section));
    if (rest.length) proposalCard({ ...storyPr, id: "story", title: "Other boxes from the HPI", items: rest }, rows, below);
    if (absent.length) {
      const add = (sym) => h("button", { class: "btn small", type: "button", title: `Add ${sym.title.toLowerCase()} and its boxes`, onclick: () => addComplaint(sym) }, icon("plus"), (state[sym.id] || {}).title || sym.title);
      const denied = absent.filter(sym => (state[sym.id] || {}).state === "denied"), unsaid = absent.filter(sym => !denied.includes(sym));
      const line = card("", h("div", { class: "row not-in-hpi" },
        ...(unsaid.length ? [h("span", { class: "muted" }, "Not in the HPI, left as denied:"), ...unsaid.map(add)] : []),
        ...(denied.length ? [h("span", { class: "muted" }, (unsaid.length ? "\u00b7 " : "") + "Denied in the HPI:"), ...denied.map(add)] : [])), { span: "absent-card" });
      line.dataset.outline = "Not in the HPI";
      below.append(line);
    }
  }
  function addComplaint(sym) { setValue(sym.on, true); buildBelow().then(() => { const el = $(`#panel [data-sec-block="${sym.id}"]`); if (el) scrollBuildTo(el); }); }
  function scrollBuildTo(el) {
    const p = $("#panel");
    p.scrollTo({ top: Math.max(0, p.scrollTop + el.getBoundingClientRect().top - p.getBoundingClientRect().top - 12), behavior: "smooth" });
  }
  // The visit type at the top of Build: what it is, in its colour, and where it came from (the schedule, Scope, the
  // note's boxes, or chosen here); one click on another chip overrides it. A pasted Scope note that says otherwise is
  // offered beside it, never applied on its own.
  function visitCard(scopeVisit) {
    const body = [visitPicker()];
    if (scopeVisit) {
      const kind = scopeVisit.changes && scopeVisit.changes["visit-kind"], t = VT().find(x => x.id === kind);
      const line = h("div", { class: "row visit-scope" });
      const paint = () => {
        const now = visitKind();
        line.hidden = !t || now.id === kind;
        if (line.hidden) return;
        line.replaceChildren(icon("paste"), h("span", {}, "Scope selected ", h("b", { style: `color:${t.color}` }, t.label), now.id ? ` (the note says ${now.type.label}, ${visitSource(now.source)})` : ""),
          h("button", { class: "btn small", type: "button", onclick: () => setVisitKind(kind, "scope") }, "Use Scope's"));
      };
      for (const n of KIND_FIELDS) bind(n, line, () => 0, paint);
      body.push(line);
    }
    return card("Visit type", body, { span: "visit-card" });
  }
  const same = (a, b) => (typeof b === "boolean" ? !!a === b : String(a ?? "").replace(/\r/g, "\n").trim() === String(b ?? "").replace(/\r/g, "\n").trim());
  // One built part: what the note says now next to the built text (editable), a list of boxes to tick one by one, or
  // the lines it changes. Parts the note already has are left out.
  function proposalCard(pr, rows, target = $("#buildBelow") || $("#panel")) {
    const selected = !pr.requires_confirmation;
    let items = null;
    if (pr.items && pr.items.length) {
      const left = pr.items.filter(it => Object.entries(it.changes).some(([k, v]) => !same(S.values[k], v)));
      if (!left.length) return;
      const rejected = S.rejected instanceof Set ? S.rejected : (S.rejected = new Set());
      items = left.map(it => ({ it, cb: h("input", { type: "checkbox", checked: selected && !rejected.has(pr.id + "|" + it.text), "aria-label": it.text }) }));
    } else if (!pr.field && Object.keys(pr.changes || {}).length && Object.entries(pr.changes).every(([k, v]) => same(S.values[k], v))) return;
    else if (pr.field && same(S.values[pr.field], pr.text)) return;
    const use = h("input", { type: "checkbox", checked: selected, "aria-label": "Use " + pr.title });
    let editor = null, body;
    if (items) {
      use.addEventListener("change", () => items.forEach(x => { x.cb.checked = use.checked; }));
      const sync = () => { const n = items.filter(x => x.cb.checked).length; use.checked = n > 0; use.indeterminate = n > 0 && n < items.length; };
      items.forEach(x => x.cb.addEventListener("change", sync));
      sync();
      body = h("div", { class: "build-items" }, items.map(({ it, cb }) => h("label", { class: "build-item" }, cb, h("span", { class: "bi-text" }, it.text),
        it.why ? h("span", { class: "bi-why", title: it.why }, "“" + it.why + "”") : null)));
    } else if (pr.field) {
      editor = h("textarea", { class: "build-text" + (pr.text.length > 160 ? " long" : ""), spellcheck: "true", "aria-label": pr.title + ", built" });
      editor.value = pr.text;
      // grow with the text so the end of a long HPI is never hidden inside the box
      const fit = () => { editor.style.height = "auto"; editor.style.height = Math.min(editor.scrollHeight + 2, 640) + "px"; };
      editor.addEventListener("input", fit); requestAnimationFrame(fit);
      // the HPI's paragraphs, one tick each (the opening, each complaint, the negatives, the closing line): untick one
      // and the built text is joined again without it; the provider's own words and a typed opening can't be dropped here
      let partRow = null;
      if (pr.parts && pr.parts.length > 1) {
        const ticks = pr.parts.map(pt => ({ pt, cb: h("input", { type: "checkbox", checked: true, disabled: !!pt.fixed, "aria-label": pt.title + (pt.fixed ? " (kept)" : "") }) }));
        const rebuild = () => { editor.value = ticks.filter(x => x.cb.checked).map(x => x.pt.text).join("\n\n"); fit(); };
        for (const x of ticks) x.cb.addEventListener("change", rebuild);
        partRow = h("div", { class: "build-parts" }, ticks.map(x => h("label", { class: "build-part" + (x.pt.fixed ? " fixed" : ""), title: x.pt.fixed ? "Your own words stay in" : x.pt.text }, x.cb, h("span", {}, x.pt.title))));
      }
      body = h("div", {}, partRow, h("div", { class: "build-compare" },
        h("div", {}, h("div", { class: "group-title" }, "The note now"), h("div", { class: "build-now" }, (pr.current || "").trim() || "(blank)")),
        h("div", {}, h("div", { class: "group-title" }, "Built"), editor)));
    } else {
      body = h("div", { class: "build-lines" }, (pr.lines || []).map(l => h("div", {}, l)));
    }
    rows.push({ pr, use, editor, items });
    const el = card("", [h("label", { class: "build-head" }, use, h("b", {}, pr.title),
      pr.requires_confirmation ? h("span", { class: "tag confirm", title: pr.confirmation_reason || "Confirm before applying" }, "Confirm") : null,
      h("span", { class: "muted" }, pr.why)), body,
      pr.requires_confirmation && pr.confirmation_reason ? h("div", { class: "confirm-reason" }, icon("alert"), pr.confirmation_reason) : null],
      { span: pr.id === "visit" || pr.id.startsWith("story") || pr.id === "scope-boxes" ? "boxes" : "" });
    if (OUTLINE[pr.id]) el.dataset.outline = OUTLINE[pr.id];
    target.append(el);
    return el;
  }
  // Build's parts as the left side lists them, so the menu follows the visit rather than the PDF's pages.
  const OUTLINE = { hpi: "HPI, as written", exam: "Exam", treatment: "Treatment", impression: "Impression & plan" };
  // What dictation, the visit recording or a pasted transcript said, what it ticked, and Undo.
  function heardCard() {
    const hd = S.heard, visit = hd.mode === "visit", scopeNote = hd.mode === "scope";
    const tools = [];
    if (S.undo && !visit) tools.push(h("button", { class: "tool", type: "button", onclick: undoLast }, icon("undo"), "Undo"));
    tools.push(h("button", { class: "tool", type: "button", title: "Hide", "aria-label": "Hide what was heard", onclick: () => { S.heard = null; renderBuild(); } }, icon("x")));
    const long = (hd.text || "").length > 600;
    const text = long ? h("details", { class: "heard-more" }, h("summary", {}, icon("right"), (hd.text || "").slice(0, 220).replace(/\s+\S*$/, "") + "…", h("span", { class: "muted" }, " Show all")), h("div", { class: "heard-text visit" }, hd.text))
                      : h("div", { class: "heard-text" + (visit ? " visit" : "") }, hd.text || "(nothing was heard)");
    const rd = hd.reading || {};
    const medNotes = [...(rd.meds || []).length ? [`Medications mentioned: ${rd.meds.join(", ")}`] : [], ...(rd.med_notes || [])];
    return card(scopeNote ? "Scope note" : (visit ? (hd.autoApplied != null ? "Visit assembled" : (hd.pasted ? "Visit transcript" : "Visit recording")) : "Heard"), [
      text,
      hd.autoApplied != null ? h("div", { class: "dictation-notes" }, h("span", { class: "tag ok" }, `${hd.autoApplied} fields filled automatically`)) : null,
      (hd.notes || []).length ? h("div", { class: "dictation-notes" }, hd.notes.map(n => h("span", { class: "tag" }, n))) : null,
      // a point the builder raises too is settled once, under Needs you
      (hd.unclear || []).some(t => !((S.lastBuild && S.lastBuild.check) || []).includes(t))
        ? h("div", { class: "build-check" }, unclearList(hd.unclear.filter(t => !((S.lastBuild && S.lastBuild.check) || []).includes(t)), { title: scopeNote ? "Needs review from Scope" : "Unclear in the conversation" })) : null,
      medNotes.length ? h("div", { class: "list", style: "margin-top:8px" }, medNotes.map(m => todoLine(m, "meds"))) : null,
    ], { tools });
  }
  function applyBuild(rows) {
    // boxes left unticked stay unticked the next time Build runs
    const rejected = S.rejected instanceof Set ? S.rejected : (S.rejected = new Set());
    for (const x of rows) for (const { it, cb } of x.items || []) { const key = x.pr.id + "|" + it.text; if (cb.checked) rejected.delete(key); else rejected.add(key); }
    const chosen = rows.filter(x => (x.items ? x.items.some(i => i.cb.checked) : x.use.checked));
    if (!chosen.length) { toast("Nothing chosen"); return; }
    checkpoint("build", true);
    let n = 0;
    for (const { pr, editor, items } of chosen) {
      let changes;
      if (items) { changes = {}; for (const { it, cb } of items) if (cb.checked) { Object.assign(changes, it.changes); n++; } }
      else { changes = { ...pr.changes }; if (pr.field && editor) changes[pr.field] = editor.value; n++; }
      if (parseDob(S.values.DOB)) delete changes["HPI-age"];
      for (const [k, v] of Object.entries(changes)) setValue(k, v, { silent: true });
      if (pr.field) S.generated = { ...S.generated, [pr.field]: pr.generated || [] };
    }
    onChange("build");
    updateStoryboard();
    toast(`Wrote ${n} change${n > 1 ? "s" : ""} into the note`);
    buildBelow();
  }
  function undoLast() {
    if (!S.undoStack || !S.undoStack.length) return;
    S.redoStack = Array.isArray(S.redoStack) ? S.redoStack : [];
    S.redoStack.push(historySnapshot());
    restoreHistory(S.undoStack.pop());
    S.historyGroup = null; syncHistory();
    refreshView(); updateStoryboard(); recode(); toast("Undone");
  }
  function redoLast() {
    if (!S.redoStack || !S.redoStack.length) return;
    S.undoStack = Array.isArray(S.undoStack) ? S.undoStack : [];
    S.undoStack.push(historySnapshot());
    restoreHistory(S.redoStack.pop());
    S.historyGroup = null; syncHistory();
    refreshView(); updateStoryboard(); recode(); toast("Redone");
  }

  // ------------------------------------------------------------------ Billing
  async function recode() {
    try {
      S.coding = await api.post("/api/coding", { values: S.values, payer: S.payer || "" });
    } catch (e) {
      S.coding = { error: e.message || "Coding isn't available", warnings: [], addons: [], procedures: [], icd: [] };
    }
    renderNav(); updateEmChip();
    if (S.tab === "billing") renderBilling();
    if (hooks.onUpdate) hooks.onUpdate();
  }
  function renderBilling() {
    const p = $("#panel"); p.innerHTML = "";
    const c = S.coding;
    if (!c || c.error || !Array.isArray(c.warnings)) { p.append(card("Billing", h("div", { class: "muted" }, c && c.error ? c.error : "Calculating…"))); return; }
    const sug = (s) => h("div", { class: "item" }, h("div", { class: "c" }, s.code + (s.modifier ? "-" + s.modifier : "")), h("div", {}, s.title, s.verify ? h("span", { class: "verify" }, "verify") : null, h("div", { class: "why" }, s.why)));
    const payer = h("input", { type: "text", class: "payer", placeholder: "Payer, e.g. Medicare", "aria-label": "Payer", value: S.payer || "" });
    payer.addEventListener("change", () => { S.payer = payer.value; recode(); });
    p.append(card("Office visit", [
      c.em ? h("div", { class: "code-line" }, h("div", { class: "code-big" }, c.em.code), c.em.modifier ? h("span", { class: "code-mod" }, "-" + c.em.modifier) : null, h("div", {}, h("div", {}, c.em.title), h("div", { class: "muted" }, c.em.why)))
           : h("div", { class: "warn todo-line" }, h("span", {}, "No visit level yet: enter the total time under Plan."), h("button", { class: "linkish", type: "button", onclick: () => goSection("plan") }, "Plan")),
      c.next_level ? h("div", { class: "muted", style: "margin-top:8px" }, c.next_level) : null,
      c.addons.length ? h("div", { class: "list", style: "margin-top:10px" }, c.addons.map(sug)) : null,
    ], { span: "half", tools: [payer] }));
    p.append(card(`Checks (${c.warnings.length})`, c.warnings.length ? h("div", { class: "list" }, c.warnings.map(w => h("div", { class: "warn" }, w))) : h("div", { class: "tag green" }, "Nothing missing"), { span: "half" }));
    p.append(card("Procedures", c.procedures.length ? h("div", { class: "list" }, c.procedures.map(sug)) : h("div", { class: "muted" }, "None. Separate-time lines become procedure codes here."), { span: "half" }));
    p.append(card("Diagnoses", c.icd.length ? h("div", { class: "list" }, c.icd.map(i => h("div", { class: "item" }, h("div", { class: "c" }, i.code || "—"), h("div", {}, i.impression, !i.code ? h("div", { class: "why" }, "No ICD-10 on file for this wording.") : null)))) : h("div", { class: "muted" }, "Numbered impressions appear here with their ICD-10."), { span: "half" }));
    p.append(h("div", { class: "fine", style: "grid-column:1/-1" }, "Suggestions follow the clinic playbook and the 2021 time rules. Have the coder confirm anything marked verify."));
    p.append(h("div", { class: "row", style: "grid-column:1/-1" }, h("button", { class: "btn primary", type: "button", onclick: () => showTab("superbill") }, "Create superbill from this note")));
  }
  // The provider superbill, started from this note: the shell's superbill screen drawn in this panel.
  function renderSuperbill() {
    const p = $("#panel"); p.innerHTML = "";
    const host = h("div", { class: "chart-host" });
    p.append(host);
    const mod = DDOS.modules && DDOS.modules.superbill;
    if (!mod) { host.append(h("div", { class: "muted" }, "The superbill screen isn't available.")); return; }
    mod.mount(host, { api, toast, noteValues: S.values, notePatient: S.patient, payer: S.payer || "" });
  }
  function updateEmChip() {
    const el = $("#emChip"), c = S.coding;
    if (!el || !c || !Array.isArray(c.warnings)) return;
    const addons = c.addons || [];
    if (c.em) { el.textContent = `${c.em.code}${c.em.modifier ? "-" + c.em.modifier : ""} · ${c.minutes} min` + (addons.length ? " + " + addons.map(a => a.code).join(", ") : ""); el.className = "em-chip"; }
    else { el.textContent = c.minutes ? `${c.minutes} min, below 99212` : "No visit time"; el.className = "em-chip warn"; }
    el.title = "Visit level from the total time. Opens Check.";
  }

  // ------------------------------------------------------------------ Preview
  async function renderPreview() {
    const p = $("#panel"); p.innerHTML = "";
    const pages = h("div", { class: "pages" }, h("div", { class: "loading" }, h("span", { class: "spinner" }), "Drawing the note as it will file"));
    const history = h("div", { class: "print-history" });
    p.append(card("The note as it will file", [pages, history], { tools: [
      h("button", { class: "tool print-note-tool", type: "button", onclick: printNote }, icon("print"), "Print"),
      h("button", { class: "tool", type: "button", onclick: renderPreview }, icon("refresh"), "Refresh")
    ] }));
    const r = await api.post("/api/preview", { values: S.values });
    pages.innerHTML = "";
    // drawn at the screen's real resolution, so the text is sharp on a high-density display
    const zoom = Math.min(3, Math.max(1.5, Math.min(920, pages.clientWidth || 920) * (window.devicePixelRatio || 1) / 612)).toFixed(2);
    for (let i = 0; i < r.pages; i++) pages.append(h("img", { src: `/api/preview?id=${r.id}&page=${i}&zoom=${zoom}`, alt: `Page ${i + 1}` }));
    renderPrintHistory(history);
  }

  async function renderPrintHistory(target = $(".print-history")) {
    if (!target) return;
    target.innerHTML = "";
    try {
      const q = new URLSearchParams({ name: S.values.Name || "", dos: S.values.Date || "" });
      const rows = await api.get("/api/print-log?" + q);
      target.append(h("div", { class: "print-history-head" }, h("b", {}, "Print history"), h("span", { class: "muted" }, rows.length ? "Latest attempts for this note" : "Nothing printed yet")));
      for (const row of rows.slice(0, 10)) target.append(h("div", { class: "print-history-row" },
        h("span", { class: "print-state " + (row.status === "printed" ? "ok" : row.status === "not_printed" ? "no" : "pending") }, row.status === "printed" ? "Printed" : row.status === "not_printed" ? "Did not print" : "Print opened"),
        h("span", {}, [row.when || "", row.printer ? `(${row.printer})` : ""].filter(Boolean).join(" · ")),
        h("span", { class: "muted" }, row.user || "")));
    } catch { target.append(h("div", { class: "muted" }, "Print history is unavailable.")); }
  }

  async function logPrint(attempt, status, printer = "") {
    return api.post("/api/print-log", { attempt, status, printer, name: S.values.Name || "", dos: S.values.Date || "", document: "progress note" });
  }
  async function printNote() {
    if (S.tab === "superbill") {
      const mod = DDOS.modules && DDOS.modules.superbill;
      if (mod && mod.print) return mod.print();
    }
    const buttons = [$("#printBtn"), ...document.querySelectorAll(".print-note-tool")].filter(Boolean);
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const prData = await api.get("/api/printers").catch(() => ({ printers: [], default: "" }));
      const printers = prData.printers || [];
      const defaultPrinter = prData.default || (printers[0]?.id || "");
      if (document.querySelector(".print-dialog")) document.querySelector(".print-dialog").remove();
      const modal = h("div", { class: "modal print-dialog", role: "dialog", "aria-modal": "true", "aria-label": "Print progress note" });
      const select = h("select", { class: "input", style: "width:100%;height:34px;margin-top:6px;border-radius:8px;border:1px solid var(--hair2);background:var(--field);color:var(--ink);padding:0 10px;font-size:13px;" });
      if (printers.length) {
        for (const p of printers) {
          const opt = h("option", { value: p.id }, p.name + (p.is_default ? " (Default)" : ""));
          if (p.id === defaultPrinter || (!defaultPrinter && p.is_default)) opt.selected = true;
          select.append(opt);
        }
      } else {
        select.append(h("option", { value: "" }, "Default printer"));
      }
      const copiesInput = h("input", { type: "number", min: "1", max: "20", value: "1", style: "width:70px;height:34px;border-radius:8px;border:1px solid var(--hair2);background:var(--field);padding:0 8px;font-size:13px;" });
      const sysDialogCheck = h("input", { type: "checkbox", id: "printSysDialog", style: "margin-right:6px;" });
      const close = () => modal.remove();
      const doPrint = async () => {
        const selectedPrinter = select.value;
        const copies = parseInt(copiesInput.value, 10) || 1;
        const useSysDialog = sysDialogCheck.checked;
        modal.remove();
        toast("Sending note to printer...");
        try {
          const res = await api.post("/api/print", {
            values: S.values,
            printer: selectedPrinter,
            copies,
            system_dialog: useSysDialog
          });
          if (res && res.error) throw new Error(res.error);
          toast(res.printer ? `Printed to ${res.printer}.` : "Print job sent successfully.");
          renderPrintHistory();
        } catch (err) {
          toast("Print failed: " + (err.message || err));
        }
      };
      modal.append(h("div", { class: "modal-box", style: "max-width:440px;" },
        h("h2", {}, "Print progress note"),
        h("p", { class: "muted", style: "margin:0 0 14px 0;font-size:13px;" },
          [S.values.Name || "New patient", S.values.Date ? `DOS ${S.values.Date}` : ""].filter(Boolean).join(" · ")),
        h("div", { style: "margin-bottom:14px;" },
          h("label", { style: "font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.04em;" }, "Select Printer"),
          select
        ),
        h("div", { style: "display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;" },
          h("label", { style: "font-size:13px;font-weight:550;" }, "Copies"),
          copiesInput
        ),
        h("div", { style: "display:flex;align-items:center;margin-bottom:18px;" },
          h("label", { style: "font-size:12.5px;color:var(--ink2);cursor:pointer;display:flex;align-items:center;" },
            sysDialogCheck,
            "Open system print dialog"
          )
        ),
        h("div", { class: "row modal-actions", style: "display:flex;gap:10px;justify-content:flex-end;" },
          h("button", { class: "btn", type: "button", onclick: close }, "Cancel"),
          h("button", { class: "btn primary", type: "button", onclick: doPrint }, icon("print"), "Print")
        )
      ));
      document.body.append(modal);
    } catch (e) {
      toast("Couldn't open print dialog: " + (e.message || e));
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  // ------------------------------------------------------------------ Storyboard
  function updateStoryboard() {
    const v = S.values, [last, first] = splitName(v.Name);
    const nameEl = $("#sbName");
    nameEl.textContent = v.Name ? `${last}${first ? ", " + first : ""}` : "New patient";
    nameEl.classList.toggle("empty", !v.Name);
    const dob = parseDob(v.DOB), dos = parseDate(v.Date);
    const sex = v["hpi-female"] ? "F" : (v["hpi-male"] ? "M" : "");
    const kind = visitKind();
    paintVisitBadge();
    const att = (v["Imp/MP"] || "").match(/I, ([^,]+),/);
    $("#sbIdent").textContent = [v.DOB ? `DOB ${v.DOB}` : "", dob ? `${ageOn(dob, dos || new Date())} y` : (v["HPI-age"] ? v["HPI-age"] + " y" : ""), sex,
      S.patient?.acct ? `Pt # ${S.patient.acct}` : "", v.Date ? `DOS ${v.Date}` : "", v["imp-mp"] && att ? `${att[1].trim()} present` : ""].filter(Boolean).join(" · ");
    const tags = $("#sbTags"); tags.innerHTML = "";
    const toMeds = () => showTab("meds");
    if (v.nkda) tags.append(h("button", { class: "tag green", type: "button", title: "Allergies (Meds)", onclick: toMeds }, "NKDA"));
    else if ((v.Allergies || "").trim()) tags.append(h("button", { class: "tag red", type: "button", title: "Allergies: " + v.Allergies.trim(), onclick: toMeds }, "Allergies"));
    if (v["ga-falls-yes"]) tags.append(h("span", { class: "tag red" }, "Falls"));
    if (v["hist-diab"]) tags.append(h("span", { class: "tag" }, "DM"));
    if (v["hist-hypt"]) tags.append(h("span", { class: "tag" }, "HTN"));
    if (v["hist-migraine"]) tags.append(h("span", { class: "tag" }, "Migraine"));
    if (v["hist-arrh"]) tags.append(h("span", { class: "tag orange" }, "Arrhythmia"));
    document.title = v.Name ? `${last}, ${first} — DizzyDoctor` : "DizzyDoctor";
    if (current && hooks.onTab) hooks.onTab(current, { label: v.Name ? `${last}${first ? ", " + first : ""}` : "New note", dirty: S.dirty, patient: S.patient,
                                                      visit: kind.type ? { id: kind.id, label: kind.type.label, color: kind.type.color } : null });
  }
  function renderNotesList() {
    const el = $("#sbNotes"); el.innerHTML = "";
    const notes = S.patientNotes || [];
    if (!notes.length) { el.append(h("div", { class: "muted" }, S.patient ? "None yet" : "Choose a patient")); return; }
    const latest = notes.find(n => n.kind === "final") || notes[0];
    el.append(h("button", { class: "new-visit", type: "button", title: "Start today's note from " + latest.name, onclick: () => newFrom(latest.path) }, icon("plus"), "New visit from last note"));
    for (const n of notes) el.append(h("button", { type: "button", title: (n.kind === "draft" ? "Continue draft " : "View signed note read-only ") + n.name,
      onclick: () => n.kind === "draft" ? loadNote(n.path) : viewHistoricalNote(n.path) },
      h("span", {}, noteDate(n)), h("small", {}, n.kind === "draft" ? "draft" : "filed")));
  }

  // "SampleAlice 08 15 26.pdf" -> "08/15/26"; anything else as named.
  function noteDate(n) { const m = /(\d{2}) (\d{2}) (\d{2,4})\.pdf$/i.exec(n.name || ""); return m ? `${m[1]}/${m[2]}/${m[3]}` : (n.name || "").replace(/\.pdf$/i, ""); }

  // ------------------------------------------------------------------ patients & notes
  async function searchPatients(q) {
    const box = $("#patientResults");
    if (!q.trim()) { box.hidden = true; return; }
    const rows = await api.get("/api/patients?q=" + encodeURIComponent(q));
    box.innerHTML = "";
    if (!rows.length) box.append(h("button", { disabled: true }, "No matches in the patient directory"));
    for (const r of rows) box.append(h("button", { onclick: () => pickPatient(r) }, h("span", {}, `${r.last}, ${r.first}`), h("small", {}, [r.dob, r.acct ? "Pt # " + r.acct : "", r.last_visit ? "last " + r.last_visit : ""].filter(Boolean).join(" · "))));
    box.hidden = false;
  }
  async function pickPatient(r) {
    S.patient = r; S.generated = {}; $("#patientResults").hidden = true; $("#patientSearch").value = `${r.last}, ${r.first}`;
    S.patientNotes = await api.get("/api/notes?" + new URLSearchParams({ last: r.last, first: r.first, id: r.id || "" }));
    renderNotesList();
    const latest = (S.patientNotes || []).find(n => n.kind === "final");
    if (latest) await newFrom(latest.path);
    else {
      // a patient with no note on file starts from the blank form (its own defaults), not from nothing
      if (!("ChiefComplaint-descr" in S.values)) { S.values = (await api.get("/api/note")).values; S.generated = {}; clearHistory(); }
      setValue("Name", `${r.last}, ${r.first}`); setValue("DOB", r.dob || ""); if (!S.values.Date) setValue("Date", todayStr()); recomputeAge(); denyByDefault();
      applyScheduleKind();
      refreshView(); resetClock();
    }
    S.dirty = false;                    // filling in who the patient is isn't an edit
    clearHistory();                     // Ctrl+Z starts with the provider's first real edit, not patient setup
    status(latest ? `Started from ${latest.name}` : "New chart");
  }
  // A new note starts with every symptom as "Patient denies"; marking one present (or the story / dictation saying so)
  // opens its details. Denials made this way are remembered as defaults, so the story can still mark one present.
  // The complaint itself isn't defaulted to "denies": a symptom named in the chief complaint or the last visit's
  // impressions is left for the provider to answer.
  const COMPLAINT_WORDS = { dizziness: /dizz|vertig|bppv|vestibul|light-?headed|meniere/i, hearing: /hearing|snhl|deaf/i, tinnitus: /tinnitus/i,
                            gait: /gait|imbalance|ataxi|balance|unsteady|fall/i, headache: /headache|migrain|\bha\b/i };
  function denyByDefault() {
    S.defaulted = new Set();
    const complaint = [S.values.ChiefComplaint, S.values["impression-plan-descr"], ...((S.prep && S.prep.impressions) || []).map(i => i.dx)].join(" ");
    for (const sym of SYMPTOMS) {
      if (S.values[sym.on] || S.values[sym.deny] || (COMPLAINT_WORDS[sym.id] && COMPLAINT_WORDS[sym.id].test(complaint))) continue;
      S.values[sym.deny] = true; S.defaulted.add(sym.deny);
    }
  }
  // What Build and the visit reader see: a default "denies" isn't the provider's answer, so it isn't written into the
  // HPI as a denial and doesn't stand in the way of the story marking the symptom present. The form keeps the box.
  function composeValues() {
    if (!(S.defaulted instanceof Set) || !S.defaulted.size) return S.values;
    const v = { ...S.values };
    for (const n of S.defaulted) if (v[n] === true) v[n] = false;
    return v;
  }
  async function newFrom(path) {
    const r = await api.post("/api/new-from", { path, dos: todayStr(), Name: S.patient ? `${S.patient.last}, ${S.patient.first}` : "", DOB: S.patient?.dob || "" });
    S.values = r.values; S.path = ""; S.dirty = false; S.generated = {}; clearHistory();
    S.prep = r.prep || null; S.prepDone = new Set();
    denyByDefault();
    applyScheduleKind();                 // the booking's visit type (a results visit, a procedure) over the carried-forward Progress note
    S.at = "patient"; showTab("build"); updateStoryboard(); recode(); resetClock();
    const open = (S.prep?.followups || []).length;
    toast(open ? `Started from the last visit. ${open} follow-up${open > 1 ? "s" : ""} flagged above.` : "Started from the last visit's note.");
    S.prepStudies = "pending";           // reports filed since then go into the study rows once the chart's documents are here
    prefillStudies(S.prep?.date || "");
  }
  async function loadNote(path) {
    const r = await api.get("/api/note?path=" + encodeURIComponent(path));
    if (r.read_only) { viewHistoricalNote(r.path || path); return; }
    S.values = r.values; S.path = r.path; S.dirty = false; S.generated = {}; clearHistory();
    S.prep = null; S.prepDone = new Set();
    S.at = "patient"; showTab("build"); updateStoryboard(); recode();
    status("Open: " + path.split(/[\\/]/).pop());
  }
  function viewHistoricalNote(path) {
    showTab("chart:review");
    requestAnimationFrame(() => {
      const review = DDOS.modules && DDOS.modules.review;
      if (review && review.open) review.open(path);
    });
    status("Read-only filed note: " + path.split(/[\\/]/).pop());
  }
  // Age follows the date of birth and the date of service. A two-digit birth year is last century when it has to be.
  function parseDob(s) { const d = window.DDOS && DDOS.dates ? DDOS.dates.parse(s, { dob: true }) : null; return d ? parseDate(d) : null; }
  function recomputeAge(cleared = false) {
    const dob = parseDob(S.values.DOB), dos = parseDate(S.values.Date) || new Date();
    if (dob) { const a = ageOn(dob, dos); if (a >= 0 && String(a) !== String(S.values["HPI-age"] || "")) setValue("HPI-age", String(a), { silent: true }); }
    else if (cleared) setValue("HPI-age", "", { silent: true });
  }
  let medsView = null;
  function meds() {
    return medsView || (medsView = NoteMeds({ h, icon, card, check, textField, textArea, S, setValue, toast, parseDate, fmtDate, active: () => S.tab === "note" }));
  }
  function medsReviewed() {
    const r = NoteMeds.parse(S.values["meds-descr"] || "").find(x => x.kind === "review" && x.date);
    const d = r && parseDate(r.date);
    return !!d && fmtDate(d) === fmtDate(parseDate(S.values.Date) || new Date());
  }

  async function save(kind) {
    const btn = kind === "final" ? $("#fileNote") : $("#saveDraft");
    btn.disabled = true;
    try {
      const r = await api.post("/api/save", { values: S.values, kind });
      if (r.error) { toast(r.error); return; }
      S.path = r.path; S.dirty = false;
      status(`${kind === "final" ? "Filed" : "Draft saved"} ${r.saved} · ${r.name}`);
      if (kind === "final") forgetDraft(current);
      toast(`${kind === "final" ? "Note filed" : "Draft saved"}: ${r.name}`);
      updateStoryboard();                 // the tab loses its unsaved dot
      // the filed or drafted note shows up under Notes on file straight away
      if (S.patient && (S.patient.last || S.patient.first || S.patient.id)) {
        const who = current, q = new URLSearchParams({ last: S.patient.last || "", first: S.patient.first || "", id: S.patient.id || "" });
        api.get("/api/notes?" + q).then(list => { if (current === who && Array.isArray(list)) { S.patientNotes = list; renderNotesList(); } }).catch(() => {});
      }
    } finally { btn.disabled = false; }
  }

  // ------------------------------------------------------------------ clock
  // The visit timer shows only when Settings turns it on.
  function paintTimer() { const el = $("#noteClock"); if (el) el.hidden = !S.status.visit_timer; }
  function tickClock() {
    if (!S.status.visit_timer) return;
    const el = $("#clock"); if (!el) return;
    const c = S.clock; const ms = (c.pausedAt ? c.pausedAt : Date.now()) - c.start - c.paused;
    const m = Math.floor(ms / 60000), s = Math.floor(ms / 1000) % 60;
    el.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  function paintClockToggle() { const b = $("#clockToggle"); if (!b) return; const paused = !!S.clock.pausedAt; b.innerHTML = ""; b.append(icon(paused ? "play" : "pause")); b.title = paused ? "Resume the visit clock" : "Pause the visit clock"; b.setAttribute("aria-label", b.title); }
  function resetClock() { S.clock = { start: Date.now(), paused: 0, pausedAt: 0 }; paintClockToggle(); }
  function useClock() { const c = S.clock; const ms = (c.pausedAt || Date.now()) - c.start - c.paused; setValue("appt-time", String(Math.max(1, Math.round(ms / 60000)))); toast("Total time set from the clock"); }

  // ------------------------------------------------------------------ dictation
  // Recording goes straight to 16 kHz PCM WAV, so the PC needs no media decoder to transcribe it.
  let rec = null;
  function encodeWav(chunks, rate) {
    let n = 0; for (const c of chunks) n += c.length;
    let pcm = new Float32Array(n); let o = 0; for (const c of chunks) { pcm.set(c, o); o += c.length; }
    if (rate !== 16000) { const m = Math.round(n * 16000 / rate), out = new Float32Array(m); for (let i = 0; i < m; i++) { const x = i * rate / 16000, j = Math.floor(x), t = x - j; out[i] = pcm[j] * (1 - t) + (pcm[Math.min(j + 1, n - 1)] || 0) * t; } pcm = out; rate = 16000; }
    const buf = new ArrayBuffer(44 + pcm.length * 2), v = new DataView(buf);
    const str = (p, s) => { for (let i = 0; i < s.length; i++) v.setUint8(p + i, s.charCodeAt(i)); };
    str(0, "RIFF"); v.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVE"); str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) { const s = Math.max(-1, Math.min(1, pcm[i])); v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
    return new Blob([buf], { type: "audio/wav" });
  }
  async function toggleMic() {
    const btn = $("#micBtn");
    if (rec) { rec.stop(); return; }
    if (!S.status.dictation) { toast("Dictation isn't installed on this computer (faster-whisper)."); return; }
    // where it goes and whose note it is are fixed when dictation starts, not when it finishes
    const target = dictationTarget(), owner = current;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
      const src = ctx.createMediaStreamSource(stream), proc = ctx.createScriptProcessor(4096, 1, 1), chunks = [];
      proc.onaudioprocess = e => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      src.connect(proc); proc.connect(ctx.destination);
      rec = { stop: async () => {
        rec = null; proc.disconnect(); src.disconnect(); stream.getTracks().forEach(t => t.stop()); const rate = ctx.sampleRate; ctx.close();
        transcribing++;
        btn.classList.remove("on"); btn.classList.add("busy"); $("#micLabel").textContent = "Transcribing";
        const blob = encodeWav(chunks, rate);
        const q = new URLSearchParams(target);
        try {
          const r = await fetch("/api/dictate?" + q, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "audio/wav" }, body: blob }).then(x => x.json());
          if (r.error) { toast(r.error); showDictation("Couldn't transcribe", r.error, [], false); }
          else if (current !== owner) toast("That dictation was for another tab; it wasn't added here.");
          else { S.section = target.section; applyInterpretation(r.text, r.interpretation); }
        } finally { transcribing--; btn.classList.remove("busy"); $("#micLabel").textContent = "Dictate"; }
      } };
      btn.classList.add("on"); $("#micLabel").textContent = "Stop";
      showDictation("Listening", "", [], false, true);
    } catch (e) { toast("Microphone not available: " + e.message); }
  }
  // The whole visit, provider and patient: sent in 30-second parts as it goes (transcribed on this computer meanwhile),
  // then read and opened in Build. Starts only after the provider confirms the patient agreed to be recorded.
  let visitRec = null;
  let transcribing = 0;                 // dictations sent and not yet back: the tab can't change under them
  function pasteTranscript() {
    S.paste = { text: (S.paste && S.paste.text) || "", kind: "visit" };
    showTab("build");
    requestAnimationFrame(() => { const ta = $("#panel .paste-text"); if (ta) ta.focus(); });
  }
  function askVisitConsent() {
    if (rec) { toast("Stop dictating first."); return; }
    if (visitRec) { stopVisit(); return; }
    if (!S.status.dictation) { pasteTranscript(); toast("Recording needs dictation on this computer. You can paste a transcript here instead."); return; }
    $("#visitConsent").hidden = false;
    $("#visitStart").focus();
  }
  async function startVisit() {
    $("#visitConsent").hidden = true;
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } }); }
    catch (e) { toast("Microphone not available: " + e.message); return; }
    const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const src = ctx.createMediaStreamSource(stream), proc = ctx.createScriptProcessor(4096, 1, 1);
    const id = "v" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    visitRec = { id, ctx, src, proc, stream, chunks: [], seq: 0, sent: 0, start: Date.now(), owner: current, timer: null,
                 storyBefore: String(S.values["ChiefComplaint-descr"] || ""), dirtyBefore: !!S.dirty };
    proc.onaudioprocess = e => visitRec && visitRec.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    src.connect(proc); proc.connect(ctx.destination);
    const flush = () => {
      if (!visitRec || !visitRec.chunks.length) return Promise.resolve();
      const blob = encodeWav(visitRec.chunks, ctx.sampleRate), seq = visitRec.seq++;
      visitRec.chunks = [];
      return fetch(`/api/visit/part?id=${id}&seq=${seq}`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "audio/wav" }, body: blob })
        .then(r => r.json()).then(() => { if (visitRec) visitRec.sent++; }).catch(() => toast("A part of the recording didn't reach the transcriber."));
    };
    visitRec.flush = flush;
    visitRec.timer = setInterval(() => { flush(); }, 30000);
    visitRec.tick = setInterval(paintVisit, 1000);
    paintVisit();
    status("Recording the visit");
  }
  function paintVisit() {
    const b = $("#visitBtn"), label = $("#visitLabel");
    if (!visitRec) { b.classList.remove("on", "busy"); label.textContent = "Record visit"; return; }
    const s = Math.floor((Date.now() - visitRec.start) / 1000);
    b.classList.add("on");
    label.textContent = `Stop  ${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    showDictation("Recording the visit", "Speak normally. Press Stop when you're done and the progress note will assemble automatically.", [], false, true);
  }
  async function stopVisit() {
    const v = visitRec; if (!v) return;
    clearInterval(v.timer); clearInterval(v.tick);
    v.proc.disconnect(); v.src.disconnect(); v.stream.getTracks().forEach(t => t.stop());
    await v.flush();
    v.ctx.close();
    visitRec = null;
    const b = $("#visitBtn"); b.classList.remove("on"); b.classList.add("busy"); $("#visitLabel").textContent = "Assembling";
    showDictation("Assembling the visit", "Finishing the transcript, selecting fields and writing the progress note.", [], false, true);
    try {
      const r = await api.post("/api/visit/finish", { id: v.id, values: composeValues(), defaults: [...(S.defaulted || [])] });
      if (r.error) { toast(r.error); return; }
      if (current !== v.owner) { toast("The visit recording finished on another tab."); return; }
      const rd = r.reading || {};
      const result = await assembleRecordedVisit(r.transcript || "", rd, v);
      $("#dictation").hidden = true;
      status(`Visit assembled · ${result.applied} field${result.applied === 1 ? "" : "s"} filled${result.review ? ` · ${result.review} to review` : ""}`);
      toast(result.review ? `Progress note assembled. ${result.review} item${result.review === 1 ? "" : "s"} still need review.` : "Progress note assembled from the recording.");
      showTab("build");
    } catch (e) { toast("Transcription failed: " + (e.message || e)); }
    finally { b.classList.remove("busy"); $("#visitLabel").textContent = "Record visit"; }
  }
  async function assembleRecordedVisit(text, rd, recording) {
    checkpoint("recorded visit", true);
    let applied = 0;
    const put = (changes) => {
      changes = { ...(changes || {}) };
      if (parseDob(S.values.DOB)) delete changes["HPI-age"];
      for (const [name, value] of Object.entries(changes)) {
        if (!same(S.values[name], value)) applied++;
        setValue(name, value, { silent: true });
      }
    };
    for (const pr of rd.proposals || []) {
      if (pr.requires_confirmation) continue;
      if ((pr.items || []).length) for (const item of pr.items) put(item.changes);
      else put(pr.changes);
    }
    // The conversation becomes the builder's source, never a verbatim transcript in the filed note. Preserve anything
    // the provider had already typed (or typed while recording) and add the visit statements beneath it.
    const story = String(rd.story || "").trim(), now = String(S.values["ChiefComplaint-descr"] || "").trim();
    const preserve = recording.dirtyBefore || String(S.values["ChiefComplaint-descr"] || "") !== recording.storyBefore;
    if (story) {
      const value = preserve && now ? `${now}\n\n${story}` : story;
      if (!same(S.values["ChiefComplaint-descr"], value)) applied++;
      setValue("ChiefComplaint-descr", value, { silent: true });
    }
    // `story` is the higher-fidelity patient narrative. The dictation reader may also offer a shorter HPI; adding
    // both would repeat the visit. Its non-HPI additions (plan, treatment, medication details) remain useful.
    for (const [name, value] of Object.entries(rd.append || {})) if (name !== "ChiefComplaint-descr") insertText(name, value, "line", { silent: true });
    for (const name of rd.imaging || []) placeImaging(name, { quiet: true, silent: true });
    for (const study of rd.studies || []) placeStudy(study, { silent: true });

    let built = { proposals: [], todo: [], check: [] };
    try { built = await api.post("/api/compose", { values: composeValues(), previous: S.generated, last_seen: lastSeen(), defaults: [...(S.defaulted || [])] }); }
    catch { /* the boxes and orders above are still useful; Build will retry composition */ }
    for (const pr of built.proposals || []) {
      if (pr.requires_confirmation) continue;
      if ((pr.items || []).length) for (const item of pr.items) put(item.changes);
      else put(pr.changes);
      if (pr.field) S.generated = { ...S.generated, [pr.field]: pr.generated || [] };
    }
    const unclear = [...new Set([...(rd.unclear || []), ...(rd.todo || []), ...(built.check || []), ...(built.todo || [])])];
    S.lastBuild = { check: built.check || [], todo: built.todo || [] };
    S.heard = { mode: "visit", autoApplied: applied, text, reading: { ...rd, proposals: [] }, notes: rd.notes || [], unclear };
    onChange("recorded visit"); updateStoryboard(); recode();
    return { applied, review: unclear.length };
  }
  function showDictation(title, text, notes, done, listening = false) {
    const d = $("#dictation"); d.hidden = false; d.classList.toggle("done", !!done);
    $("#dictationTitle").textContent = title; $("#dictationText").textContent = text;
    const n = $("#dictationNotes"); n.innerHTML = ""; for (const x of notes) n.append(h("span", { class: "tag" }, x));
    $("#dictationUndo").hidden = !done;
  }
  function applyInterpretation(text, it) {
    checkpoint("dictation", true);
    if (parseDob(S.values.DOB) && it.set) delete it.set["HPI-age"];
    if (it.section && it.section !== S.section) S.section = it.section;
    for (const [k, v] of Object.entries(it.set || {})) setValue(k, v, { silent: true });
    for (const [k, v] of Object.entries(it.append || {})) insertText(k, v, "line", { silent: true });
    // Imaging ordered goes on the order lines, reports reviewed into the study rows; neither is lost when those are full.
    const placed = { plan: 0, more: 0 };
    for (const name of it.imaging || []) if (placeImaging(name, { quiet: true, silent: true }) === "plan") placed.plan++;
    for (const s of it.studies || []) if (placeStudy(s, { silent: true }) === "more") placed.more++;
    if (placed.plan) toast(`${placed.plan} imaging order${placed.plan > 1 ? "s" : ""} went to the plan notes: both order lines were in use.`);
    else if (placed.more) toast(`${placed.more} stud${placed.more > 1 ? "ies" : "y"} went to History / imaging continued: all five rows are in use.`);
    onChange("dictation");
    S.heard = { mode: "dictation", text, notes: it.notes || [] };
    $("#dictation").hidden = true;
    showTab("build");
  }
  // ------------------------------------------------------------------ misc
  let toastTimer;
  function toast(msg) { const t = $("#toast"); if (!t) return; t.textContent = msg; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3200); }
  function parseDate(s) { const m = /(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})/.exec(s || ""); if (!m) return null; let y = +m[3]; if (y < 100) y += 2000; const d = new Date(y, +m[1] - 1, +m[2]); return isNaN(d) ? null : d; }
  function todayStr() { return fmtDate(new Date()); }
  function fmtDate(d) { return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}/${d.getFullYear()}`; }
  function ageOn(dob, when) { let a = when.getFullYear() - dob.getFullYear(); if (when.getMonth() < dob.getMonth() || (when.getMonth() === dob.getMonth() && when.getDate() < dob.getDate())) a--; return a; }
  function splitName(name) { name = (name || "").trim(); if (!name) return ["", ""]; if (name.includes(",")) { const [l, f] = name.split(","); return [l.trim(), f.trim()]; } const parts = name.split(/\s+/); return parts.length > 1 ? [parts.at(-1), parts.slice(0, -1).join(" ")] : [name, ""]; }

  const NOTE_HTML = `
<div class="note-app">
  <header class="note-head">
    <div class="note-patient">
      <div class="np-top"><div class="np-name empty" id="sbName">New patient</div><button class="visit-badge" id="sbVisit" type="button" hidden></button><span class="np-tags" id="sbTags"></span></div>
      <div class="np-meta"><span id="sbIdent"></span></div>
    </div>
    <div class="note-clock" id="noteClock" hidden>
      <span class="timer-clock" id="clock" title="Visit clock">00:00</span>
      <button class="icon-btn" id="clockToggle" type="button"></button>
      <button class="tool" id="clockUse" type="button" title="Put the timer's minutes in Total time">Use time</button>
    </div>
    <div class="note-actions">
      <button id="micBtn" class="btn mic" type="button" title="Dictate (F9)"><span class="mic-dot"></span><span id="micLabel" class="btn-text">Dictate</span></button>
      <button id="visitBtn" class="btn visit" type="button" title="Record the whole visit (provider and patient)">${DDOS.icon("wave")}<span id="visitLabel" class="btn-text">Record visit</span></button>
      <span class="act-sep" aria-hidden="true"></span>
      <button id="superbillBtn" class="btn" type="button" title="The superbill for this visit">${DDOS.icon("list")}<span class="btn-text">Superbill</span></button>
      <button id="previewBtn" class="btn" type="button" title="The note as it will file">${DDOS.icon("file")}<span class="btn-text">Preview PDF</span></button>
      <button id="printBtn" class="btn" type="button" title="Print this note and record the outcome">${DDOS.icon("print")}<span class="btn-text">Print</span></button>
      <button id="undoBtn" class="btn history-btn" type="button" title="Undo the last change (Ctrl+Z)" aria-label="Undo" disabled>${DDOS.icon("undo")}</button>
      <button id="redoBtn" class="btn history-btn" type="button" title="Redo the last undone change (Ctrl+Shift+Z or Ctrl+Y)" aria-label="Redo" disabled>${DDOS.icon("redo")}</button>
      <button id="saveDraft" class="btn" type="button">Save draft</button>
      <button id="fileNote" class="btn primary" type="button">File note</button>
    </div>
    <div class="patient-search" hidden>
      <input id="patientSearch" type="search" autocomplete="off" spellcheck="false" aria-hidden="true" tabindex="-1">
      <div id="patientResults" class="results" hidden></div>
    </div>
  </header>
  <div class="note-body">
    <nav class="steps" id="tabs" aria-label="The note, page by page, and the chart">
      <div id="pageList" class="step-list"></div>
      <div class="steps-section">
        <div class="steps-title">Chart</div>
        <div id="chartList" class="step-list"></div>
      </div>
      <div class="steps-section">
        <div class="steps-title">Notes on file</div>
        <div id="sbNotes" class="sb-notes"><div class="muted">Choose a patient</div></div>
      </div>
    </nav>
    <main class="workspace">
      <nav id="viewSwitch" class="view-tabs" aria-label="Note, Build, Check, Superbill or PDF"></nav>
      <div class="dictation" id="dictation" hidden>
        <div class="dictation-head"><span class="dictation-dot"></span><span id="dictationTitle">Listening</span><button class="tool" id="dictationClose" type="button" aria-label="Close">${DDOS.icon("x")}</button></div>
        <div class="dictation-text" id="dictationText"></div>
        <div class="dictation-notes" id="dictationNotes"></div>
        <div class="dictation-actions"><button class="tool" id="dictationUndo" type="button">${DDOS.icon("undo")}Undo</button></div>
      </div>
      <section class="panel" id="panel"></section>
    </main>
  </div>
  <div id="toast" class="toast" hidden></div>
  <div id="phrasePop" class="pop" hidden></div>
  <div class="modal" id="visitConsent" hidden role="dialog" aria-modal="true" aria-labelledby="visitConsentTitle">
    <div class="modal-box">
      <h2 id="visitConsentTitle">Record this visit?</h2>
      <p>Ask the patient first: recording a conversation needs everyone's consent. The audio stays on this computer and each part is deleted once it is transcribed.</p>
      <div class="row modal-actions"><button class="btn quiet" type="button" id="visitPaste" title="Paste a transcript made somewhere else, or a test transcript">${DDOS.icon("paste")}Paste a transcript instead</button><span class="spacer"></span><button class="btn" type="button" id="visitCancel">Cancel</button><button class="btn primary" type="button" id="visitStart">The patient agreed. Start recording</button></div>
    </div>
  </div>
</div>`;

  let host = null;
  let booted = false;
  let booting = null;
  let docBound = false;

  function bindControls() {
    $("#clockToggle").addEventListener("click", () => { const c = S.clock; if (c.pausedAt) { c.paused += Date.now() - c.pausedAt; c.pausedAt = 0; } else c.pausedAt = Date.now(); paintClockToggle(); });
    paintClockToggle();
    $("#clockUse").addEventListener("click", useClock);
    $("#dictationUndo").addEventListener("click", undoLast);
    $("#dictationClose").addEventListener("click", () => { $("#dictation").hidden = true; });
    $("#micBtn").addEventListener("click", toggleMic);
    $("#visitBtn").addEventListener("click", askVisitConsent);
    $("#visitStart").addEventListener("click", startVisit);
    $("#visitCancel").addEventListener("click", () => { $("#visitConsent").hidden = true; });
    $("#visitPaste").addEventListener("click", () => { $("#visitConsent").hidden = true; pasteTranscript(); });
    $("#visitConsent").addEventListener("keydown", e => { if (e.key === "Escape") $("#visitConsent").hidden = true; });
    $("#sbVisit").addEventListener("click", () => goSection("patient"));
    $("#superbillBtn").addEventListener("click", () => { if (S.tab !== "superbill") showTab("superbill"); });
    $("#previewBtn").addEventListener("click", () => showTab("preview"));
    $("#printBtn").addEventListener("click", printNote);
    $("#undoBtn").addEventListener("click", undoLast);
    $("#redoBtn").addEventListener("click", redoLast);
    $("#saveDraft").addEventListener("click", () => save("draft"));
    $("#fileNote").addEventListener("click", () => { if (confirm("File this note to the Progress Notes folder?")) save("final"); });
    $("#panel").addEventListener("scroll", () => {
      if (DDOS.dates) DDOS.dates.close();
      const pop = $("#phrasePop"); if (pop && !pop.hidden) pop.hidden = true;
      requestAnimationFrame(spyScroll);
    }, { passive: true });
    $("#panel").addEventListener("focusin", (e) => { const sec = e.target.closest && e.target.closest(".note-sec"); if (sec && S.tab === "note" && sec.dataset.sec !== S.at) markActive(sec.dataset.sec); });
    let st; $("#patientSearch").addEventListener("input", e => { clearTimeout(st); st = setTimeout(() => searchPatients(e.target.value), 180); });
    $("#patientSearch").addEventListener("keydown", e => { if (e.key === "Escape") $("#patientResults").hidden = true; });
    if (!docBound) {
      docBound = true;
      document.addEventListener("keydown", e => {
        if (e.key === "F9" || (e.altKey && e.code === "Space")) { e.preventDefault(); toggleMic(); return; }
        if (!(e.ctrlKey || e.metaKey) || !scope.querySelector || !scope.querySelector(".note-app")) return;
        if (e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redoLast() : undoLast(); }
        else if (e.key.toLowerCase() === "y") { e.preventDefault(); redoLast(); }
      });
      window.addEventListener("beforeunload", e => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
      setInterval(tickClock, 1000);
    }
  }

  function patientKey(p) {
    if (!p) return "";
    return [p.id || "", p.last || "", p.first || "", p.path || ""].join("|");
  }

  let chartHooked = false;
  function hookChart() {
    if (chartHooked || !DDOS.on) return;
    chartHooked = true;
    DDOS.on("chart:loaded", () => {
      if (S.tab.startsWith("chart:")) showTab(S.tab); else refreshOnFile();
      if (S.prepStudies === "pending") prefillStudies(S.prep?.date || "");   // the chart's reports arrived after the note was prepped
    });
  }

  // ------------------------------------------------------------------ one note per tab
  // Each open patient tab has its own note: these are the parts of S that belong to one note. Switching tabs puts
  // the current note away and brings the other one back exactly as it was.
  const NOTE_KEYS = ["values", "path", "patient", "tab", "coding", "undo", "undoStack", "redoStack", "historyGroup", "generated", "dirty", "clock", "patientNotes", "payer", "section", "focus", "statusText", "heard", "paste", "prep", "prepDone", "at", "scroll", "rejected", "defaulted", "reviewed", "sensoryMap", "lastBuild"];
  const notes = new Map();
  let current = "";
  const DRAFTS = "ddos-tabs";
  function status(text) {
    S.statusText = text || "";
    const el = document.getElementById("noteStatus");
    if (el) el.textContent = [S.statusText, S.status.dictation ? "Dictation ready (F9)" : "Dictation not installed"].filter(Boolean).join("  ·  ");
  }
  function freshNote() {
    return { values: {}, path: "", patient: null, tab: "build", coding: null, undo: null, undoStack: [], redoStack: [], historyGroup: null, generated: {}, dirty: false,
             clock: { start: Date.now(), paused: 0, pausedAt: 0 }, patientNotes: [], payer: "", section: "hpi", focus: "", statusText: "", heard: null,
             paste: { text: "", kind: "auto" }, prep: null, prepDone: new Set(), at: "patient", scroll: 0, rejected: new Set(), defaulted: new Set(), reviewed: new Set(), sensoryMap: {}, lastBuild: null };
  }
  function putAway() {
    closeReport();                                   // a report never stays open over another patient's note
    if (!current) return;
    const p = $("#panel");
    if (p && S.tab === "note") S.scroll = p.scrollTop;
    notes.set(current, Object.fromEntries(NOTE_KEYS.map(k => [k, S[k] instanceof Set ? new Set(S[k]) : S[k]])));
  }
  function repaint() {
    if (S.tab === "note" || isSection(OLD_STEPS[S.tab] || S.tab)) renderNote(null, { scrollTop: S.scroll || 0 }); else showTab(S.tab);
    updateStoryboard(); renderNotesList(); recode(); paintClockToggle(); paintTimer(); status(S.statusText); syncHistory();
    const d = $("#dictation"); if (d) d.hidden = true;
  }
  function readDrafts() { try { return JSON.parse(localStorage.getItem(DRAFTS) || "{}") || {}; } catch { return {}; } }
  function writeDrafts(all) { try { localStorage.setItem(DRAFTS, JSON.stringify(all)); } catch { /* the copy on this computer is a convenience */ } }
  function keepDraft() {
    if (!current || !S.dirty) return;
    const all = readDrafts(), [last, first] = splitName(S.values.Name);
    all[current] = { label: S.values.Name ? `${last}${first ? ", " + first : ""}` : "New note", values: S.values, path: S.path, generated: S.generated, patient: S.patient, defaulted: [...(S.defaulted || [])], t: Date.now() };
    writeDrafts(all);
  }
  function forgetDraft(key) { const all = readDrafts(); if (key in all) { delete all[key]; writeDrafts(all); } }
  // Notes left open with unsaved changes in the last 12 hours come back as tabs.
  function savedTabs() {
    const all = readDrafts(), out = [];
    for (const [key, d] of Object.entries(all)) {
      if (!d || !d.values || Date.now() - (d.t || 0) > 12 * 3600e3) { delete all[key]; continue; }
      notes.set(key, { ...freshNote(), values: d.values, path: d.path || "", generated: d.generated || {}, patient: d.patient || null, defaulted: new Set(d.defaulted || []), dirty: true, statusText: "Restored unsaved changes" });
      out.push({ key, label: d.label || "New note", patient: d.patient || null, dirty: true });
    }
    writeDrafts(all);
    return out;
  }

  async function activate(key, patient) {
    await ready();
    if (key === current && !(patient && patient.path)) return;
    const normalizePrep = () => { if (!(S.prepDone instanceof Set)) S.prepDone = new Set(S.prepDone || []); };
    if (key !== current) {
      putAway();
      current = key;
      if (notes.has(key)) { Object.assign(S, notes.get(key)); normalizePrep(); }
      else {
        Object.assign(S, freshNote());
        const pnl = $("#panel"); if (pnl) { pnl.innerHTML = ""; pnl.dataset.mode = ""; pnl.scrollTop = 0; }
        if (patient && patient.path) { await loadNote(patient.path); S.patient = patient.last || patient.first || patient.id ? patient : null; }
        else if (patient && (patient.last || patient.first || patient.id)) await pickPatient(patient);
        else { S.values = (await api.get("/api/note")).values; S.values.Date = todayStr(); denyByDefault(); status("New note"); }
        putAway();
        repaint();
        return;
      }
    }
    if (patient && patient.path && patient.path !== S.path) { await loadNote(patient.path); putAway(); }
    repaint();
    if (S.patient && !(S.patientNotes || []).length) {        // a tab brought back from last time: its filed notes
      const who = current, q = new URLSearchParams({ last: S.patient.last || "", first: S.patient.first || "", id: S.patient.id || "" });
      api.get("/api/notes?" + q).then(r => { if (current === who && Array.isArray(r)) { S.patientNotes = r; renderNotesList(); } }).catch(() => {});
    }
  }
  function close(key) {
    const note = key === current ? S : notes.get(key);
    if (note && note.dirty && !confirm(`${note.values?.Name || "This note"} has unsaved changes. Close it anyway?\n\nChoose Cancel, then Save draft, to keep them.`)) return false;
    notes.delete(key);
    forgetDraft(key);
    if (key === current) current = "";
    return true;
  }

  let readyPromise = null;
  function ready() { return readyPromise || Promise.resolve(); }

  async function boot() {
    hookChart();
    [S.schema, S.phrases, S.status] = await Promise.all([api.get("/api/schema"), api.get("/api/phrases"), api.get("/api/status")]);
    S.phrases.DIAGNOSES = S.phrases.DIAGNOSES || [];
    paintTimer();
    if (DDOS.on) DDOS.on("settings:changed", (saved) => {
      if (!saved || !("visit_timer" in saved) || !!saved.visit_timer === !!S.status.visit_timer) return;
      S.status.visit_timer = !!saved.visit_timer; paintTimer();
      const pnl = $("#panel");
      if (S.tab === "note" && pnl && pnl.offsetParent !== null) refreshView();
    });
    booted = true;
  }

  async function openPatient(patient) {
    if (!patient) return;
    await ready();
    if (patient.path) { await loadNote(patient.path); putAway(); repaint(); }
    else if (patient.last || patient.first || patient.id) { await pickPatient(patient); putAway(); }
  }

  function mount(container, patient, givenApi) {
    if (!container) return null;
    if (givenApi) api = givenApi;
    scope = container;
    if (!container.querySelector(".note-app")) {
      container.innerHTML = NOTE_HTML;
      bindControls();
      booted = false;
      readyPromise = boot();
    }
    host = container;
    return { open: openPatient, showTab, get coding() { return S.coding; }, get values() { return S.values; } };
  }

  window.NoteWriter = {
    mount,
    ready,
    activate,
    close,
    savedTabs,
    open: openPatient,
    busy: () => !!rec || !!visitRec || transcribing > 0,
    mayReplace: () => !S.dirty || confirm("This note has unsaved changes. Open the filed note instead?\n\nChoose Cancel, then Save draft, to keep them."),
    showTab,
    get coding() { return S.coding; },
    get values() { return S.values; },
    get patient() { return S.patient; },
    get payer() { return S.payer; },
    set: (name, val) => setValue(name, val),      // a chart screen (SnapShot's body map) writing into the open note
    addStudy: (d) => addStudy(d),                 // Results / testing putting a drive report (a Zeto EEG) in a study row
    set onUpdate(fn) { hooks.onUpdate = fn; },
    set onTab(fn) { hooks.onTab = fn; },
  };
  DDOS.register("note", {
    mount(container, ctx) {
      return mount(container, null, ctx && ctx.api);
    },
    showTab,
    get coding() { return S.coding; },
  });
})();
