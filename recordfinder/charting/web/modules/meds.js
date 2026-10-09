/* Medications and allergies: the Meds step of the note.
   The note keeps the list as text in one box (meds-descr), the way the clinic writes it ("Gabapentin 300mg TID (new)").
   This screen shows that text as rows to search, add, edit and review; a line nobody touches is written back exactly
   as it was. The drug list is offline (meds.json): names, brands, class, common strengths, and two flags this clinic
   cares about: held before vestibular testing, and ototoxic. */
(() => {
  const HEADER = "--------PO Medications--------";
  const STRENGTH = /(^|[^\w.])(\d+(?:[.,]\d+)?(?:\s*[-\/]\s*\d+(?:\.\d+)?)*)\s*(mg|mcg|µg|g|ml|units?|iu|%|meq|caps?|capsules?|tabs?|tablets?|puffs?|sprays?|drops?|patch(?:es)?|softgels?|gumm(?:y|ies))((?:\s*\/\s*(?:ml|hr|24\s*hrs?|72\s*hrs?|spray|actuation|dose|tab|g|day))?)(?![a-z])/i;
  // Dictation commonly transcribes spoken "D/C" as "did see" / "dee cee".
  // Accept those only as a trailing parenthetical status, never in free text.
  const STATUS = /\s*[\(\[]\s*(new|not taking|d\s*\/?\s*c(?:\s*'?d)?|did see|dee\s+cee|stopped|discontinued|dose changed|changed|held|on hold)\b[^)\]]*[\)\]]\s*$/i;
  const REVIEW = /^[-*\s]*(?:I,\s*[^,]{1,24},\s*independently (?:performed medication reconciliation|reviewed the medication list with (?:the )?patient(?:,\s*\d{1,2}\/\d{1,2}\/\d{2,4})?)|medication list was reviewed with (?:the )?patient\b[^\n]*|(?:PA\s+)?[A-Za-z.]{1,12} reviewed and updated (?:the )?medication list with (?:the )?patient\b[^\n]*)[.*\s-]*$/i;
  const HEADER_RE = /^[-=*_\s]*(?:PO\s+)?medications?(?:\s+list)?[-=*_\s:]*$|^-{3,}.*-{3,}$/i;
  const SIG_AT = /\b(QD|QAM|QPM|QHS|HS|BID|TID|QID|PRN|Q\d+H|daily|nightly|weekly|monthly|once|twice|as needed)\b/i;
  const STATUS_TEXT = { new: "new", changed: "dose changed", "not taking": "not taking", stopped: "D/C", held: "held" };
  const SIGS = ["QD", "BID", "TID", "QID", "QHS", "QAM", "PRN", "QD PRN", "BID PRN", "Q4-6H PRN", "Q6H PRN", "Q8H PRN", "weekly", "monthly", "Q3 months"];
  const STATUSES = [["", "Taking"], ["new", "New"], ["changed", "Changed"], ["held", "Held"], ["not taking", "Not taking"], ["stopped", "Stopped / D/C"]];
  // What an allergy word covers, beyond the drug's own name.
  const ALLERGY_GROUPS = {
    "penicillin|pcn": /amoxicillin|ampicillin|penicillin|augmentin|dicloxacillin|nafcillin|piperacillin/i,
    "sulfa": /sulfamethoxazole|sulfasalazine|bactrim|sulfadiazine/i,
    "nsaid|ibuprofen|aspirin|asa": /ibuprofen|naproxen|meloxicam|diclofenac|celecoxib|ketorolac|indomethacin|nabumetone|etodolac|aspirin|advil|motrin|aleve/i,
    "ceph|cef|keflex": /^cef|cephalexin|keflex/i,
    "codeine|opioid|opiate|morphine": /codeine|morphine|hydrocodone|oxycodone|hydromorphone|tramadol|fentanyl|tapentadol/i,
    "statin": /statin$/i, "macrolide|erythromycin|zithromax": /azithromycin|clarithromycin|erythromycin|zithromax/i,
    "quinolone|cipro|levaquin": /floxacin$|cipro|levaquin/i, "tetracycline": /doxycycline|minocycline|tetracycline/i,
    "benzo": /azepam$|azolam$/i, "ace inhibitor|lisinopril": /pril$/i, "triptan": /triptan$/i,
  };

  // On the drug list, but in a story they are habits, not medications ("caffeine 3-4 cups").
  const NOT_MEDS = new Set(["caffeine", "nicotine", "alcohol", "oxygen", "water", "cannabis", "marijuana", "probiotic"]);
  let DB = null, loading = null, NAMES = [];
  function load() {
    if (DB) return Promise.resolve(DB);
    loading = loading || fetch("/static/meds.json", { credentials: "same-origin" }).then(r => r.json()).catch(() => []).then(d => {
      DB = Array.isArray(d) ? d : [];
      const names = new Set();
      for (const e of DB) { names.add(e.name); (e.brands || []).forEach(b => names.add(b)); }
      NAMES = [...names].sort((a, b) => b.length - a.length);
      return DB;
    });
    return loading;
  }
  function lookup(name) {
    const k = String(name || "").trim().toLowerCase();
    if (!k || !DB) return null;
    return DB.find(e => e.name.toLowerCase() === k || (e.brands || []).some(b => b.toLowerCase() === k))
        || DB.find(e => k.startsWith(e.name.toLowerCase() + " ") || (e.brands || []).some(b => k.startsWith(b.toLowerCase() + " ")))
        || DB.find(e => (e.brands || []).some(b => b.toLowerCase().startsWith(k + " ")))       // "Nurtec" is "Nurtec ODT"
        || null;
  }
  function search(q) {
    q = String(q || "").trim().toLowerCase();
    if (!q || !DB) return [];
    const out = [];
    for (const e of DB) {
      const names = [e.name, ...(e.brands || [])];
      let best = 9, hit = e.name;
      names.forEach((n, i) => {
        const l = n.toLowerCase();
        const s = l === q ? -1 : l.startsWith(q) ? (i ? 1 : 0) : (l.split(/[\s\-\/]+/).some(w => w.startsWith(q)) ? 2 : (l.includes(q) ? 3 : 9));
        if (s < best) { best = s; hit = n; }
      });
      if (best < 9) out.push({ e, hit, score: best });
    }
    return out.sort((a, b) => a.score - b.score || a.hit.length - b.hit.length || a.hit.localeCompare(b.hit)).slice(0, 10);
  }
  // Drug names in some text (the story), longest first so "Carbidopa-levodopa" wins over "Levodopa".
  function mentioned(text) {
    const low = " " + String(text || "").toLowerCase().replace(/[^a-z0-9\-]+/g, " ") + " ";
    const found = [];
    for (const n of NAMES) {
      const k = n.toLowerCase();
      if (k.length >= 4 && !NOT_MEDS.has(k) && low.includes(" " + k + " ") && !found.some(f => f.toLowerCase().includes(k))) found.push(n);
    }
    return found;
  }

  // ---------------------------------------------------------------- the box as rows
  function splitLine(line) {
    let status = "";
    const sm = STATUS.exec(line);
    if (sm) { status = statusOf(sm[1]); line = line.slice(0, sm.index).trimEnd(); }
    line = line.replace(/^[\s\-*•]+|[\s\-*•]+$/g, "");
    const m = STRENGTH.exec(line);
    if (m) {
      const at = m.index + m[1].length;
      return { name: line.slice(0, at).replace(/[\s,\-]+$/, ""), dose: (m[2] + " " + m[3] + (m[4] || "")).replace(/\s+/g, " ").trim(), sig: line.slice(m.index + m[0].length).replace(/^[\s,\-]+/, ""), status, form: "" };
    }
    const sig = SIG_AT.exec(line);
    return sig ? { name: line.slice(0, sig.index).replace(/[\s,\-]+$/, ""), dose: "", sig: line.slice(sig.index).trim(), status, form: "" } : { name: line, dose: "", sig: "", status, form: "" };
  }
  // Everyday words for how often, in the clinic's shorthand.
  function plainSig(sig) {
    const t = String(sig || "").trim().toLowerCase();
    const map = [[/^(?:as needed|when needed|prn)$/, "PRN"], [/^(?:daily|once (?:a|per) day|every day|qd|once daily)$/, "QD"], [/^(?:twice (?:a|per) day|two times (?:a|per) day|bid)$/, "BID"],
                 [/^(?:three times (?:a|per) day|tid)$/, "TID"], [/^(?:four times (?:a|per) day|qid)$/, "QID"], [/^(?:at bedtime|nightly|at night|qhs)$/, "QHS"], [/^(?:in the morning|every morning|qam)$/, "QAM"]];
    const hit = map.find(([rx]) => rx.test(t));
    return hit ? hit[1] : String(sig || "").trim();
  }
  function statusOf(w) {
    w = w.toLowerCase();
    if (/^(d\s*\/?\s*c|did see|dee\s+cee|stopped|discontinued)/.test(w)) return "stopped";
    if (w.startsWith("not")) return "not taking";
    if (w.includes("chang")) return "changed";
    if (/^(held|on hold)/.test(w)) return "held";
    return w === "new" ? "new" : "";
  }
  function parse(text) {
    const rows = [];
    for (const raw of String(text || "").replace(/\r\n?/g, "\n").split("\n")) {
      const line = raw.trim();
      if (!line) continue;
      if (REVIEW.test(line)) { const d = /(\d{1,2}\/\d{1,2}\/\d{2,4})/.exec(line); rows.push({ kind: "review", raw: line, date: d ? d[1] : "" }); continue; }
      if (HEADER_RE.test(line)) { rows.push({ kind: "header", raw: line }); continue; }
      rows.push({ kind: "med", raw: line, ...splitLine(line), was: null });
    }
    for (const r of rows) if (r.kind === "med") r.was = { dose: r.dose, sig: r.sig };
    return rows;
  }
  // "300 mg" -> "300mg", the way the clinic writes doses.
  const compact = (d) => String(d || "").trim().replace(/(\d)\s+(mg|mcg|g|ml|iu)\b/gi, "$1$2");
  function line(r) {
    return [r.name.trim(), compact(r.dose), r.sig.trim()].filter(Boolean).join(" ") + (r.status ? ` (${STATUS_TEXT[r.status] || r.status})` : "");
  }
  function toText(rows) {
    const out = [];
    if (rows.some(r => r.kind === "med") && !rows.some(r => r.kind === "header")) out.push(HEADER);
    for (const r of rows) {
      if (r.kind === "med") { if (r.name.trim() || r.dose || r.sig) out.push(r.edited ? line(r) : r.raw); }
      else out.push(r.raw);
    }
    return out.join("\n");
  }

  window.NoteMeds = function (x) {
    const { h, icon, card, check, textField, textArea, S, setValue, toast, parseDate, fmtDate } = x;
    let cache = { text: null, rows: [] };
    let asText = false;

    function rows() {
      const text = S.values["meds-descr"] || "";
      if (cache.text !== text) cache = { text, rows: parse(text) };
      return cache.rows;
    }
    function write(list) {
      const text = toText(list);
      cache = { text, rows: list };
      setValue("meds-descr", text);
    }
    function dos() { return parseDate(S.values.Date) || new Date(); }
    function reviewedToday(list) {
      const r = list.find(r => r.kind === "review" && r.date);
      const d = r && parseDate(r.date);
      return !!d && fmtDate(d) === fmtDate(dos());
    }
    function markReviewed() {
      const list = rows().filter(r => r.kind !== "review");
      const text = (S.phrases.MED_RECONCILIATION || [])[1] || "***I, {initials}, independently reviewed the medication list with patient, {date}***";
      list.push({ kind: "review", raw: text.replace("{initials}", S.status.initials || "**").replace("{date}", fmtDate(dos())), date: fmtDate(dos()) });
      write(list);
      toast("Medication list marked reviewed with the patient");
      if (panel && document.contains(panel)) render(panel);
    }
    function unreview() { write(rows().filter(r => r.kind !== "review")); render(panel); }
    function allergyHits(name, e) {
      const a = String(S.values.Allergies || "").toLowerCase();
      if (!a.trim()) return [];
      const names = [name, ...(e ? [e.name, ...(e.brands || [])] : [])].filter(Boolean).map(n => n.toLowerCase());
      const hits = [];
      for (const w of a.split(/[,;\n]+|\band\b/).map(s => s.trim()).filter(s => s.length >= 3)) {
        const word = w.replace(/\s*\(.*?\)\s*/g, "").replace(/\s*-\s*.*$/, "").trim();
        if (!word) continue;
        if (names.some(n => n.includes(word) || word.includes(n))) { hits.push(w); continue; }
        const allergic = lookup(word);
        const allergyNames = [word, ...(allergic ? [allergic.name, ...(allergic.brands || [])] : [])].map(n => n.toLowerCase());
        for (const [keys, rx] of Object.entries(ALLERGY_GROUPS)) {
          const covers = new RegExp(`\\b(?:${keys})`, "i").test(word) || allergyNames.some(n => rx.test(n));
          if (covers && names.some(n => rx.test(n))) { hits.push(w); break; }
        }
      }
      return hits;
    }
    function add(name, e, opts = {}) {
      const list = rows();
      if (list.some(r => r.kind === "med" && r.name.toLowerCase() === name.toLowerCase())) { toast(`${name} is already on the list`); return; }
      const row = { kind: "med", raw: "", name, dose: opts.dose || "", sig: opts.sig || "", status: opts.status || "", edited: true, fresh: true, was: null };
      const at = list.findIndex(r => r.kind === "review");
      if (at >= 0) list.splice(at, 0, row); else list.push(row);
      write(list);
      const hits = allergyHits(name, e);
      if (hits.length) toast(`Added ${name}. Allergy on file: ${hits.join(", ")}. Remove it with \u00d7 if it isn't safe.`);
      if (opts.dose) render(panel, { focusSearch: true }); else render(panel, { focusDose: name });
    }

    let panel = null;
    // Where the keyboard was (row and field), so a redraw can put it back.
    function focusKey() {
      const a = document.activeElement;
      if (!a || !panel || !panel.contains(a)) return null;
      const row = a.closest(".med-row");
      if (row) return { name: row.dataset.name, cls: a.dataset.v != null ? `[data-v="${a.dataset.v}"]` : "." + [...a.classList].find(c => c.startsWith("med-")) };
      if (a.closest(".med-search")) return { search: true };
      return null;
    }
    function restoreFocus(k) {
      if (!k || !panel) return;
      const el = k.search ? panel.querySelector(".med-search input")
        : [...panel.querySelectorAll(".med-row")].find(r => r.dataset.name === k.name)?.querySelector(k.cls);
      if (el) el.focus({ preventScroll: true });
    }
    function render(p, { focusDose = "", focusSearch = false } = {}) {
      const keep = panel === p ? focusKey() : null;
      panel = p;
      p.innerHTML = "";
      if (!DB) load().then(() => { if (x.active()) render(p, { focusDose }); });
      const allergyBox = textField("Allergies", { span: 9, labelText: "Allergies and reactions", placeholder: "e.g. Penicillin (hives), sulfa" });
      const nkda = check("nkda", { text: "NKDA" });
      // an allergy written down and "no known allergies" can't both be true
      let recheck = 0;
      allergyBox.querySelector("input").addEventListener("input", (ev) => {
        if (ev.target.value.trim() && S.values.nkda) setValue("nkda", false);
        clearTimeout(recheck);
        recheck = setTimeout(() => {
          for (const rowEl of p.querySelectorAll(".med-row[data-name]")) {
            const nm = rowEl.dataset.name, meta = rowEl.querySelector(".med-meta");
            if (!meta) continue;
            meta.querySelectorAll(".tag.allergy").forEach(t => t.remove());
            const hits = allergyHits(nm, lookup(nm));
            if (hits.length) meta.append(h("span", { class: "tag red allergy" }, "Allergy: " + hits.join(", ")));
          }
        }, 400);
      });
      nkda.addEventListener("click", () => { if (S.values.nkda && String(S.values.Allergies || "").trim()) toast("NKDA is ticked: clear the allergy list if there are none."); });
      p.append(card("Allergies", h("div", { class: "grid" },
        h("div", { class: "f s3" }, h("label", {}, "No known allergies"), h("div", { class: "row" }, nkda)), allergyBox,
      ), { span: "full" }));

      const list = rows(), meds = list.filter(r => r.kind === "med");
      const reviewed = reviewedToday(list);
      const tools = [
        h("button", { class: "tool", type: "button", title: "The list as it prints on the note", onclick: () => { asText = !asText; render(panel); } }, icon(asText ? "list" : "pen"), asText ? "Edit as a list" : "Edit as text"),
        reviewed ? h("span", { class: "tag green" }, icon("check"), "Reviewed this visit")
                 : h("button", { class: "btn primary small", type: "button", onclick: markReviewed, title: "Adds the clinic's review statement with your initials and today's date" }, icon("check"), "Mark reviewed with patient"),
      ];
      const body = [];
      const carried = meds.some(r => !r.fresh);
      body.push(h("div", { class: "med-banner " + (reviewed ? "ok" : "todo") },
        reviewed ? [h("span", {}, "Reviewed with the patient for this visit."), h("button", { class: "linkish", type: "button", onclick: unreview }, "Undo")]
                 : h("span", {}, meds.length ? (carried ? "From the last note. Ask about each one (still taking, dose, anything new or stopped), then mark the list reviewed."
                                                       : "Ask about each one, then mark the list reviewed.")
                                             : "No medications yet. Search to add them, or mark the list reviewed if the patient takes none.")));
      if (asText) {
        body.push(textArea("meds-descr", { cls: "big", labelText: "", phrases: [{ title: "Reconciliation", items: S.phrases.MED_RECONCILIATION || [] }, { title: "Header", items: [HEADER] }] }));
        p.append(card("Medications", body, { span: "full", tools }));
        return;
      }
      body.push(searchBox());
      if (meds.length) {
        const table = h("div", { class: "med-table", role: "table", "aria-label": "Medications" },
          h("div", { class: "med-row med-th", role: "row" }, h("div", {}, "Medication"), h("div", {}, "Dose"), h("div", {}, "How often"), h("div", {}, "Status"), h("div", {})));
        for (const r of meds) table.append(rowView(r, list));
        body.push(table);
      }
      const mentions = DB ? mentioned(S.values["ChiefComplaint-descr"] || "").filter(n => !meds.some(r => r.name.toLowerCase().includes(n.toLowerCase()) || n.toLowerCase().includes(r.name.toLowerCase().split(" ")[0]))) : [];
      if (mentions.length) body.push(h("div", { class: "med-mentions" }, h("span", { class: "muted" }, "In today's story:"),
        mentions.map(n => h("button", { class: "chip small", type: "button", title: `Add ${n} to the list`, onclick: () => add(n, lookup(n)) }, icon("plus"), n))));
      const active = meds.filter(r => r.status !== "stopped" && r.status !== "not taking").map(r => [r, lookup(r.name)]);
      const hold = active.filter(([, e]) => e && e.vestibular_suppressant).map(([r]) => r.name);
      const oto = meds.filter(r => r.status !== "not taking").map(r => [r, lookup(r.name)]).filter(([, e]) => e && e.ototoxic).map(([r]) => r.name);
      if (hold.length) body.push(h("div", { class: "med-note amber" }, icon("alert"), h("span", {}, h("b", {}, "Before VNG / vestibular testing: "), `${hold.join(", ")} ${hold.length > 1 ? "are" : "is"} usually held for 48 hours.`)));
      if (oto.length) body.push(h("div", { class: "med-note red" }, icon("alert"), h("span", {}, h("b", {}, "Ototoxic: "), oto.join(", ") + "."),
        S.values["hist-ototox"] ? null : h("button", { class: "tool", type: "button", onclick: () => { setValue("hist-ototox", true); setValue("hist-ototox-denies", false); toast("Ticked: Hx of ototoxic drug exposure"); render(panel); } }, "Tick Hx of ototoxic drug exposure")));
      p.append(card("Medications", body, { span: "full", tools }));
      if (focusDose) { const el = [...p.querySelectorAll(".med-row")].find(r => r.dataset.name === focusDose); el && el.querySelector(".med-dose").focus({ preventScroll: true }); }
      else restoreFocus(focusSearch ? { search: true } : keep);
    }

    function rowView(r, list) {
      const e = lookup(r.name);
      const update = (k, v) => {
        r[k] = v; r.edited = true;
        if ((k === "dose" || k === "sig") && r.was && !r.fresh && (!r.status || r.autoChanged)) {
          const was = (x) => String(x || "").trim().toLowerCase().replace(/\s+/g, "");
          const differs = (was(r.was.dose) && was(r.dose) !== was(r.was.dose)) || (was(r.was.sig) && was(r.sig) !== was(r.was.sig));
          r.status = differs ? "changed" : ""; r.autoChanged = differs; paintStatus();
        }
        write(list);
      };
      const name = h("input", { class: "med-name", type: "text", value: r.name, "aria-label": "Medication name", spellcheck: "false" });
      name.addEventListener("change", () => { update("name", name.value); render(panel); });
      const sdl = "dl-str-" + (e ? e.name.replace(/\W+/g, "") : "none");
      if (e && !document.getElementById(sdl)) document.body.append(h("datalist", { id: sdl }, (e.strengths || []).map(s => h("option", { value: s }))));
      const dose = h("input", { class: "med-dose", type: "text", value: r.dose, placeholder: e && e.strengths && e.strengths[0] ? e.strengths[0] : "dose", list: e ? sdl : null, "aria-label": "Dose of " + r.name, spellcheck: "false" });
      dose.addEventListener("input", () => update("dose", dose.value));
      const gdl = "dl-sig-" + (e ? e.name.replace(/\W+/g, "") : "any");
      if (!document.getElementById(gdl)) document.body.append(h("datalist", { id: gdl }, [...new Set([...(e && e.sigs || []), ...SIGS])].map(s => h("option", { value: s }))));
      const sig = h("input", { class: "med-sig", type: "text", value: r.sig, placeholder: "e.g. BID", list: gdl, "aria-label": "How often " + r.name + " is taken", spellcheck: "false" });
      sig.addEventListener("input", () => update("sig", sig.value));
      const status = h("select", { class: "med-status", "aria-label": "Status of " + r.name },
        STATUSES.map(([v, label]) => h("option", { value: v }, label)));
      const paintStatus = () => {
        status.value = r.status || "";
        row.classList.toggle("stopped", r.status === "stopped"); row.classList.toggle("off", r.status === "not taking");
      };
      status.addEventListener("change", () => { r.status = status.value; r.autoChanged = false; r.edited = true; write(list); paintStatus(); render(panel); });
      const meta = [];
      if (e) {
        const other = r.name.toLowerCase() === e.name.toLowerCase() ? (e.brands || []).slice(0, 2).join(", ") : e.name;
        meta.push(h("span", { class: "muted" }, [other, e.class].filter(Boolean).join(" · ")));
        if (e.vestibular_suppressant) meta.push(h("span", { class: "tag orange" }, "Hold before VNG"));
        if (e.ototoxic) meta.push(h("span", { class: "tag red" }, "Ototoxic"));
      } else if (r.name && DB && DB.length) meta.push(h("span", { class: "muted" }, "Not in the drug list: kept as typed"));
      const hits = allergyHits(r.name, e);
      if (hits.length) meta.push(h("span", { class: "tag red allergy" }, "Allergy: " + hits.join(", ")));
      const del = h("button", { class: "icon-btn quiet", type: "button", title: "Remove from the list (use D/C if it was stopped today)", "aria-label": "Remove " + r.name, onclick: () => { list.splice(list.indexOf(r), 1); write(list); render(panel); } }, icon("x"));
      const row = h("div", { class: "med-row", role: "row", "data-name": r.name }, h("div", { class: "med-cell-name" }, name, h("div", { class: "med-meta" }, meta)), dose, sig, status, del);
      paintStatus();
      return row;
    }

    function searchBox() {
      const wrap = h("div", { class: "med-search" });
      const inp = h("input", { type: "search", placeholder: "Add a medication: name or brand, with the dose if you like (gabapentin 300 TID)", "aria-label": "Add a medication",
        autocomplete: "off", spellcheck: "false", role: "combobox", "aria-expanded": "false", "aria-controls": "medResults", "aria-autocomplete": "list" });
      const box = h("div", { class: "results med-results", id: "medResults", hidden: true, role: "listbox" });
      let hits = [], at = 0;
      const typed = () => {
        const parts = splitLine(inp.value.trim());
        parts.name = parts.name || inp.value.trim();
        const m = /^(.*?[a-z)])\s+(\d+(?:[.,]\d+)?(?:\s*[-\/]\s*\d+(?:\.\d+)?)*)$/i.exec(parts.name);
        if (!parts.dose && m && lookup(m[1])) { parts.name = m[1]; parts.dose = m[2]; }
        return parts;
      };
      const withUnit = (dose, e) => {
        if (!dose || /[a-z%]/i.test(dose) || !e) return dose;
        const s = (e.strengths || []).find(x => x.replace(/\s+/g, "").toLowerCase().startsWith(dose.replace(/\s+/g, "") + "m") || x.startsWith(dose + " "));
        return s || dose;
      };
      const paint = () => {
        box.innerHTML = "";
        const q = inp.value.trim();
        if (!q) { box.hidden = true; inp.setAttribute("aria-expanded", "false"); return; }
        hits = search(typed().name);
        hits.forEach((x, i) => {
          const e = x.e, other = x.hit === e.name ? (e.brands || []).slice(0, 2).join(", ") : e.name;
          box.append(h("button", { type: "button", class: i === at ? "on" : "", id: "medOpt" + i, role: "option", "aria-selected": String(i === at), tabindex: "-1", onmousedown: (ev) => { ev.preventDefault(); choose(i); } },
            h("span", {}, h("b", {}, x.hit), other ? h("span", { class: "muted" }, ` (${other})`) : null),
            h("small", {}, [e.class, e.otc ? "OTC" : ""].filter(Boolean).join(" · ")),
            e.vestibular_suppressant ? h("span", { class: "tag orange" }, "Hold before VNG") : null, e.ototoxic ? h("span", { class: "tag red" }, "Ototoxic") : null));
        });
        box.append(h("button", { type: "button", class: hits.length === at ? "on" : "", id: "medOpt" + hits.length, role: "option", "aria-selected": String(hits.length === at), tabindex: "-1", onmousedown: (ev) => { ev.preventDefault(); choose(hits.length); } },
          h("span", {}, "Add \u201c", h("b", {}, q), "\u201d as typed")));
        box.hidden = false;
        inp.setAttribute("aria-expanded", "true");
        inp.setAttribute("aria-activedescendant", "medOpt" + at);
      };
      const choose = (i) => {
        const q = inp.value.trim();
        if (!q) return;
        const t = typed();
        if (i < hits.length) add(hits[i].hit, hits[i].e, { dose: withUnit(t.dose, hits[i].e), sig: t.sig });
        else add(t.name[0].toUpperCase() + t.name.slice(1), null, { dose: t.dose, sig: t.sig });
      };
      inp.addEventListener("input", () => { at = 0; load().then(paint); });
      inp.addEventListener("keydown", (ev) => {
        if (ev.key === "ArrowDown") { ev.preventDefault(); at = Math.min(at + 1, hits.length); paint(); }
        else if (ev.key === "ArrowUp") { ev.preventDefault(); at = Math.max(at - 1, 0); paint(); }
        else if (ev.key === "Enter") { ev.preventDefault(); choose(at); }
        else if (ev.key === "Escape") { box.hidden = true; }
      });
      inp.addEventListener("blur", () => setTimeout(() => { box.hidden = true; }, 120));
      wrap.append(icon("search"), inp, box);
      return wrap;
    }

    // Put a drug the notes mention on the list, with the dose and how often as the notes give them
    // ("meclizine 25 mg as needed" -> "Meclizine 25mg PRN"). False when it is already there.
    // Named apart from add() above (the search box's): two declarations of one name in this scope and the later
    // one silently replaces the earlier, which once broke adding from the search.
    function addMentioned(name, context = "") {
      const list = rows().slice();
      const e = lookup(name);
      const shown = e ? ((e.brands || []).find(b => b.toLowerCase() === name.toLowerCase()) || e.name) : name.replace(/^\w/, c => c.toUpperCase());
      const same = (r) => r.kind === "med" && [shown, name, e && e.name].filter(Boolean).some(n => r.name.trim().toLowerCase() === n.toLowerCase());
      if (list.some(same)) return false;
      const at = new RegExp("\\b" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b([^.;,\\n]{0,48})", "i").exec(String(context || ""));
      const tail = at ? at[1].split(/\s+(?:and|but|which|that|for|since|with)\s+/i)[0] : "";
      const got = tail ? splitLine(name + tail) : { dose: "", sig: "" };
      const r = { kind: "med", raw: "", name: shown, dose: got.dose, sig: plainSig(got.sig), status: "", form: "", edited: true, was: null };
      const firstMed = list.findIndex(x => x.kind === "med" || x.kind === "header");
      const lastMed = list.map(x => x.kind).lastIndexOf("med");
      list.splice(lastMed >= 0 ? lastMed + 1 : (firstMed >= 0 ? firstMed + 1 : list.length), 0, r);
      write(list);
      return true;
    }
    return { render, load, mentioned, markReviewed, addMentioned, names: () => NAMES, reviewedToday: () => reviewedToday(rows()) };
  };
  window.NoteMeds.parse = parse;
  window.NoteMeds.toText = toText;
})();
