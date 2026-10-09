/* Vestibular Testing: the IR oculography / positional section of the latest filed note, drawn as a
 * canal map and a findings table, with the study impression, the results timeline and a short
 * interpretation guide. Reads ctx.chart.notes -> GET /api/note, ctx.snapshot.studies and GET /api/schema. */
(() => {
  const W = 250, H = 182;                    // one ear's drawing box
  let gen = 0, schemaFields = null;

  // Labels come from /api/schema; these cover the screen if that request fails.
  const LABELS = {
    "goggle-dr": "Oculography performed by", "goggle-date": "Date", "see-epley": "See Epley Chair / CRM Chair report",
    "goggle-treatment-x": "Treatment", "imp-normal": "Normal", "imp-rightpost": "Right posterior canal BPPV",
    "imp-leftpost": "Left posterior canal BPPV", "imp-rightcupu": "Right cupulolithiasis", "imp-leftcupu": "Left cupulolithiasis",
    "imp-leftlateral": "Left lateral canal BPPV", "imp-rightlateral": "Right lateral canal BPPV",
    "imp-leftanterior": "Left anterior canal BPPV", "imp-rightanterior": "Right anterior canal BPPV",
    "imp-atyp": "Atypical", "imp-rightnyst": "Right sustained nystagmus", "imp-leftnyst": "Left sustained nystagmus",
  };
  const SUFFIX = { normal: "Normal", torsion: "Torsional", fatigue: "Fatigable", latency: "Latency", atyp: "Atypical", geo: "Geotropic",
    ageo: "Ageotropic", down: "Down beat", up: "Upbeat", atypical: "Atypical", leftbeat: "Left beat", rightbeat: "Right beat",
    downbeat: "Down beat", upbeat: "Upbeat", leftevoked: "Left evoked", rightevoked: "Right evoked" };

  // The positional findings table: group -> rows -> the checkbox names behind each row.
  const POSITIONS = [
    { group: "Dix-Hallpike", rows: [
      { label: "Right", prefix: "dixhall-right-", keys: ["normal", "torsion", "latency", "fatigue", "atyp"] },
      { label: "Left", prefix: "dixhall-left-", keys: ["normal", "torsion", "latency", "fatigue", "atyp"] }] },
    { group: "Supine positional (roll test)", rows: [
      { label: "Head centre", prefix: "supine-center-", keys: ["normal", "rightbeat", "leftbeat", "upbeat", "downbeat", "atypical"] },
      { label: "Head right", prefix: "supine-right-", keys: ["normal", "geo", "ageo", "up", "down", "atypical"] },
      { label: "Head left", prefix: "supine-left-", keys: ["normal", "geo", "ageo", "up", "down", "atypical"] }] },
    { group: "Gaze", rows: [
      { label: "Gaze right", prefix: "gaze-right-", keys: ["normal", "rightevoked", "leftevoked"] },
      { label: "Gaze left", prefix: "gaze-left-", keys: ["normal", "rightevoked", "leftevoked"] }] },
  ];
  const IMPRESSIONS = ["imp-normal", "imp-rightpost", "imp-leftpost", "imp-rightlateral", "imp-leftlateral", "imp-rightanterior", "imp-leftanterior",
    "imp-rightcupu", "imp-leftcupu", "imp-rightnyst", "imp-leftnyst", "imp-atyp"];
  const CANAL_OF = { "imp-rightpost": ["right", "posterior"], "imp-leftpost": ["left", "posterior"], "imp-rightlateral": ["right", "lateral"],
    "imp-leftlateral": ["left", "lateral"], "imp-rightanterior": ["right", "anterior"], "imp-leftanterior": ["left", "anterior"],
    "imp-rightcupu": ["right", "lateral", "cupula"], "imp-leftcupu": ["left", "lateral", "cupula"] };
  const CANAL_NAME = { posterior: "Posterior canal", lateral: "Lateral canal", anterior: "Anterior canal" };

  const on = (v) => v === true || v === "Yes" || v === "On" || v === "true" || v === 1 || v === "1";
  const text = (v) => (v == null ? "" : String(v)).replace(/\r/g, "\n").trim();
  const label = (name) => (schemaFields && schemaFields[name] && schemaFields[name].label) || LABELS[name] || SUFFIX[name.split("-").pop()] || name;

  function latestNote(chart) {
    const notes = (chart && Array.isArray(chart.notes) ? chart.notes : []).filter(n => n && n.path);
    if (!notes.length) return null;
    const finals = notes.filter(n => n.kind === "final");
    const pool = finals.length ? finals : notes;
    return pool.slice().sort((a, b) => String(b.dos || "").localeCompare(String(a.dos || "")) || String(b.name || "").localeCompare(String(a.name || "")))[0];
  }

  // What the impression says about each ear's canals.
  function readImpression(v) {
    const ears = { right: { posterior: false, lateral: false, anterior: false, cupula: false, tags: [] }, left: { posterior: false, lateral: false, anterior: false, cupula: false, tags: [] } };
    const other = [], ticked = IMPRESSIONS.filter(k => on(v[k]));
    for (const k of ticked) {
      const c = CANAL_OF[k];
      if (c) { ears[c[0]][c[1]] = true; if (c[2]) ears[c[0]].cupula = true; ears[c[0]].tags.push(k); }
      else if (k !== "imp-normal") other.push(k);
    }
    return { ears, other, ticked, normal: on(v["imp-normal"]), any: ticked.length > 0 };
  }

  function recorded(v) {
    for (const g of POSITIONS) for (const r of g.rows) for (const k of r.keys) if (on(v[r.prefix + k])) return true;
    return IMPRESSIONS.some(k => on(v[k])) || !!(text(v["goggle-treatment-x"]) || text(v["goggle-dr"]) || text(v["goggle-date"]) || on(v["see-epley"]));
  }

  // ------------------------------------------------------------------ the canal map (inline SVG)
  function earSvg(side, ear) {
    const X = (x) => side === "right" ? x : W - x, R = (a) => side === "right" ? a : -a;
    const canal = (name, cx, cy, rx, ry, rot) => {
      const lit = !!ear[name];
      const attrs = `cx="${X(cx)}" cy="${cy}" rx="${rx}" ry="${ry}" transform="rotate(${R(rot)} ${X(cx)} ${cy})"`;
      return (lit ? `<ellipse class="vt-glow" ${attrs}/>` : "") + `<ellipse class="vt-canal${lit ? " on" : ""}" ${attrs}><title>${CANAL_NAME[name]}${lit ? " — implicated" : ""}</title></ellipse>`;
    };
    const lbl = (name, x, y, t) => `<text class="vt-lbl${ear[name] ? " on" : ""}" x="${X(x)}" y="${y}" text-anchor="middle">${t}</text>`;
    const cochlea = side === "right"
      ? "M 146 112 C 184 102, 200 138, 174 146 C 154 152, 148 128, 164 124 C 176 121, 180 134, 170 136"
      : "M 104 112 C 66 102, 50 138, 76 146 C 96 152, 102 128, 86 124 C 74 121, 70 134, 80 136";
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${side} inner ear canal map">
      ${canal("posterior", 100, 76, 33, 29, 28)}${canal("anterior", 148, 74, 33, 29, -28)}${canal("lateral", 80, 114, 40, 15, 0)}
      <ellipse class="vt-utricle" cx="${X(125)}" cy="104" rx="23" ry="16"/>
      ${ear.cupula ? `<circle class="vt-ampulla" cx="${X(103)}" cy="102" r="5.5"><title>Cupula of the lateral canal</title></circle>` : ""}
      <path class="vt-cochlea" d="${cochlea}"/>
      ${lbl("anterior", 170, 38, "Anterior")}${lbl("posterior", 76, 40, "Posterior")}${lbl("lateral", 58, 150, "Lateral")}
      <text class="vt-lbl" x="${X(125)}" y="140" text-anchor="middle">Utricle</text>
      <text class="vt-lbl" x="${X(178)}" y="168" text-anchor="middle">Cochlea</text>
    </svg>`;
  }

  function canalMapCard(h, imp, isRecorded) {
    const ear = (side) => {
      const e = imp.ears[side], lit = e.posterior || e.lateral || e.anterior;
      const tags = e.tags.map(k => h("span", { class: "tag blue" }, label(k)));
      return h("div", { class: "vt-ear" + (lit ? " lit" : "") },
        h("div", { class: "vt-ear-title" }, side === "right" ? "Right ear" : "Left ear"),
        h("div", { html: earSvg(side, e) }),
        h("div", { class: "vt-status" }, tags.length ? tags : h("span", { class: "muted" }, imp.normal ? "No canal implicated" : (isRecorded ? "Nothing implicated" : "Not tested"))));
    };
    const legend = h("div", { class: "vt-legend" },
      h("span", {}, h("i", { class: "vt-sw on" }), "Implicated canal"),
      h("span", {}, h("i", { class: "vt-sw" }), "Not implicated"),
      imp.ears.right.cupula || imp.ears.left.cupula ? h("span", {}, h("i", { class: "vt-sw dot" }), "Cupulolithiasis (otoconia on the cupula)") : null);
    const headTag = imp.normal ? h("span", { class: "tag green" }, "Normal study") : (!isRecorded ? h("span", { class: "tag" }, "Not recorded") : null);
    return h("section", { class: "card twothirds vt-map" },
      h("div", { class: "card-head" }, h("h2", {}, "Canal map"), headTag, h("span", { class: "spacer" }), legend),
      h("div", { class: "vt-ears" }, ear("right"), ear("left")),
      imp.other.length ? h("div", { class: "vt-other" }, h("span", { class: "muted" }, "Also noted "), imp.other.map(k => h("span", { class: "tag " + (k === "imp-atyp" ? "amber" : "blue") }, label(k)))) : null,
      h("div", { class: "vt-foot muted" }, "Right and left ears as the clinician faces the patient. Cupulolithiasis is mapped to the lateral canal cupula, as elicited by the supine roll test."));
  }

  // ------------------------------------------------------------------ positional findings
  function hintFor(row, v, all) {
    const t = (k) => on(v[row.prefix + k]);
    if (row.prefix.startsWith("dixhall")) {
      if (t("atyp")) return "Atypical response — consider anterior canal or a central cause.";
      if (t("torsion") && (t("latency") || t("fatigue"))) return "Torsional with latency or fatigue — classic posterior canal canalithiasis, this side.";
      if (t("torsion")) return "Torsional without latency or fatigue — consider cupulolithiasis.";
      if (t("latency") || t("fatigue")) return "Latent or fatigable response.";
      return "";
    }
    if (row.prefix.startsWith("supine-center")) {
      if (t("downbeat")) return "Down-beat supine — anterior canal vs central; check for torsion.";
      if (t("upbeat")) return "Upbeat supine — posterior canal if torsional, otherwise consider central.";
      if (t("rightbeat") || t("leftbeat")) return "Horizontal nystagmus head-centre — compare with head right and head left.";
      return t("atypical") ? "Atypical response." : "";
    }
    if (row.prefix.startsWith("supine")) {
      if (t("geo")) return "Geotropic — lateral canal canalithiasis; the stronger side is the affected ear.";
      if (t("ageo")) return "Ageotropic — lateral canal cupulolithiasis; the weaker side is the affected ear.";
      if (t("down")) return "Down-beat — anterior canal vs central.";
      if (t("up")) return "Upbeat — posterior canal if torsional, otherwise consider central.";
      return t("atypical") ? "Atypical response." : "";
    }
    if (row.prefix.startsWith("gaze")) {
      const rr = on(v["gaze-right-rightevoked"]), ll = on(v["gaze-left-leftevoked"]);
      if (rr && ll) return "Direction-changing gaze-evoked nystagmus — central pattern.";
      if (t("rightevoked") || t("leftevoked")) return "Unidirectional gaze-evoked — a peripheral pattern is more likely (Alexander's law).";
    }
    return "";
  }

  function findingsCard(h, v, isRecorded) {
    const body = [];
    for (const g of POSITIONS) {
      body.push(h("tr", { class: "vt-group" }, h("td", { colspan: 2 }, g.group)));
      for (const r of g.rows) {
        const ticked = r.keys.filter(k => on(v[r.prefix + k]));
        const chips = ticked.map(k => h("span", { class: "tag " + (k === "normal" ? "green" : (k === "atyp" || k === "atypical" ? "amber" : "blue")) }, label(r.prefix + k)));
        const hint = hintFor(r, v);
        body.push(h("tr", {}, h("td", { class: "vt-pos" }, r.label),
          h("td", {}, h("div", { class: "chips" }, chips.length ? chips : h("span", { class: "muted" }, isRecorded ? "Not tested" : "—")), hint ? h("div", { class: "vt-hint" }, hint) : null)));
      }
    }
    return h("section", { class: "card half vt-findings" },
      h("div", { class: "card-head" }, h("h2", {}, "Positional findings"), h("span", { class: "spacer" }), h("span", { class: "muted" }, "IR video goggles")),
      h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", {}, "Position"), h("th", {}, "Findings"))), h("tbody", {}, body)));
  }

  // ------------------------------------------------------------------ impression, treatment, who and when
  function impressionCard(h, ctx, v, imp, note, snapshot) {
    const fmt = ctx.fmt;
    const when = text(v["goggle-date"]) || text(v["Date"]) || (note && note.dos) || "";
    const who = text(v["goggle-dr"]) || "";
    const treatment = text(v["goggle-treatment-x"]);
    const kv = (k, val) => h("div", { class: "vt-kv" }, h("span", {}, k), h("b", {}, val || "—"));
    return h("section", { class: "card third vt-imp" },
      h("div", { class: "card-head" }, h("h2", {}, "Study impression"), h("span", { class: "spacer" }),
        note ? h("button", { class: "chip small", title: note.name || "", onclick: () => ctx.go("chart", { sub: "note", path: note.path }) }, "Open note") : null),
      h("div", { class: "chips" }, imp.ticked.length
        ? imp.ticked.map(k => h("span", { class: "tag " + (k === "imp-normal" ? "green" : k === "imp-atyp" ? "amber" : "blue") }, label(k)))
        : h("span", { class: "muted" }, "No impression recorded")),
      h("div", { class: "vt-sub" }, "Treatment"),
      treatment ? h("div", { class: "vt-treat" }, treatment) : h("div", { class: "muted" }, "No treatment recorded"),
      on(v["see-epley"]) ? h("div", { class: "vt-epley" }, h("span", { class: "tag blue" }, label("see-epley"))) : null,
      h("div", { class: "vt-kvs" },
        kv("Performed by", who || (snapshot && snapshot.attending ? `${snapshot.attending} (attending)` : "")),
        kv("Date", when ? fmt.short(when) : ""),
        kv("Source", note ? `Note of ${fmt.short(note.dos || when)}` : "")));
  }

  // ------------------------------------------------------------------ results timeline
  function kindOf(name) {
    const n = String(name || "");
    if (/\b(cdp|posturograph)/i.test(n)) return ["CDP", "blue"];
    if (/\b(vng|eng|nystagmograph|caloric)/i.test(n)) return ["VNG", "blue"];
    if (/\bvemp/i.test(n)) return ["VEMP", "blue"];
    if (/\b(audio|hearing|abr|tympan)/i.test(n)) return ["Audiogram", "blue"];
    if (/\b(vhit|head impulse)/i.test(n)) return ["vHIT", "blue"];
    if (/\b(mri|mra|ct|cta|imaging|x-?ray)\b/i.test(n)) return ["Imaging", ""];
    if (/\b(emg|ncv|ncs)\b/i.test(n)) return ["EMG/NCV", ""];
    if (/\b(eeg|zeto)\b/i.test(n)) return ["EEG", ""];
    return ["Study", ""];
  }
  function composite(result) { const m = /composite(?:\s+score)?\s*(?:of|=|:|is)?\s*(\d{1,3})\b/i.exec(result || ""); const n = m ? +m[1] : NaN; return n >= 0 && n <= 100 ? n : null; }

  function timelineCard(h, ctx, snapshot) {
    const fmt = ctx.fmt;
    const studies = (snapshot && Array.isArray(snapshot.studies) ? snapshot.studies : []).filter(s => s && text(s.name));
    const rows = studies.map(s => ({ ...s, d: fmt.parse(s.date) })).sort((a, b) => (b.d ? b.d.getTime() : 0) - (a.d ? a.d.getTime() : 0));
    const vest = rows.filter(r => kindOf(r.name)[1] === "blue").length;
    const items = rows.map(r => {
      const [kind, tone] = kindOf(r.name), score = kind === "CDP" ? composite(r.result) : null;
      return h("li", {},
        h("div", { class: "vt-date" }, r.d ? fmt.short(r.d) : (text(r.date) || "Undated")),
        h("div", { class: "vt-dot" + (tone ? " on" : "") }),
        h("div", { class: "vt-item" },
          h("div", { class: "row vt-item-head" }, h("span", { class: "tag " + tone }, kind), h("b", {}, text(r.name))),
          score != null ? h("div", { class: "vt-score" }, h("b", {}, String(score)), h("span", { class: "muted" }, "/ 100 composite"), h("div", { class: "vt-bar" }, h("i", { style: { width: score + "%" } }))) : null,
          text(r.result) ? h("div", { class: "vt-result" }, text(r.result)) : h("div", { class: "muted" }, "No result recorded")));
    });
    return h("section", { class: "card half vt-tl-card" },
      h("div", { class: "card-head" }, h("h2", {}, "Results timeline"), h("span", { class: "spacer" }),
        rows.length ? h("span", { class: "muted" }, `${vest} vestibular · ${rows.length} total`) : null),
      rows.length ? h("ul", { class: "vt-tl" }, items)
        : h("div", { class: "empty vt-empty-sm" }, h("div", { class: "empty-title" }, "No studies on file"), h("div", { class: "muted" }, "CDP, VNG, VEMP and audiogram results entered in the note's studies section appear here.")));
  }

  // ------------------------------------------------------------------ reports on file
  // EMG/NCV, EEG, VNG, VEMP and chair reports linked to this patient. Each opens read-only in Chart review.
  // A Zeto EEG is told apart from other EEGs by its folder or file name; the rest keep the drive's kind.
  function reportLabel(d) {
    if (d.kind === "EEG") return /zeto/i.test(`${d.folder || ""} ${d.path || ""} ${d.name || ""}`) ? "Zeto EEG" : "EEG";
    return d.kind === "Chair report" ? "Chair" : (d.kind || "Study");
  }
  function mdy(s) { const m = /(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})/.exec(s || ""); if (!m) return null; let y = +m[3]; if (y < 100) y += 2000; const d = new Date(y, +m[1] - 1, +m[2]); return isNaN(d) ? null : d; }
  function lastSeen(chart) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String((chart && chart.last_seen) || ""));
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
    const dos = ((chart && chart.notes) || []).map(n => n.dos).filter(Boolean).sort().pop();
    const k = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(dos || ""));
    return k ? new Date(+k[1], +k[2] - 1, +k[3]) : null;
  }
  let reportFilter = "";                      // the kind chip picked last time (Zeto EEG, VNG, …), kept across patients
  function reportsCard(h, ctx) {
    const docs = ((ctx.chart && ctx.chart.documents) || []).filter(d => d.group === "Neuro testing" || d.group === "Vestibular testing");
    if (!docs.length) return null;
    const since = lastSeen(ctx.chart);
    const isNew = (d) => { const when = mdy(d.modified); return !!(since && when && when > since); };
    const open = (d) => {
      const r = DDOS.modules.review;
      if (r && r.open) r.open(d.path);                     // remembered until Chart review mounts
      if (window.NoteWriter) NoteWriter.showTab("chart:review");
    };
    // one click: into the open note's next empty study row, with the report's impression, and over to the Studies part
    const addToNote = (d) => { if (!window.NoteWriter || !NoteWriter.addStudy) return; NoteWriter.addStudy(d); NoteWriter.showTab("studies"); };
    const kinds = [...new Set(docs.map(reportLabel))];
    if (reportFilter && !kinds.includes(reportFilter)) reportFilter = "";
    const list = h("div", { class: "on-file-list" });
    const chips = h("div", { class: "chips vt-report-chips" });
    const paint = () => {
      list.innerHTML = "";
      const shown = docs.filter(d => !reportFilter || reportLabel(d) === reportFilter).sort((a, b) => (isNew(b) ? 1 : 0) - (isNew(a) ? 1 : 0));
      for (const d of shown) list.append(h("div", { class: "on-file-row" + (isNew(d) ? " is-new" : "") },
        h("span", { class: "tag" + (d.group === "Vestibular testing" ? " blue" : "") }, reportLabel(d)),
        h("span", { class: "on-file-name", title: d.name }, isNew(d) ? h("span", { class: "tag orange on-file-new", title: "Filed since the last visit" }, "New since last visit") : null, d.name),
        h("span", { class: "muted on-file-date" }, d.modified || ""),
        h("span", { class: "on-file-acts" },
          d.path ? h("button", { class: "btn", type: "button", onclick: () => open(d) }, "Open") : h("span", { class: "muted" }, "No file"),
          window.NoteWriter && NoteWriter.addStudy ? h("button", { class: "btn", type: "button", title: "Put it in the open note's next empty study row with the report's impression", onclick: () => addToNote(d) }, "Add to studies") : null)));
      if (!shown.length) list.append(h("div", { class: "muted", style: { padding: "8px 10px" } }, `No ${reportFilter} report on file.`));
      chips.innerHTML = "";
      if (kinds.length > 1) for (const k of ["", ...kinds]) {
        const n = k ? docs.filter(d => reportLabel(d) === k).length : docs.length;
        chips.append(h("button", { class: "chip small" + ((reportFilter || "") === k ? " on" : ""), type: "button", onclick: () => { reportFilter = k; paint(); } }, k || "All", h("small", {}, ` ${n}`)));
      }
    };
    paint();
    const fresh = docs.filter(isNew).length;
    return h("section", { class: "card vt-reports-card" },
      h("div", { class: "card-head" }, h("h2", {}, "Reports on file"), h("span", { class: "spacer" }),
        h("span", { class: "muted" }, `${docs.length} report${docs.length > 1 ? "s" : ""}${fresh ? ` · ${fresh} new since the last visit` : ""}`)),
      kinds.length > 1 ? chips : null, list);
  }

  // ------------------------------------------------------------------ interpretation guide
  function guideCard(h) {
    const row = (a, b) => h("tr", {}, h("td", {}, a), h("td", {}, b));
    return h("section", { class: "card vt-guide-card" },
      h("div", { class: "card-head" }, h("h2", {}, "Interpretation guide"), h("span", { class: "spacer" }), h("span", { class: "muted" }, "Quick reference for the clinician")),
      h("div", { class: "vt-guide" },
        h("div", {}, h("h3", {}, "Supine roll · geotropic vs ageotropic"),
          h("p", {}, h("b", {}, "Geotropic"), " (beats toward the ground in both head positions): lateral canal canalithiasis. The side with the stronger nystagmus is the affected ear."),
          h("p", {}, h("b", {}, "Ageotropic"), " (beats away from the ground): lateral canal cupulolithiasis or anterior-arm canalithiasis. The side with the weaker nystagmus is the affected ear."),
          h("p", {}, "Persistent direction-fixed horizontal nystagmus that does not change with head position is not positional — look for a spontaneous nystagmus.")),
        h("div", {}, h("h3", {}, "Dix-Hallpike · latency and fatigability"),
          h("p", {}, h("b", {}, "Canalithiasis"), ": latency of 1–15 s, crescendo–decrescendo, under 60 s, fatigable on repetition, reverses on sitting up. Posterior canal beats upward with torsion toward the down ear."),
          h("p", {}, h("b", {}, "Cupulolithiasis"), ": little or no latency, persists beyond 60 s, does not fatigue."),
          h("p", {}, h("b", {}, "Think central"), " when nystagmus is pure down-beat without torsion, gaze-evoked and direction-changing, or does not fit a single canal — consider imaging.")),
        h("div", {}, h("h3", {}, "Which maneuver treats which canal"),
          h("table", { class: "vt-mini" }, h("tbody", {},
            row("Posterior", "Epley (canalith repositioning) or Semont, toward the affected side"),
            row("Lateral · geotropic", "Lempert 360° barbecue roll away from the affected ear, or Gufoni (lie to the unaffected side, nose down 45°)"),
            row("Lateral · ageotropic", "Gufoni ageotropic variant (lie to the affected side, nose up 45°), or head-shaking to convert to geotropic, then treat as above"),
            row("Anterior", "Yacovino deep head-hanging maneuver, or reverse Epley"))),
          h("p", { class: "muted vt-small" }, "Afterwards: post-maneuver precautions per the plan and re-test at follow-up."))));
  }

  // ------------------------------------------------------------------ screen
  function recordBtn(ctx, extra) {
    return ctx.h("button", { class: "btn primary" + (extra ? " " + extra : ""), onclick: () => ctx.go("chart", { sub: "note", params: { tab: "testing" } }) }, "Record today's testing in the note");
  }

  async function mount(el, ctx) {
    const my = ++gen, h = ctx.h;
    el.classList.add("vt");
    const note = latestNote(ctx.chart);
    if (!note) {
      el.append(h("div", { class: "cards" },
        h("section", { class: "card" }, h("div", { class: "empty" },
          h("div", { class: "empty-title" }, "No vestibular testing on file"),
          h("div", { class: "muted" }, "Positional testing lives in the progress note's IR oculography section. Once a note is filed for this patient it appears here as a canal map, findings and a results timeline."),
          h("div", { style: { marginTop: "14px" } }, recordBtn(ctx)))),
        reportsCard(h, ctx), guideCard(h)));
      return;
    }
    el.append(h("div", { class: "loading" }, h("div", { class: "spinner" }), "Reading the latest note…"));
    let values = {};
    try {
      const [res, schema] = await Promise.all([
        ctx.api.get("/api/note?path=" + encodeURIComponent(note.path)),
        schemaFields ? Promise.resolve(null) : ctx.api.get("/api/schema").catch(() => null),
      ]);
      if (schema && schema.fields) schemaFields = schema.fields;
      if (my !== gen) return;
      if (res && res.error) throw new Error(res.error);
      values = (res && res.values) || {};
    } catch (e) {
      if (my !== gen) return;
      el.innerHTML = "";
      el.append(h("div", { class: "empty" }, h("div", { class: "empty-title" }, "Couldn't read the latest note"), h("div", { class: "muted" }, String(e.message || e))));
      return;
    }
    el.innerHTML = "";
    const imp = readImpression(values), isRecorded = recorded(values);
    const wrap = h("div", { class: "cards" });
    wrap.append(h("div", { class: "vt-actions" }, recordBtn(ctx)));
    if (!isRecorded) wrap.append(h("div", { class: "vt-notice" },
      h("b", {}, "No positional testing recorded"), ` in the note of ${ctx.fmt.short(note.dos || values.Date)}. `, "The map stays neutral until goggles findings are entered."));
    wrap.append(canalMapCard(h, imp, isRecorded), impressionCard(h, ctx, values, imp, note, ctx.snapshot),
      findingsCard(h, values, isRecorded), timelineCard(h, ctx, ctx.snapshot), reportsCard(h, ctx), guideCard(h));
    el.append(wrap);
  }

  function unmount() { gen++; }

  DDOS.register("testing", { title: "Vestibular Testing", mount, unmount });
})();
