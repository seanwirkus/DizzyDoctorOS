/* Settings: an Apple-Settings-style page of grouped cards.
 *
 * Reads GET /api/status for what this build shows (version, dictation, model, notes_root, demo, clinic, initials,
 * classic, platform, synced, bookings) and writes POST /api/settings. Each card saves on its own and only sends the
 * fields you changed, so a blank field never overwrites a value this build doesn't report back. The reply carries
 * every saved setting, and the cards pick those up.
 */
(() => {
  const MODELS = [
    { id: "tiny.en", name: "Tiny", size: "~75 MB", note: "Fastest and lightest; misses more clinical words. For a slow PC." },
    { id: "base.en", name: "Base", size: "~140 MB", note: "Quick, with noticeably better words than Tiny." },
    { id: "small.en", name: "Small", size: "~460 MB", note: "The default: accurate on clinical terms, a few seconds per sentence." },
    { id: "medium.en", name: "Medium", size: "~1.5 GB", note: "Most accurate and several times slower; wants a fast CPU." },
  ];
  const CLASSIC_TOOLS = [
    ["Billing requests", "Requests that arrive from the billing team in Teams, triaged and answered with the chart open."],
    ["Superbill fixes", "Corrections to superbills before they go out: codes, modifiers, missing pages."],
    ["MIPS workbooks", "The quality-measure workbooks for the reporting year, filled from the schedule."],
  ];
  const CHECK = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6.5l2.5 2.5 4.5-5"/></svg>';

  const cleanPath = (s) => String(s == null ? "" : s).trim().replace(/^["']+|["']+$/g, "").trim();
  const asList = (v) => Array.isArray(v) ? v : (v ? [v] : []);
  const lines = (s) => { const out = []; for (const l of String(s || "").split(/\r?\n/)) { const p = cleanPath(l); if (p && !out.includes(p)) out.push(p); } return out; };
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const clock = () => new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const isWin = (p) => String(p || "").startsWith("win");
  const platformName = (p) => p === "darwin" ? "macOS" : isWin(p) ? "Windows" : p ? "Linux" : "";
  const modelDir = (p) => isWin(p) ? "%LOCALAPPDATA%\\DizzyDoctor\\whisper" : "~/.local/share/DizzyDoctor/whisper";

  let teardown = [];
  const later = (fn) => teardown.push(fn);

  DDOS.register("settings", {
    title: "Settings",

    async mount(el, ctx) {
      const { h, toast, api } = ctx;
      let alive = true; later(() => { alive = false; });

      // The boot copy of status is already here; a fresh one picks up anything saved since.
      let status = { ...(ctx.status || {}) };
      try { const fresh = await api.get("/api/status"); if (fresh && !fresh.error) status = { ...status, ...fresh }; } catch (e) { /* the boot copy will do */ }
      if (!alive) return;

      const cards = [];
      const root = h("div", { class: "settings" });
      root.append(h("div", { class: "day-head" }, h("div", {}, h("h1", {}, "Settings"), h("p", {}, "How this computer charts, dictates and files notes."))));

      // ---------------------------------------------------------------- building blocks
      function makeField(key, o) {
        const f = { key, initial: o.initial, read: o.read, write: o.write, validate: o.validate || (() => ""), inputs: o.inputs || [], errorEl: null, card: null };
        f.value = () => f.read();
        f.changed = () => !same(f.value(), f.initial);
        f.setError = (msg) => { if (f.errorEl) { f.errorEl.textContent = msg || ""; f.errorEl.hidden = !msg; } for (const i of f.inputs) i.classList.toggle("invalid", !!msg); };
        // A value the server reports back: it becomes the baseline, and shows unless you were mid-edit elsewhere.
        f.accept = (v, force) => { const dirty = f.changed(); f.initial = v; if (force || !dirty) { f.write(v); f.setError(""); } };
        return f;
      }

      function makeCard(id, title, intro) {
        const rows = h("div", { class: "settings-rows" });
        const state = h("span", { class: "settings-state muted" });
        const card = { id, title, fields: [], rows, state, saveBtn: null, busy: false };
        card.el = h("section", { class: "card settings-card", id: "settings-" + id },
          h("div", { class: "card-head" }, h("div", { class: "settings-head" }, h("h2", {}, title), intro ? h("p", { class: "muted" }, intro) : null)),
          rows);
        card.row = (label, help, control, field) => {
          const err = h("div", { class: "settings-error", hidden: true });
          rows.append(h("div", { class: "settings-row" },
            h("div", { class: "settings-row-text" }, h("b", {}, label), help ? h("div", { class: "muted" }, help) : null),
            h("div", { class: "settings-row-control" }, control, err)));
          if (field) { field.errorEl = err; field.card = card; card.fields.push(field); }
          return card;
        };
        card.setState = (text, tone) => { state.textContent = text || ""; state.className = "settings-state " + (tone === "ok" ? "tone-green" : tone === "bad" ? "tone-red" : tone === "warn" ? "tone-amber" : "muted"); };
        card.refresh = () => {
          const dirty = card.fields.some(f => f.changed());
          if (card.saveBtn) card.saveBtn.disabled = card.busy || !dirty;
          if (dirty) card.setState("Unsaved changes", "warn"); else if (!card.savedAt) card.setState("");
        };
        card.foot = (...extra) => {
          card.saveBtn = card.fields.length ? h("button", { class: "btn primary", disabled: true, onclick: () => save(card) }, "Save") : null;
          card.el.append(h("div", { class: "settings-foot" }, extra, h("span", { class: "spacer" }), state, card.saveBtn));
          return card;
        };
        cards.push(card);
        return card;
      }

      const input = (f, attrs = {}) => {
        const i = h("input", { type: "text", class: "settings-input", autocomplete: "off", spellcheck: "false", ...attrs, oninput: () => { f.setError(""); f.card && f.card.refresh(); } });
        f.inputs.push(i); return i;
      };
      const textarea = (f, attrs = {}) => {
        const t = h("textarea", { class: "settings-input mono", rows: 3, spellcheck: "false", ...attrs, oninput: () => { f.setError(""); f.card && f.card.refresh(); } });
        f.inputs.push(t); return t;
      };
      const value = (...kids) => h("div", { class: "settings-value" }, kids);
      const tag = (text, tone) => h("span", { class: "tag " + (tone || "") }, text);

      // ---------------------------------------------------------------- saving
      async function save(card) {
        if (card.busy) return;
        const payload = {}; let bad = false;
        for (const f of card.fields) {
          f.setError("");
          if (!f.changed()) continue;
          const v = f.value(); const err = f.validate(v);
          if (err) { f.setError(err); bad = true; } else payload[f.key] = v;
        }
        if (bad) { const first = card.el.querySelector(".invalid"); first && first.focus(); toast("Fix the highlighted field, then save again."); return; }
        if (!Object.keys(payload).length) return;
        card.busy = true; card.saveBtn.textContent = "Saving…"; card.refresh();
        let r;
        try { r = await api.post("/api/settings", payload); } catch (e) { r = { error: "Couldn't reach the app (" + (e.message || e) + ")." }; }
        card.busy = false; card.saveBtn.textContent = "Save";
        if (!alive) return;
        if (!r || r.error || !r.ok) { card.setState((r && r.error) || "Not saved.", "bad"); toast((r && r.error) || "Settings weren't saved."); card.refresh(); return; }
        applySettings(r.settings || payload, card);
        card.savedAt = clock(); card.setState("Saved " + card.savedAt, "ok");
        toast(card.title + " saved.");
      }

      function applySettings(settings, savedCard) {
        for (const c of cards) for (const f of c.fields) if (f.key in settings && settings[f.key] != null) f.accept(settings[f.key], c === savedCard);
        for (const c of cards) c.refresh();
        const S = ctx.state && ctx.state.status;
        if (S) {
          if (settings.initials != null) S.initials = settings.initials;
          if (settings.dictation_model) S.model = settings.dictation_model;
          const folders = asList(settings.notes_folders); if (folders.length) S.notes_root = folders[0];
        }
        const who = document.getElementById("topUser"); if (who && settings.initials != null) who.textContent = settings.initials;
        if (settings.dictation_model) renderModels();
        ctx.emit("settings:changed", settings);
      }

      // ---------------------------------------------------------------- Charting
      {
        const card = makeCard("charting", "Charting", "Where this computer files notes, and how they're signed.");

        const folders = makeField("notes_folders", { initial: status.notes_root ? [status.notes_root] : [], read: () => lines(folders.inputs[0].value), write: (v) => { folders.inputs[0].value = asList(v).join("\n"); },
          validate: (v) => v.length ? "" : "Enter at least one folder path, one per line." });
        const ta = textarea(folders, { placeholder: "V:\\1111 Progress Notes" }); folders.write(folders.initial);
        card.row("Progress notes folders", "One path per line. Signed notes are filed in the first folder, in a subfolder for the year; the others are searched when a chart is opened.", ta, folders);

        const drafts = makeField("prepped_notes_folder", { initial: "", read: () => cleanPath(drafts.inputs[0].value), write: (v) => { drafts.inputs[0].value = v || ""; },
          validate: (v) => v ? "" : "Enter a folder path for drafts." });
        card.row("Drafts folder", "Notes in progress are kept here until they're signed. Blank means the app's own “Prepped Notes” folder.", input(drafts, { class: "settings-input mono", placeholder: "Prepped Notes (app data folder)" }), drafts);

        const tmpl = makeField("note_template", { initial: "", read: () => cleanPath(tmpl.inputs[0].value), write: (v) => { tmpl.inputs[0].value = v || ""; },
          validate: (v) => !v || /\.pdf$/i.test(v) ? "" : "The blank form is a PDF (the path should end in .pdf)." });
        card.row("Blank form override", "Only if the office revises the progress-note form: the path to the newer blank PDF. Leave blank to use the form built into the app.", input(tmpl, { class: "settings-input mono", placeholder: "Built-in form" }), tmpl);

        const initials = makeField("initials", { initial: status.initials || "", read: () => initials.inputs[0].value.trim().toUpperCase(), write: (v) => { initials.inputs[0].value = v || ""; },
          validate: (v) => !v ? "Enter your initials; they go on the notes you sign." : v.length > 8 ? "Keep initials short (up to 8 characters)." : "" });
        const ini = input(initials, { class: "settings-input short", maxlength: 8, placeholder: "IMP", "aria-label": "Your initials" }); initials.write(initials.initial);
        ini.addEventListener("blur", () => { ini.value = ini.value.trim().toUpperCase(); });
        card.row("Your initials", "Shown in the top bar and written into the notes and billing you sign.", ini, initials);

        const dir = makeField("practice_directory", { initial: status.practice_directory || "", read: () => dir.inputs[0].value.replace(/\s+$/, ""), write: (v) => { dir.inputs[0].value = v || ""; } });
        const dirBox = textarea(dir, { placeholder: "Blank: the form prints its own directory" }); dir.write(dir.initial);
        card.row("Practice directory (left margin)", "Leave blank to keep the directory the form prints. Fill it in only when the practice's providers change; it then replaces the left margin on every note filed from this computer.", dirBox, dir);

        const timer = makeField("visit_timer", { initial: !!status.visit_timer, read: () => timer.on, write: (v) => { timer.on = !!v; tsw.setAttribute("aria-checked", String(timer.on)); } });
        timer.on = timer.initial;
        const tsw = h("button", { type: "button", class: "settings-switch", role: "switch", "aria-checked": String(timer.on), "aria-label": "Visit timer",
          onclick: () => { timer.write(!timer.on); card.refresh(); } });
        card.row("Visit timer", "A clock on each note, with Use time to put its minutes in Total time.", h("div", { class: "row" }, tsw, h("span", { class: "muted" }, "Off unless you turn it on")), timer);

        card.foot(h("span", { class: "settings-hint muted" }, "Blank fields keep whatever is saved on this computer."));
        root.append(card.el);
      }

      // ---------------------------------------------------------------- Dictation
      let renderModels = () => {};
      {
        const card = makeCard("dictation", "Dictation", "Speech to note, transcribed on this computer.");
        const installed = !!status.dictation;
        card.row("Speech engine", "Whisper, running on this computer. Audio never leaves the PC.",
          value(tag(installed ? "Installed" : "Not installed", installed ? "green" : "amber"), h("span", { class: "muted" }, installed ? "Press F9 or Dictate on a note." : "Install faster-whisper on this computer to dictate.")));

        const model = makeField("dictation_model", { initial: status.model || "small.en", read: () => model.picked, write: (v) => { model.picked = v; renderModels(); } });
        model.picked = model.initial;
        const list = h("div", { class: "settings-options", role: "radiogroup", "aria-label": "Dictation model" });
        renderModels = () => {
          list.innerHTML = "";
          for (const m of MODELS) list.append(h("button", { type: "button", class: "settings-option", role: "radio", "aria-checked": String(model.picked === m.id),
            onclick: () => { model.picked = m.id; renderModels(); card.refresh(); } },
            h("span", { class: "settings-check", html: CHECK }),
            h("span", { class: "settings-option-text" }, h("b", {}, m.name, " ", h("span", { class: "mono settings-option-id" }, m.id), m.id === model.initial ? tag("In use", "blue") : null), h("small", {}, m.note)),
            h("span", { class: "settings-option-size mono" }, m.size)));
        };
        renderModels();
        card.row("Model", "Bigger models hear clinical vocabulary better and take longer per sentence. The model you pick downloads the first time you dictate.", list, model);

        card.row("Model cache", "Downloaded models live in the app's data folder. Delete the folder to free the space; the next dictation downloads again.",
          value(h("span", { class: "mono" }, modelDir(status.platform))));
        card.foot(h("span", { class: "settings-hint muted" }, "If you've already dictated since the app opened, a new model is used after the next restart."));
        root.append(card.el);
      }

      // ---------------------------------------------------------------- Clinic data
      {
        const card = makeCard("clinic", "Clinic data", "Which clinic this computer opens, and where its schedule comes from.");
        card.row("Clinic", "The practice whose patients, notes and calendar this app shows.", value(h("b", {}, status.clinic || "Not set"), status.demo ? h("span", { class: "muted" }, " · demo") : null));

        const demo = makeField("demo_mode", { initial: !!status.demo, read: () => demo.on, write: (v) => { demo.on = !!v; sw.setAttribute("aria-checked", String(demo.on)); } });
        demo.on = demo.initial;
        const sw = h("button", { type: "button", class: "settings-switch", role: "switch", "aria-checked": String(demo.on), "aria-label": "Demo clinic",
          onclick: () => { demo.write(!demo.on); card.refresh(); } });
        card.row("Demo clinic", "Five made-up patients with generated notes and a pretend schedule. Nothing in the demo is a real person, and nothing you do there touches the practice's files. Turn it off to open the practice's own data the next time the app starts.",
          h("div", { class: "row" }, sw, h("span", { class: "muted" }, "Made-up patients only")), demo);

        const synced = status.synced || "";
        const bookings = status.bookings || 0;
        card.row("Outlook calendar", "The schedule and visit history come from the Outlook calendar cache kept by the classic app.",
          value(status.demo ? tag("Not used", "") : synced ? tag("Synced", "green") : tag("Not synced", "amber"),
            h("span", { class: "muted" }, status.demo ? "The demo clinic has its own pretend schedule." : synced ? `Last sync ${synced}${bookings ? ` · ${bookings.toLocaleString()} bookings on file` : ""}` : "Open the classic tools and run a calendar sync to fill the schedule.")));
        card.foot(h("span", { class: "settings-hint muted" }, "Switching clinics applies the next time the app starts."));
        root.append(card.el);
      }

      // ---------------------------------------------------------------- Classic tools
      {
        const card = makeCard("classic", "Classic tools", "The parts of the practice workflow that still run in the classic window.");
        for (const [name, what] of CLASSIC_TOOLS) card.row(name, what, value(tag("Classic", "")));
        const can = !!status.classic;
        const open = h("button", { class: "btn primary", disabled: !can, onclick: async () => {
          open.disabled = true;
          let r; try { r = await api.post("/api/classic", { tab: "settings" }); } catch (e) { r = { error: "Couldn't reach the app." }; }
          open.disabled = !can;
          toast((r && r.error) || "Opening the classic tools…");
        } }, "Open classic tools");
        card.foot(h("span", { class: "settings-hint muted" }, can ? "Opens in its own window; this one stays where it is." : "Not available in this build."), open);
        root.append(card.el);
      }

      // ---------------------------------------------------------------- About
      {
        const card = makeCard("about", "About");
        const version = status.version ? "v" + status.version : "development build";
        card.row("Version", null, value(h("span", { class: "mono" }, version), platformName(status.platform) ? h("span", { class: "muted" }, ` · ${platformName(status.platform)}`) : null));
        card.row("What this is", null, value("DizzyDoctor fills in the clinic's progress-note PDF. Choose a patient, work through the steps, then file the note."));
        card.row("Your data", null, value("Nothing is sent anywhere. Notes, the patient directory and the Outlook cache are read from the practice's folders, and dictation runs on this PC."));
        card.row("Who to call", null, value("A wrong or missing patient: the front desk. Anything in this app that looks broken: whoever set up this computer, with the version above and what you were doing."));
        card.row("Shortcuts", null, value(h("span", { class: "settings-kbd" }, h("kbd", {}, isWin(status.platform) ? "Ctrl" : "Cmd"), " ", h("kbd", {}, "K"), " find a patient"), h("span", { class: "settings-kbd" }, h("kbd", {}, "F9"), " dictate"), h("span", { class: "settings-kbd" }, h("kbd", {}, isWin(status.platform) ? "Ctrl" : "Cmd"), " ", h("kbd", {}, "S"), " save the card you're in")));
        const copy = h("button", { class: "btn", onclick: async () => {
          const text = [`DizzyDoctor ${version}`, `Platform: ${status.platform || "?"}`, `Clinic: ${status.clinic || "?"}${status.demo ? " (demo)" : ""}`, `Dictation: ${status.dictation ? "installed" : "not installed"}, model ${status.model || "?"}`, `Notes root: ${status.notes_root || "?"}`].join("\n");
          try { await navigator.clipboard.writeText(text); toast("Details copied."); } catch (e) { toast("Couldn't copy; the details are in the About card."); }
        } }, "Copy details");
        card.foot(h("span", { class: "settings-hint muted" }, "Paste the details into a message when you report a problem."), copy);
        root.append(card.el);
      }

      el.append(root);
      for (const c of cards) c.refresh();

      // Cmd/Ctrl+S saves the card the cursor is in.
      const onKey = (e) => {
        if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "s") return;
        const c = cards.find(c => c.saveBtn && c.el.contains(document.activeElement));
        if (c) { e.preventDefault(); save(c); }
      };
      document.addEventListener("keydown", onKey); later(() => document.removeEventListener("keydown", onKey));
    },

    unmount() {
      for (const fn of teardown.splice(0)) { try { fn(); } catch (e) { console.error(e); } }
      const tools = document.getElementById("headTools"); if (tools) tools.innerHTML = "";
    },
  });
})();
