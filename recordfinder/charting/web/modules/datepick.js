/* Dates: type them any common way ("8/15/26", "081526", "Aug 15 2026", "today") or pick from the calendar.
   DDOS.dates.normalize() turns typing into mm/dd/yyyy; DDOS.dates.attach() puts the calendar button on a field. */
(() => {
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (d) => `${pad(d.getMonth() + 1)}/${pad(d.getDate())}/${d.getFullYear()}`;
  const sameDay = (a, b) => a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

  function make(y, m, d) {
    const dt = new Date(y, m - 1, d);
    return dt.getFullYear() === y && dt.getMonth() === m - 1 && dt.getDate() === d ? dt : null;
  }
  // Two-digit years: a birth date is in the past (58 is 1958); any other date is within ten years ahead (30 is 2030,
  // 98 is 1998).
  function year(y, dob) {
    y = +y;
    if (y >= 100) return y;
    const now = new Date().getFullYear();
    return dob ? (2000 + y > now ? 1900 + y : 2000 + y) : (2000 + y > now + 10 ? 1900 + y : 2000 + y);
  }
  function monthOf(word) {
    const w = word.toLowerCase().replace(/\.$/, "");
    const i = MONTHS.findIndex(m => m.toLowerCase().startsWith(w.slice(0, 3)));
    return w.length >= 3 && i >= 0 ? i + 1 : 0;
  }
  // mm/dd/yyyy for anything that is plainly a date; "" for blank; null when it isn't one.
  function parse(raw, { dob = false } = {}) {
    const s = String(raw || "").trim();
    if (!s) return "";
    const low = s.toLowerCase(), today = new Date();
    if (/^(t|tod|today|now)$/.test(low)) return fmt(today);
    if (/^(yesterday)$/.test(low)) return fmt(new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1));
    let m = /^([+-])(\d{1,3})\s*([dwmy])$/i.exec(low);          // +2w, -3d, +6m
    if (m) {
      const n = (m[1] === "-" ? -1 : 1) * +m[2], d = new Date(today);
      if (m[3] === "d") d.setDate(d.getDate() + n); else if (m[3] === "w") d.setDate(d.getDate() + 7 * n);
      else if (m[3] === "m") d.setMonth(d.getMonth() + n); else d.setFullYear(d.getFullYear() + n);
      return fmt(d);
    }
    let d = null;
    if ((m = /^(\d{1,2})\s*[\/\-. ]\s*(\d{1,2})\s*[\/\-. ]\s*(\d{2}|\d{4})$/.exec(s))) d = make(year(m[3], dob), +m[1], +m[2]);
    else if ((m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s))) d = make(+m[1], +m[2], +m[3]);
    else if ((m = /^(\d{2})(\d{2})(\d{4}|\d{2})$/.exec(s))) d = make(year(m[3], dob), +m[1], +m[2]);
    else if ((m = /^([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4}|\d{2})$/i.exec(s)) && monthOf(m[1])) d = make(year(m[3], dob), monthOf(m[1]), +m[2]);
    else if ((m = /^(\d{1,2})\s+([a-z]{3,9})\.?,?\s+(\d{4}|\d{2})$/i.exec(s)) && monthOf(m[2])) d = make(year(m[3], dob), monthOf(m[2]), +m[1]);
    if (!d) return null;
    if (dob && d > today) return null;
    return fmt(d);
  }

  // ---------------------------------------------------------------- the calendar
  let pop = null, state = null;
  function el(tag, attrs = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else if (v != null && v !== false) e.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) if (k != null) e.append(k.nodeType ? k : document.createTextNode(k));
    return e;
  }
  function close() {
    if (pop) pop.hidden = true;
    document.removeEventListener("mousedown", outside, true);
    if (state && state.input) state.input.setAttribute("aria-expanded", "false");
    state = null;
  }
  function outside(e) { if (pop && !pop.contains(e.target) && !(state && (e.target === state.input || e.target === state.button || state.button.contains(e.target)))) close(); }
  function paint() {
    const { view, chosen, dob } = state, today = new Date();
    pop.innerHTML = "";
    const y0 = dob ? 1900 : today.getFullYear() - 30, y1 = dob ? today.getFullYear() : today.getFullYear() + 5;
    const month = el("select", { "aria-label": "Month", onchange: () => { state.view = new Date(state.view.getFullYear(), +month.value, 1); state.keyboard = false; paint(); pop.querySelector('select[aria-label="Month"]').focus(); } },
      MONTHS.map((m, i) => el("option", { value: i, selected: i === view.getMonth() }, m)));
    const years = [];
    for (let y = y1; y >= y0; y--) years.push(el("option", { value: y, selected: y === view.getFullYear() }, String(y)));
    if (view.getFullYear() < y0 || view.getFullYear() > y1) years.push(el("option", { value: view.getFullYear(), selected: true }, String(view.getFullYear())));
    const yr = el("select", { "aria-label": "Year", onchange: () => { state.view = new Date(+yr.value, state.view.getMonth(), 1); state.keyboard = false; paint(); pop.querySelector('select[aria-label="Year"]').focus(); } }, years);
    const step = (n) => { state.view = new Date(state.view.getFullYear(), state.view.getMonth() + n, 1); paint(); };
    pop.append(el("div", { class: "dp-head" },
      el("button", { type: "button", class: "dp-nav", "aria-label": "Previous month", onclick: () => step(-1) }, "‹"), month, yr,
      el("button", { type: "button", class: "dp-nav", "aria-label": "Next month", onclick: () => step(1) }, "›")));
    const grid = el("div", { class: "dp-grid", role: "grid" }, ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map(d => el("div", { class: "dp-dow" }, d)));
    const first = new Date(view.getFullYear(), view.getMonth(), 1), start = new Date(first);
    start.setDate(1 - first.getDay());
    for (let i = 0; i < 42; i++) {
      const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
      const cls = ["dp-day", d.getMonth() !== view.getMonth() ? "out" : "", sameDay(d, today) ? "today" : "", sameDay(d, chosen) ? "on" : "", sameDay(d, state.focus) ? "focus" : ""].filter(Boolean).join(" ");
      grid.append(el("button", { type: "button", class: cls, tabindex: sameDay(d, state.focus) ? "0" : "-1", "aria-label": d.toDateString(), disabled: dob && d > today ? true : null, onclick: () => pick(d) }, String(d.getDate())));
    }
    pop.append(grid);
    pop.append(el("div", { class: "dp-foot" },
      el("button", { type: "button", class: "tool", onclick: () => pick(new Date()) }, "Today"),
      el("span", { class: "spacer" }),
      el("button", { type: "button", class: "tool", onclick: () => { state.onPick(""); close(); } }, "Clear")));
    const f = pop.querySelector(".dp-day.focus");
    if (f && state.keyboard) f.focus();
  }
  function pick(d) { const cb = state.onPick, input = state.input; cb(fmt(d)); close(); if (input) input.focus(); }
  function open(input, button, { dob = false, onPick, keyboard = false }) {
    if (!pop) {
      pop = el("div", { class: "datepop", role: "dialog", "aria-label": "Choose a date" });
      pop.hidden = true;
      document.body.append(pop);
      pop.addEventListener("keydown", (e) => {
        if (!state) return;
        const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
        const onDay = e.target.classList && e.target.classList.contains("dp-day");
        if (e.key === "Escape") { e.preventDefault(); const i = state.input; close(); i && i.focus(); return; }
        if (!onDay) return;
        if (e.key in moves) { e.preventDefault(); const f = state.focus; state.focus = new Date(f.getFullYear(), f.getMonth(), f.getDate() + moves[e.key]); state.view = new Date(state.focus.getFullYear(), state.focus.getMonth(), 1); state.keyboard = true; paint(); }
        else if (e.key === "PageUp" || e.key === "PageDown") { e.preventDefault(); const n = e.key === "PageUp" ? -1 : 1; const f = state.focus; state.focus = new Date(f.getFullYear(), f.getMonth() + n, Math.min(f.getDate(), 28)); state.view = new Date(state.focus.getFullYear(), state.focus.getMonth(), 1); state.keyboard = true; paint(); }
        else if (e.key === "Enter" && document.activeElement.classList.contains("dp-day")) { e.preventDefault(); pick(state.focus); }
        else if (e.key === "Escape") { e.preventDefault(); const i = state.input; close(); i && i.focus(); }
      });
    }
    if (state && state.input === input) { close(); return; }
    const cur = parse(input.value, { dob });
    const chosen = cur ? (([mm, dd, yy]) => new Date(+yy, +mm - 1, +dd))(cur.split("/")) : null;
    const focus = chosen || (dob ? new Date(1960, 0, 1) : new Date());
    state = { input, button, dob, onPick, chosen, focus, view: new Date(focus.getFullYear(), focus.getMonth(), 1), keyboard };
    input.setAttribute("aria-expanded", "true");
    paint();
    pop.hidden = false;
    const r = input.getBoundingClientRect(), w = 264, hgt = pop.offsetHeight || 300;
    pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
    // below the field when it fits (or there's more room below); never over the field itself on a short window
    const below = innerHeight - r.bottom - 12, above = r.top - 12, down = below >= hgt || below >= above;
    pop.style.maxHeight = Math.max(180, down ? below : above) + "px";
    pop.style.overflowY = "auto";
    pop.style.top = (down ? r.bottom + 6 : Math.max(8, r.top - 6 - Math.min(hgt, above))) + "px";
    if (keyboard) { const d = pop.querySelector(".dp-day.focus"); if (d) d.focus({ preventScroll: true }); }
    setTimeout(() => document.addEventListener("mousedown", outside, true), 0);
  }

  window.DDOS = window.DDOS || {};
  DDOS.dates = { parse, format: fmt, open, close, MONTHS };
})();
