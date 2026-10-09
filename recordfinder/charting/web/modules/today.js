/* Provider calendar: day, week and month ranges with persistent provider filters. */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const day = (s) => { const [y, m, d] = String(s).slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
  const add = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const monday = (d) => add(d, -((d.getDay() + 6) % 7));
  const same = (a, b) => iso(a) === iso(b);
  const today = () => { const x = new Date(); return new Date(x.getFullYear(), x.getMonth(), x.getDate()); };
  const label = (d, opts) => d.toLocaleDateString(undefined, opts);
  const time = (s) => { const d = new Date(s); return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); };

  let mounted = false;
  DDOS.register("today", {
    async mount(host, ctx) {
      mounted = true;
      const { api, openChart } = ctx;
      let mode = localStorage.getItem("ddos-calendar-view") || "day";
      if (!["day", "week", "month"].includes(mode)) mode = "day";
      let anchor = today(), data = { providers: [], rows: [] }, selected = null, query = "", filter = "all";
      host.innerHTML = `<div class="view-scroll"><div class="today calendar-page"><div class="loading"><div class="spinner"></div>Loading calendar…</div></div></div>`;
      const root = host.querySelector(".today");

      function parseCsv(text) {
        const table = []; let row = [], cell = "", quoted = false;
        for (let i = 0; i <= text.length; i++) {
          const c = text[i] || "\n";
          if (quoted && c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
          else if (c === '"') quoted = !quoted;
          else if (!quoted && c === ",") { row.push(cell); cell = ""; }
          else if (!quoted && (c === "\n" || c === "\r")) {
            if (c === "\r" && text[i + 1] === "\n") i++;
            row.push(cell); cell = ""; if (row.some((x) => x.trim())) table.push(row); row = [];
          } else cell += c;
        }
        if (table.length < 2) return [];
        const heads = table[0].map((x) => x.trim());
        return table.slice(1).map((cells) => Object.fromEntries(heads.map((head, i) => [head, (cells[i] || "").trim()])));
      }
      function batchDialog() {
        const modal = document.createElement("div"); modal.className = "modal batch-modal"; modal.setAttribute("role", "dialog"); modal.setAttribute("aria-modal", "true");
        const providerSet = selected || new Set(data.providers.map((p) => p.id));
        modal.innerHTML = `<div class="modal-box batch-box"><div class="batch-head"><div><h2>Create superbill batches</h2><p>Create one draft superbill per scheduled patient, grouped into a PDF batch for each provider.</p></div><button class="icon-btn" data-close aria-label="Close">${DDOS.icon("x")}</button></div>
          <label class="batch-label">Date<input type="date" data-batch-date value="${iso(mode === "day" ? anchor : today())}"></label>
          <fieldset><legend>Providers</legend><div class="batch-providers">${data.providers.map((p) => `<label><input type="checkbox" value="${esc(p.id)}" ${providerSet.has(p.id) ? "checked" : ""}><i style="--provider:${esc(p.color)}"></i>${esc(p.name)}</label>`).join("")}</div></fieldset>
          <label class="batch-upload"><span>${DDOS.icon("upload")} Optional patient-information file</span><input type="file" accept=".csv,.json,text/csv,application/json"><small>CSV or JSON stays on this computer. Account number is the strongest match; name plus DOB is next.</small></label>
          <div class="batch-file muted">No file selected — superbills will start from the schedule, chart, and same-day note.</div>
          <div class="batch-result" hidden></div>
          <div class="row modal-actions"><button class="btn" data-close type="button">Cancel</button><button class="btn primary" data-create type="button">Create provider batches</button></div></div>`;
        const close = () => modal.remove();
        modal.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
        modal.addEventListener("mousedown", (e) => { if (e.target === modal) close(); });
        modal.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
        let uploaded = [];
        const input = modal.querySelector('input[type="file"]'), fileLine = modal.querySelector(".batch-file");
        input.addEventListener("change", async () => {
          const file = input.files && input.files[0]; uploaded = [];
          if (!file) { fileLine.textContent = "No file selected."; return; }
          try {
            const text = await file.text();
            const parsed = file.name.toLowerCase().endsWith(".json") ? JSON.parse(text) : parseCsv(text);
            uploaded = Array.isArray(parsed) ? parsed : (Array.isArray(parsed.patients) ? parsed.patients : []);
            fileLine.textContent = `${file.name} · ${uploaded.length} patient row${uploaded.length === 1 ? "" : "s"} ready to match`;
          } catch (e) { fileLine.textContent = `Could not read ${file.name}: ${e.message}`; }
        });
        modal.querySelector("[data-create]").addEventListener("click", async (e) => {
          const button = e.currentTarget, result = modal.querySelector(".batch-result");
          const providers = [...modal.querySelectorAll(".batch-providers input:checked")].map((x) => x.value);
          if (!providers.length) { result.hidden = false; result.innerHTML = '<div class="warn">Choose at least one provider.</div>'; return; }
          button.disabled = true; button.textContent = "Creating…"; result.hidden = false; result.innerHTML = '<div class="loading"><span class="spinner"></span>Matching patients and building PDFs…</div>';
          try {
            const r = await api.post("/api/superbill/batch", { date: modal.querySelector("[data-batch-date]").value, providers, rows: uploaded });
            const batches = (r.batches || []).map((b, i) => `<div class="batch-result-row"><div><b>${esc(b.provider)}</b><span>${b.count} superbill${b.count === 1 ? "" : "s"} · ${b.ready} ready to file</span></div><button class="btn" type="button" data-open="${i}">Open PDF batch</button></div>`).join("");
            const unmatched = (r.unmatched || []).length ? `<details><summary>${r.unmatched.length} uploaded row${r.unmatched.length === 1 ? "" : "s"} not matched</summary><div class="batch-unmatched">${r.unmatched.slice(0, 30).map((x) => esc(x.name || x.patient_name || [x.last_name || x.last, x.first_name || x.first].filter(Boolean).join(", ") || "Unnamed row")).join("<br>")}</div></details>` : "";
            result.innerHTML = batches || '<div class="warn">No scheduled patients were found for those providers on this date.</div>';
            result.insertAdjacentHTML("beforeend", unmatched);
            result.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => api.post("/api/open", { path: r.batches[+b.dataset.open].path }).catch((err) => { fileLine.textContent = err.message; })));
            button.textContent = "Recreate batches";
          } catch (err) { result.innerHTML = `<div class="warn">${esc(err.message)}</div>`; button.textContent = "Try again"; }
          finally { button.disabled = false; }
        });
        document.body.append(modal); setTimeout(() => modal.querySelector("[data-batch-date]").focus(), 0);
      }

      function range() {
        if (mode === "day") return [anchor, add(anchor, 1)];
        if (mode === "week") { const a = monday(anchor); return [a, add(a, 7)]; }
        const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1), a = monday(first);
        return [a, add(a, 42)];
      }
      function title() {
        const [a, b] = range();
        if (mode === "day") return label(a, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
        if (mode === "week") return `${label(a, { month: "short", day: "numeric" })} – ${label(add(b, -1), { month: "short", day: "numeric", year: "numeric" })}`;
        return label(anchor, { month: "long", year: "numeric" });
      }
      function move(n) {
        anchor = mode === "day" ? add(anchor, n) : mode === "week" ? add(anchor, n * 7) : new Date(anchor.getFullYear(), anchor.getMonth() + n, 1);
        load();
      }
      function visibleRows() {
        const chosen = selected || new Set(data.providers.map((p) => p.id));
        const q = query.trim().toLowerCase();
        return data.rows.filter((r) => chosen.has(r.cal) && (!q || `${r.name} ${r.cal} ${r.provider} ${r.visit} ${r.plan} ${r.kind_label || ""}`.toLowerCase().includes(q)) &&
          (filter === "all" || (filter === "patients" && r.patient) || (filter === "blocks" && !r.patient) || (filter === "duplicates" && r.duplicate)));
      }
      // The visit type (New patient, Follow-up, Procedure, Results, Telehealth, Urgent) in its colour, the same colour
      // the patient's tab and note header use. The provider's colour stays on the left edge.
      const kindChip = (r, compact = false) => r.kind ? `<span class="visit-kind${compact ? " compact" : ""}" style="--kind:${esc(r.kind_color || "#6b7280")}" title="${esc(r.kind_label || "")}${r.visit ? " · " + esc(r.visit) : ""}">${esc(compact ? "" : r.kind_short || r.kind_label || "")}</span>` : "";
      function event(r, compact = false) {
        const alerts = (r.alerts || []).slice(0, compact ? 1 : 3).map((a) => `<span class="calendar-alert ${esc(a.tone || "")}">${esc(a.text)}</span>`).join("");
        const dup = r.duplicate ? `<span class="duplicate-badge" title="Also booked on ${esc((r.duplicate_calendars || []).join(", "))}">Across ${r.duplicate_count} schedules</span>` : "";
        return `<button type="button" class="calendar-event${r.duplicate ? " duplicate" : ""}${r.patient ? " patient" : " block"}${r.kind ? " kind-" + esc(r.kind) : ""}" data-id="${esc(r.id)}" title="${esc(r.name || "")}${r.kind_label ? " · " + esc(r.kind_label) : ""}" style="--provider:${esc(r.color || "#6b7280")};--kind:${esc(r.kind_color || r.color || "#6b7280")}">
          <span class="calendar-event-time">${esc(time(r.start))}${compact ? kindChip(r, true) : ""}</span><span class="calendar-event-main"><b>${esc(r.name || "Untitled").split(" ").map((w) => `<span>${w}</span>`).join(" ")}</b>${compact ? "" : `<small>${kindChip(r)}${[r.provider, r.visit, r.plan].filter(Boolean).map((x, i, a) => `<span>${esc(x).replace(/ · /g, "&nbsp;· ")}${i < a.length - 1 ? "&nbsp;·" : ""}</span>`).join(" ")}</small>`}</span>${dup}${alerts}</button>`;
      }
      // The key to the colours, for the types booked in this range.
      function kindLegend(rows) {
        const types = (data.visit_types || []).filter((t) => rows.some((r) => r.kind === t.id));
        return types.length ? `<span class="kind-legend">${types.map((t) => `<span style="--kind:${esc(t.color)}" title="${esc(t.hint || "")}"><i></i>${esc(t.short)}</span>`).join("")}</span>` : "";
      }
      function bindEvents() {
        root.querySelectorAll(".calendar-event.patient").forEach((button) => button.addEventListener("click", () => {
          const r = data.rows.find((x) => String(x.id) === button.dataset.id);
          if (r) openChart({ id: r.patient_id || "", last: r.last, first: r.first, dob: r.dob, acct: r.acct, insurance: r.plan, complaint: r.visit, name: r.name,
                             visit_kind: r.kind || "", visit_label: r.kind_label || "" }, "note");
        }));
      }
      function dayView(rows) {
        const providers = data.providers.filter((p) => (selected || new Set(data.providers.map(x => x.id))).has(p.id));
        if (!data.providers.length) {
          // nothing booked in this range; on a computer without the Outlook schedule, say where it comes from
          const st = ctx.status || {};
          const why = !st.demo && !st.bookings ? "This computer has no schedule yet: it comes from the Outlook calendar sync on the clinic PCs (Settings, Clinic data)." : "Nothing is booked for any provider on this day.";
          return `<div class="empty"><h2>No appointments</h2><p>${esc(why)}</p></div>`;
        }
        if (!providers.length) return `<div class="empty"><h2>No providers selected</h2><p>Tick at least one provider above.</p></div>`;
        return `<div class="calendar-day" style="--provider-count:${providers.length}">${providers.map((p) => {
          const items = rows.filter((r) => r.cal === p.id).sort((a, b) => a.start.localeCompare(b.start));
          return `<section class="provider-column"><header style="--provider:${esc(p.color)}"><i></i><div><b>${esc(p.name)}</b><small>${esc(p.calendar)} · ${items.length}</small></div></header><div>${items.map((r) => event(r)).join("") || `<div class="calendar-empty">No appointments</div>`}</div></section>`;
        }).join("")}</div>`;
      }
      function weekView(rows) {
        const [a] = range();
        return `<div class="calendar-week">${Array.from({ length: 7 }, (_, i) => add(a, i)).map((d) => {
          const items = rows.filter((r) => r.date === iso(d)).sort((x, y) => x.start.localeCompare(y.start) || x.cal.localeCompare(y.cal));
          return `<section class="calendar-date${same(d, today()) ? " is-today" : ""}"><header><span>${label(d, { weekday: "short" })}</span><b>${d.getDate()}</b></header><div>${items.map((r) => event(r, true)).join("") || `<div class="calendar-empty">—</div>`}</div></section>`;
        }).join("")}</div>`;
      }
      function monthView(rows) {
        const [a] = range();
        return `<div class="calendar-month"><div class="month-weekdays">${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((x) => `<span>${x}</span>`).join("")}</div><div class="month-days">${Array.from({ length: 42 }, (_, i) => add(a, i)).map((d) => {
          const items = rows.filter((r) => r.date === iso(d)).sort((x, y) => x.start.localeCompare(y.start) || x.cal.localeCompare(y.cal));
          const outside = d.getMonth() !== anchor.getMonth();
          return `<section class="month-day${outside ? " outside" : ""}${same(d, today()) ? " is-today" : ""}"><header>${d.getDate()}</header><div>${items.slice(0, 5).map((r) => event(r, true)).join("")}${items.length > 5 ? `<span class="month-more">+${items.length - 5} more</span>` : ""}</div></section>`;
        }).join("")}</div></div>`;
      }
      function render() {
        if (!mounted) return;
        const rows = visibleRows(), duplicateCount = rows.filter((r) => r.duplicate).length;
        root.innerHTML = `<div class="calendar-toolbar"><div><h1>Calendar</h1><p>${esc(title())}</p></div><div class="calendar-controls">
          <button class="btn batch-launch" type="button" data-superbill-batch>${DDOS.icon("upload")}Superbill batch</button>
          <div class="seg calendar-modes">${["day", "week", "month"].map((m) => `<button type="button" data-mode="${m}" class="${mode === m ? "on" : ""}">${m[0].toUpperCase() + m.slice(1)}</button>`).join("")}</div>
          <div class="today-nav"><button class="btn" data-move="-1" aria-label="Previous">${DDOS.icon("left")}</button><button class="btn today-btn-today" data-today>Today</button><button class="btn" data-move="1" aria-label="Next">${DDOS.icon("right")}</button></div>
        </div></div>
        <div class="calendar-filters"><div class="provider-filters">${data.providers.map((p) => `<label><input type="checkbox" value="${esc(p.id)}" ${(selected || new Set(data.providers.map(x => x.id))).has(p.id) ? "checked" : ""}><i style="--provider:${esc(p.color)}"></i>${esc(p.name)}</label>`).join("")}</div>
          <span class="spacer"></span><input class="calendar-search" type="search" placeholder="Filter appointments" value="${esc(query)}"><select class="calendar-kind"><option value="all">Everything</option><option value="patients" ${filter === "patients" ? "selected" : ""}>Patients only</option><option value="blocks" ${filter === "blocks" ? "selected" : ""}>Blocks only</option><option value="duplicates" ${filter === "duplicates" ? "selected" : ""}>Across schedules</option></select>
        </div><div class="calendar-summary"><span>${rows.length} shown</span>${duplicateCount ? `<span class="duplicate-key"><i></i>${duplicateCount} cross-schedule copies</span>` : ""}${kindLegend(rows)}</div>
        <div class="calendar-surface">${mode === "day" ? dayView(rows) : mode === "week" ? weekView(rows) : monthView(rows)}</div>`;
        root.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => { mode = b.dataset.mode; localStorage.setItem("ddos-calendar-view", mode); load(); }));
        root.querySelector("[data-superbill-batch]").addEventListener("click", batchDialog);
        root.querySelectorAll("[data-move]").forEach((b) => b.addEventListener("click", () => move(+b.dataset.move)));
        root.querySelector("[data-today]").addEventListener("click", () => { anchor = today(); load(); });
        root.querySelectorAll(".provider-filters input").forEach((box) => box.addEventListener("change", () => {
          selected = new Set([...root.querySelectorAll(".provider-filters input:checked")].map((x) => x.value)); render();
        }));
        root.querySelector(".calendar-search").addEventListener("input", (e) => { query = e.target.value; render(); });
        root.querySelector(".calendar-kind").addEventListener("change", (e) => { filter = e.target.value; render(); });
        bindEvents();
      }
      async function load() {
        const [a, b] = range();
        root.innerHTML = `<div class="loading"><div class="spinner"></div>Loading ${esc(mode)} calendar…</div>`;
        try {
          data = await api.get(`/api/schedule?start=${iso(a)}&end=${iso(b)}`);
          if (selected == null) selected = new Set(data.providers.map((p) => p.id));
        } catch (e) { data = { providers: [], rows: [] }; root.innerHTML = `<div class="empty"><h2>Calendar unavailable</h2><p>${esc(e.message)}</p></div>`; return; }
        render();
      }
      await load();
    },
    unmount() { mounted = false; },
  });
})();
