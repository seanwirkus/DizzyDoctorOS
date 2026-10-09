/* Provider superbill creation, on a page laid out like the PDF it files (superbill.fill()): the same
   title, header lines, tables and column widths, typed into directly. Pick the patient (demographics and
   insurance attach themselves), add ICD-10 diagnoses and CPT service lines, check the PDF itself, then
   save a draft or file it. "From open note" starts one from the note's own coding. The ICD-10 and CPT
   lists are offline (/static/icd.json, /static/cpt.json), the same way the drug list is; the server
   search is the fallback. */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function h(tag, attrs = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }
  const icon = (name) => {
    const t = document.createElement("template");
    t.innerHTML = (window.DDOS && DDOS.icon) ? DDOS.icon(name) : "";
    return t.content.firstChild || document.createTextNode("");
  };
  const apiDefault = {
    async get(p) { const r = await fetch(p, { credentials: "same-origin" }); const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || r.statusText); return d; },
    async post(p, body) { const r = await fetch(p, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || r.statusText); return d; },
  };
  function toast(msg, ctx) {
    if (ctx && ctx.toast) { ctx.toast(msg); return; }
    const t = document.querySelector("#shellToast");
    if (t) { t.textContent = msg; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 2800); }
  }

  let ICD = null, CPT = null;
  function loadLists() {
    const get = (url) => fetch(url, { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : [])).catch(() => []);
    return Promise.all([
      ICD !== null ? ICD : get("/static/icd.json").then((d) => (ICD = Array.isArray(d) ? d : [])),
      CPT !== null ? CPT : get("/static/cpt.json").then((d) => (CPT = Array.isArray(d) ? d : [])),
    ]);
  }
  function searchLocal(rows, q, codeFirst) {
    q = String(q || "").trim().toLowerCase();
    if (!q) return [];
    const compact = q.replace(/[.\s]+/g, "");
    const first = [], then = [];
    for (const row of rows) {
      const code = String(row.code || ""), desc = String(row.description || ""), extra = String(row.keywords || row.category || "");
      const low = `${code} ${desc} ${extra}`.toLowerCase();
      if (code.toLowerCase().replace(".", "").startsWith(compact)) first.push(row);
      else if (codeFirst && q.length >= 2 && low.includes(q)) then.push(row);
      else if (!codeFirst && q.split(/\s+/).every((w) => low.includes(w))) then.push(row);
    }
    return (first.concat(then)).slice(0, 10);
  }

  function blankValues() {
    return { Name: "", DOB: "", Date: "", "HPI-age": "", sex: "", acct: "", phone: "", insurance: "", insurance_id: "", secondary: "", secondary_id: "", rendering: "", attending: "", incident_to: "", visit: "", minutes: "", payer: "", diagnoses: [], procedures: [], notes: "" };
  }
  function todayStr() {
    const d = new Date();
    return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}/${d.getFullYear()}`;
  }

  // The clinic's providers, as the superbill names them (playbooks/superbill-provider-not-circled.md).
  const PROVIDERS = ["Ian Purcell, M.D., Ph.D.", "Monali Patel, M.D.", "Touraj Yari, PA-C", "Jake Perkins, PA-C", "Carter Copeland, PA-C", "Evan Camarillo, PA-C"];
  const PHYSICIANS = PROVIDERS.slice(0, 2);
  const PAYERS = ["Medicare", "Medicare Advantage", "Medi-Cal", "SCMG (HMO)", "Scripps (HMO)", "Aetna", "Anthem Blue Cross", "Blue Shield", "Cigna", "Humana", "UnitedHealthcare", "Self-pay"];

  // What is still missing before filing: the same rules as superbill.validate() on the server.
  function missingOf(v) {
    const out = [], dx = v.diagnoses || [], px = v.procedures || [];
    if (!String(v.Name || "").trim()) out.push("the patient's name");
    if (!String(v.Date || "").trim()) out.push("the date of service");
    if (!dx.length) out.push("at least one diagnosis (ICD-10)");
    else if (dx.some((d) => !String(d.code || "").trim())) out.push("an ICD-10 code on every diagnosis row");
    if (!px.length) out.push("at least one service line (CPT)");
    else if (px.some((p) => !String(p.cpt || "").trim())) out.push("a CPT code on every service line");
    if (!String(v.rendering || "").trim()) out.push("the rendering provider");
    return out;
  }
  const stamp = (d = new Date()) => {
    const hr = d.getHours() % 12 || 12;
    return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}/${d.getFullYear()} ${String(hr).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")} ${d.getHours() < 12 ? "AM" : "PM"}`;
  };
  const appVersion = () => { try { return (DDOS.context && DDOS.context().status && DDOS.context().status.version) || ""; } catch { return ""; } };

  // Inline text on the sheet grows with what's typed, so a filled line reads like the printed one. It is measured
  // with a hidden copy of the text in the input's own font, which is exact whatever font the system picks.
  const ruler = document.createElement("span");
  ruler.setAttribute("aria-hidden", "true");
  ruler.style.cssText = "position:absolute; left:-9999px; top:0; visibility:hidden; white-space:pre;";
  function fit(inp) {
    if (!inp.isConnected) return;
    const cs = getComputedStyle(inp);
    for (const k of ["fontFamily", "fontSize", "fontWeight", "fontStyle", "letterSpacing", "fontStretch"]) ruler.style[k] = cs[k];
    ruler.textContent = inp.value || inp.placeholder || "";
    if (!ruler.isConnected) document.body.append(ruler);
    inp.style.width = Math.ceil(ruler.getBoundingClientRect().width + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + 8) + "px";
  }

  async function mount(host, ctx) {
    ctx = ctx || {};
    const api = ctx.api || apiDefault;
    const S = { values: blankValues(), patient: ctx.notePatient || ctx.patient || null, sheets: [], focus: "", dirty: false };
    host.innerHTML = "";
    const wrap = h("div", { class: "sb-wrap" });
    host.append(wrap);

    function setValue(name, val) { S.values[name] = val; S.dirty = true; refreshStatus(); }
    // A blank on the sheet: borderless text in the sheet's own type, underlined only while hovered or typed in.
    function blank(name, { ph = "", cls = "", list = null, label } = {}) {
      const inp = h("input", { type: "text", class: "sb-in" + (cls ? " " + cls : ""), value: S.values[name] || "", placeholder: ph, autocomplete: "off", spellcheck: "false", "aria-label": label || ph || name });
      if (list) inp.setAttribute("list", list);
      inp.addEventListener("input", () => { setValue(name, inp.value); fit(inp); });
      return inp;
    }
    function cell(row, key, { ph = "", cls = "", label = "", grow = false } = {}) {
      const inp = h("input", { type: "text", class: "sb-in sb-cell" + (cls ? " " + cls : "") + (grow ? " grow" : ""), value: row[key] || "", placeholder: ph, autocomplete: "off", spellcheck: "false", "aria-label": label });
      inp.addEventListener("input", () => { row[key] = inp.value; S.dirty = true; refreshStatus(); });
      return inp;
    }
    const dot = () => h("span", { class: "sb-dot" }, "  ·  ");

    function paint() {
      wrap.innerHTML = "";
      wrap.append(toolbar(), sheet(), sheetsCard());
      requestAnimationFrame(() => {
        wrap.querySelectorAll("input.sb-in:not(.grow)").forEach(fit);
        if (S.focus) { const el = wrap.querySelector(`[data-focus="${S.focus}"] input`); S.focus = ""; if (el) el.focus(); }
      });
      refreshStatus();
    }
    function refreshStatus() {
      const miss = missingOf(S.values);
      const chip = wrap.querySelector(".sb-status");
      if (chip) {
        chip.className = "sb-status tag " + (miss.length ? "orange" : "green");
        chip.textContent = miss.length ? `${miss.length} still needed` : "Ready to file";
        chip.title = miss.length ? "Still needed: " + miss.join(", ") + "." : "Everything the office needs is on the sheet.";
      }
      const draft = wrap.querySelector(".sb-draft");
      if (draft) { draft.hidden = !miss.length; draft.textContent = "DRAFT — " + miss.join("; ") + " still needed."; }
      const file = wrap.querySelector(".sb-file");
      if (file) file.disabled = !!miss.length;
    }

    // ------------------------------------------------------------ above the sheet
    function toolbar() {
      const search = h("input", { type: "search", placeholder: S.patient ? "Assign to another patient" : "Assign to a patient: name, Pt # or DOB", "aria-label": "Assign to a patient", autocomplete: "off", spellcheck: "false" });
      const results = h("div", { class: "results", hidden: true });
      let timer;
      search.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const q = search.value.trim();
          if (!q) { results.hidden = true; return; }
          let rows = [];
          try { rows = await api.get("/api/patients?q=" + encodeURIComponent(q)); } catch { rows = []; }
          results.innerHTML = "";
          if (!rows.length) results.append(h("button", { disabled: true }, "No matches in the patient directory"));
          for (const r of rows) results.append(h("button", { type: "button", onclick: () => pickPatient(r) }, h("span", {}, `${r.last}, ${r.first}`), h("small", {}, [r.dob, r.acct ? "Pt # " + r.acct : ""].filter(Boolean).join(" · "))));
          results.hidden = false;
        }, 160);
      });
      search.addEventListener("keydown", (e) => { if (e.key === "Escape") results.hidden = true; });
      const payer = h("input", { type: "text", class: "sb-payer-in", value: S.values.payer || "", placeholder: "Payer", list: "sb-dl-payers", "aria-label": "Payer, for the coding rules", title: "Payer, for the coding rules (not printed)", autocomplete: "off" });
      payer.addEventListener("input", () => { setValue("payer", payer.value); syncFromNote(); });

      return h("div", { class: "sb-toolbar" },
        h("div", { class: "sb-group" },
          h("div", { class: "sb-search sb-assign" }, search, results),
          S.patient ? h("span", { class: "tag green", title: "Demographics and insurance came from the chart" }, "Assigned") : h("span", { class: "tag" }, "Unassigned"),
          payer),
        h("div", { class: "sb-group sb-group-end" },
          h("span", { class: "sb-status tag" }),
          h("button", { class: "btn sb-print-btn", type: "button", title: "Print this superbill to a printer", onclick: printSuperbill }, icon("print"), "Print"),
          h("button", { class: "btn", type: "button", onclick: () => doSave("draft") }, "Save draft"),
          h("button", { class: "btn primary sb-file", type: "button", onclick: () => doSave("final") }, "File superbill")),
        h("datalist", { id: "sb-dl-payers" }, PAYERS.map((x) => h("option", { value: x }))),
        h("datalist", { id: "sb-dl-providers" }, PROVIDERS.map((x) => h("option", { value: x }))),
        h("datalist", { id: "sb-dl-physicians" }, PHYSICIANS.map((x) => h("option", { value: x }))),
        h("datalist", { id: "sb-dl-visit" }, ["new", "established"].map((x) => h("option", { value: x }))));
    }

    // ------------------------------------------------------------ the sheet, laid out like the filed PDF
    function sheet() {
      const v = S.values;
      const ver = appVersion();
      const inc = h("select", { class: "sb-in sb-select", "aria-label": "Medicare incident-to" }, ["", "YES", "NO"].map((x) => h("option", { value: x, selected: (v.incident_to || "") === x ? true : false }, x || "—")));
      inc.addEventListener("change", () => setValue("incident_to", inc.value));
      const notes = h("textarea", { class: "sb-in sb-notes", rows: 2, placeholder: "Notes for billing (optional; printed only when filled in)", "aria-label": "Notes for billing" });
      notes.value = v.notes || "";
      const grow = () => { notes.style.height = "auto"; notes.style.height = notes.scrollHeight + "px"; };
      notes.addEventListener("input", () => { setValue("notes", notes.value); grow(); });
      requestAnimationFrame(grow);
      return h("div", { class: "sb-page" }, h("article", { class: "sb-sheet", "aria-label": "Superbill" },
        h("header", { class: "sb-top" },
          h("div", {}, h("div", { class: "sb-title" }, "SUPERBILL"), h("div", { class: "sb-practice" }, "Ian Purcell, M.D., APC", ver ? h("span", {}, "  ·  DizzyDoctor " + ver) : null)),
          h("div", { class: "sb-dos" }, "DOS ", blank("Date", { ph: "**/**/****", label: "Date of service" }))),
        h("div", { class: "sb-rule" }),
        h("div", { class: "sb-name" }, blank("Name", { ph: "****", cls: "name", label: "Patient name (Last, First)" })),
        h("div", { class: "sb-line faint" }, "DOB ", blank("DOB", { ph: "mm/dd/yyyy", label: "Date of birth" }), dot(), blank("HPI-age", { ph: "age", label: "Age" }), " y/o ", blank("sex", { ph: "sex", label: "Sex" }), dot(),
          "Pt # ", blank("acct", { ph: "—", label: "Patient number" }), dot(), blank("phone", { ph: "phone", label: "Phone" })),
        h("div", { class: "sb-line faint" }, "Primary: ", blank("insurance", { ph: "No insurance on file", label: "Primary insurance" }), "  ID ", blank("insurance_id", { ph: "—", label: "Primary member ID" }), dot(),
          "Secondary: ", blank("secondary", { ph: "none", label: "Secondary insurance" }), "  ID ", blank("secondary_id", { ph: "—", label: "Secondary member ID" })),
        h("div", { class: "sb-line strong" }, "Rendering: ", blank("rendering", { ph: "****", list: "sb-dl-providers", label: "Rendering provider" }), dot(),
          "Attending: ", blank("attending", { ph: "—", list: "sb-dl-physicians", label: "Attending physician" }), dot(),
          "Incident-to: ", inc, dot(), blank("visit", { ph: "visit", list: "sb-dl-visit", label: "Visit (new or established)" }), " · ", blank("minutes", { ph: "—", label: "Minutes" }), " min"),
        diagnosesTable(), servicesTable(),
        h("section", { class: "sb-sec" }, h("h3", {}, "Notes"), notes),
        h("footer", { class: "sb-foot" },
          h("div", { class: "sb-draft", hidden: true }),
          h("div", { class: "sb-meta" }, `Provider-created superbill  ·  ${stamp()}${ver ? "  ·  DizzyDoctor " + ver : ""}`))));
    }
    function removeBtn(label, onclick) {
      return h("button", { class: "sb-x", type: "button", title: "Remove", "aria-label": "Remove " + label, onclick }, "×");
    }
    function diagnosesTable() {
      const rows = S.values.diagnoses || [];
      const t = h("div", { class: "sb-table sb-dx", role: "table", "aria-label": "Diagnoses" },
        h("div", { class: "sb-th", role: "row" }, h("span", {}, "#"), h("span", {}, "ICD-10"), h("span", {}, "Description")));
      rows.forEach((d, i) => t.append(h("div", { class: "sb-tr", role: "row" },
        h("span", { class: "sb-num" }, String(i + 1)), cell(d, "code", { ph: "***", cls: "code", label: `Diagnosis ${i + 1} ICD-10` }), cell(d, "description", { grow: true, label: `Diagnosis ${i + 1} description` }),
        removeBtn(d.code || "diagnosis", () => { S.values.diagnoses.splice(i, 1); S.dirty = true; paint(); }))));
      t.append(addRow("dx", "Add a diagnosis: ICD-10 code or words (dizziness, BPPV, tinnitus)", icdSearch, (x) => {
        S.values.diagnoses.push({ code: x.code, description: x.description }); S.dirty = true; S.focus = "dx"; paint();
      }));
      return h("section", { class: "sb-sec" }, h("h3", {}, "Diagnoses (ICD-10)"), t);
    }
    function servicesTable() {
      const rows = S.values.procedures || [];
      const t = h("div", { class: "sb-table sb-px", role: "table", "aria-label": "Services" },
        h("div", { class: "sb-th", role: "row" }, h("span", {}, "CPT"), h("span", {}, "Mod"), h("span", {}, "Units"), h("span", {}, "Dx"), h("span", {}, "Description")));
      rows.forEach((p, i) => t.append(h("div", { class: "sb-tr", role: "row" },
        cell(p, "cpt", { ph: "***", cls: "code", label: `Service ${i + 1} CPT` }), cell(p, "modifier", { ph: "", label: `Service ${i + 1} modifier` }),
        cell(p, "units", { ph: "1", label: `Service ${i + 1} units` }), cell(p, "dx", { ph: "", label: `Service ${i + 1} diagnosis pointer` }),
        cell(p, "description", { grow: true, label: `Service ${i + 1} description` }),
        removeBtn(p.cpt || "service line", () => { S.values.procedures.splice(i, 1); S.dirty = true; paint(); }))));
      t.append(addRow("px", "Add a service: CPT code or words (99215, Epley, oculography)", cptSearch, (x) => {
        S.values.procedures.push({ cpt: x.code, modifier: "", units: "1", dx: S.values.diagnoses.length === 1 ? "1" : "", description: x.description }); S.dirty = true; S.focus = "px"; paint();
      }));
      return h("section", { class: "sb-sec" }, h("h3", {}, "Services (CPT)"), t);
    }
    // The last row of a table: type a code or words, Enter or a click adds the line.
    function addRow(key, placeholder, onSearch, onPick) {
      const inp = h("input", { type: "search", placeholder, "aria-label": placeholder, autocomplete: "off", spellcheck: "false" });
      const box = h("div", { class: "results sb-results", hidden: true });
      let hits = [];
      inp.addEventListener("input", async () => {
        const q = inp.value.trim();
        if (!q) { box.hidden = true; return; }
        hits = await onSearch(q);
        box.innerHTML = "";
        if (!hits.length) box.append(h("button", { disabled: true }, "No matches"));
        hits.forEach((x, i) => box.append(h("button", { type: "button", onclick: () => onPick(hits[i]) },
          h("span", {}, h("b", {}, x.code), ` ${x.description}`), x.category ? h("small", {}, x.category) : null)));
        box.hidden = false;
      });
      inp.addEventListener("keydown", (e) => { if (e.key === "Escape") box.hidden = true; if (e.key === "Enter" && hits.length) { e.preventDefault(); onPick(hits[0]); } });
      return h("div", { class: "sb-add sb-search", "data-focus": key }, h("span", { class: "sb-plus", "aria-hidden": "true" }, "+"), inp, box);
    }
    function sheetsCard() {
      const list = h("div", { class: "sb-rows" });
      if (!S.sheets.length) list.append(h("div", { class: "muted" }, S.patient ? "No superbills on file for this patient." : "Assign the patient to see their superbills."));
      for (const s of S.sheets) list.append(h("div", { class: "sb-row" }, h("span", { class: "sb-desc" }, s.name || s.path), h("span", { class: "tag" }, s.kind || ""), h("button", { class: "linkish", type: "button", onclick: () => loadSheet(s.path) }, "Open")));
      return h("section", { class: "card sb-onfile" }, h("div", { class: "card-head" }, h("h2", {}, "Superbills on file")), list);
    }

    async function pickPatient(r) {
      S.patient = r;
      try {
        const res = await api.post("/api/superbill/attach", { values: S.values, patient: r });
        S.values = { ...S.values, ...(res.values || {}) };
        try { S.sheets = await api.get("/api/superbills?" + new URLSearchParams({ last: r.last || "", first: r.first || "" })); } catch { S.sheets = []; }
        toast(`Assigned to ${r.last}, ${r.first}. Demographics and insurance attached.`, ctx);
      } catch (e) { toast(e.message, ctx); }
      paint();
    }
    async function icdSearch(q) {
      await loadLists();
      if (ICD && ICD.length) return searchLocal(ICD, q, false);
      try { return await api.get("/api/superbill/icd?q=" + encodeURIComponent(q)); } catch { return []; }
    }
    async function cptSearch(q) {
      await loadLists();
      if (CPT && CPT.length) return searchLocal(CPT, q, true);
      try { return await api.get("/api/superbill/cpt?q=" + encodeURIComponent(q)); } catch { return []; }
    }

    async function printSuperbill() {
      const btn = wrap.querySelector(".sb-print-btn");
      if (btn) btn.disabled = true;
      try {
        const prData = await api.get("/api/printers").catch(() => ({ printers: [], default: "" }));
        const printers = prData.printers || [];
        const defaultPrinter = prData.default || (printers[0]?.id || "");
        if (document.querySelector(".sb-print-dialog")) document.querySelector(".sb-print-dialog").remove();
        const modal = h("div", { class: "modal sb-print-dialog", role: "dialog", "aria-modal": "true", "aria-label": "Print superbill" });
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
        const sysDialogCheck = h("input", { type: "checkbox", id: "sbPrintSysDialog", style: "margin-right:6px;" });
        const close = () => modal.remove();
        const doPrint = async () => {
          const selectedPrinter = select.value;
          const copies = parseInt(copiesInput.value, 10) || 1;
          const useSysDialog = sysDialogCheck.checked;
          modal.remove();
          toast("Sending superbill to printer...", ctx);
          try {
            const res = await api.post("/api/superbill/print", {
              values: S.values,
              printer: selectedPrinter,
              copies,
              system_dialog: useSysDialog,
              document: "superbill"
            });
            if (res && res.error) throw new Error(res.error);
            toast(res.printer ? `Superbill printed to ${res.printer}.` : "Superbill print job sent.", ctx);
          } catch (err) {
            toast("Print failed: " + (err.message || err), ctx);
          }
        };
        modal.append(h("div", { class: "modal-box", style: "max-width:440px;" },
          h("h2", {}, "Print superbill"),
          h("p", { class: "muted", style: "margin:0 0 14px 0;font-size:13px;" },
            [S.values.Name || "Patient", S.values.Date ? `DOS ${S.values.Date}` : ""].filter(Boolean).join(" · ")),
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
        toast("Couldn't open print dialog: " + (e.message || e), ctx);
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function syncFromNote() {
      const noteVals = ctx.noteValues || (window.NoteWriter && NoteWriter.values) || null;
      const patient = S.patient || ctx.notePatient || (window.NoteWriter && NoteWriter.patient) || (window.DDOS && DDOS.context ? DDOS.context().patient : null) || {};
      const payer = S.values.payer || ctx.payer || (window.NoteWriter && NoteWriter.payer) || "";
      if (payer && !S.values.payer) S.values.payer = payer;
      if (noteVals && Object.keys(noteVals).length > 0) {
        try {
          const r = await api.post("/api/superbill/from-note", { values: noteVals, payer: S.values.payer || "", patient });
          if (r && r.values) {
            if (!S.dirty) {
              S.values = { ...S.values, ...r.values };
            } else {
              for (const [k, v] of Object.entries(r.values)) {
                if (!S.values[k] || (Array.isArray(S.values[k]) && !S.values[k].length)) {
                  S.values[k] = v;
                }
              }
            }
          }
        } catch { /* proceed with available values */ }
      }
      if (patient && (patient.last || patient.first || patient.id)) {
        S.patient = patient;
        try {
          const a = await api.post("/api/superbill/attach", { values: S.values, patient }).catch(() => null);
          if (a && a.values) S.values = { ...S.values, ...a.values };
          S.sheets = await api.get("/api/superbills?" + new URLSearchParams({ last: patient.last || "", first: patient.first || "" })).catch(() => []);
        } catch { /* list is convenience */ }
      }
      if (!S.values.Date) S.values.Date = todayStr();
      if (host.isConnected) paint();
    }

    async function doSave(kind) {
      try {
        const r = await api.post("/api/superbill/save", { values: S.values, kind });
        S.dirty = false;
        toast(`${kind === "final" ? "Superbill filed" : "Draft saved"}: ${r.name}`, ctx);
        if (S.patient) { try { S.sheets = await api.get("/api/superbills?" + new URLSearchParams({ last: S.patient.last || "", first: S.patient.first || "" })); } catch { /* list is a convenience */ } }
      } catch (e) { toast(e.message, ctx); }
      paint();
    }
    async function loadSheet(path) {
      try {
        const r = await api.get("/api/superbill?path=" + encodeURIComponent(path));
        S.values = { ...blankValues(), ...(r.values || {}) };
        S.dirty = false;
        toast("Opened " + path.split(/[\\/]/).pop(), ctx);
      } catch (e) { toast(e.message, ctx); }
      paint();
    }

    // Automatically generate from the open note upon mounting, and load lists
    paint();
    loadLists();
    syncFromNote();
    activeSuperbill = { print: printSuperbill, sync: syncFromNote, get values() { return S.values; } };
  }

  let activeSuperbill = null;
  DDOS.register("superbill", {
    mount,
    print() { if (activeSuperbill) return activeSuperbill.print(); },
    sync() { if (activeSuperbill) return activeSuperbill.sync(); },
    unmount() { activeSuperbill = null; }
  });
})();
