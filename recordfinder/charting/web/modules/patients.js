/* Patient search, in the directory page and in the top-bar results. */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function button(r, onPick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "plist";
    const meta = [r.dob, r.acct ? "Pt # " + r.acct : "", r.last_visit ? "last " + r.last_visit : ""].filter(Boolean).join(" · ");
    b.innerHTML = `<span>${esc(r.last)}, ${esc(r.first)}</span><small>${esc(meta)}</small>`;
    b.addEventListener("click", () => onPick({ id: r.id, last: r.last, first: r.first, dob: r.dob, acct: r.acct, name: `${r.last}, ${r.first}` }));
    return b;
  }
  function fill(box, rows, onPick) {
    box.innerHTML = "";
    if (!rows.length) {
      const b = document.createElement("button");
      b.type = "button";
      b.disabled = true;
      b.className = "plist";
      b.textContent = "No matches";
      box.append(b);
      return;
    }
    for (const r of rows) box.append(button(r, onPick));
  }
  async function mount(host, ctx) {
    const api = ctx.api;
    const onPick = (p) => ctx.openChart(p, "note");
    host.innerHTML = `<div class="view-scroll"><div class="search-page">
      <div class="day-head"><div><h1>Patients</h1><p>The whole patient directory. Choosing someone opens their note and chart.</p></div></div>
      <input id="dirSearch" type="search" placeholder="Name, Pt #, or date of birth" autocomplete="off" spellcheck="false">
      <div id="dirResults"></div>
    </div></div>`;
    const input = host.querySelector("#dirSearch");
    const results = host.querySelector("#dirResults");
    let t;
    input.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        const q = input.value.trim();
        if (!q) { results.innerHTML = ""; return; }
        let rows = [];
        try { rows = await api.get("/api/patients?q=" + encodeURIComponent(q)); } catch { rows = []; }
        results.innerHTML = "";
        const card = document.createElement("div");
        card.style.background = "#fff";
        card.style.border = "1px solid var(--hair)";
        card.style.borderRadius = "14px";
        card.style.overflow = "hidden";
        fill(card, Array.isArray(rows) ? rows : [], onPick);
        results.append(card);
      }, 160);
    });
    input.dispatchEvent(new Event("input"));
    input.focus();
  }
  DDOS.register("patients", { mount, fill });
})();
