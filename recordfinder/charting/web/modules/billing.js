/* Visit coding from the open progress note. Charge submission stays in Classic tools. */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function mount(host, ctx) {
    const patient = ctx && ctx.patient;
    const c = window.NoteWriter && NoteWriter.coding;
    if (!patient) {
      host.innerHTML = `<div class="view-scroll"><div class="empty"><h2>No visit to code</h2><p>Open a patient to see the visit level and diagnoses from the progress note. The billing worklist, superbills, and charge entry stay in Classic tools. Nothing here submits a charge.</p><button type="button" class="btn primary" data-classic>Open classic tools</button></div></div>`;
      return;
    }
    let body;
    if (!c || c.error || !Array.isArray(c.warnings)) {
      body = `<p class="fine">Coding is calculated from the open progress note. Open Note and enter the visit. This does not submit a charge.</p>`;
    } else if (c.em) {
      const icd = (c.icd || []).map((i) => `<div class="kv"><span>${esc(i.code || "—")}</span><b>${esc(i.impression)}</b></div>`).join("") || `<p class="fine">No diagnoses yet.</p>`;
      const warns = (c.warnings || []).slice(0, 6).map((w) => `<div class="warn">${esc(w)}</div>`).join("");
      body = `<div class="bill-code">${esc(c.em.code)}${c.em.modifier ? "-" + esc(c.em.modifier) : ""}</div><p class="fine">${esc(c.em.title || "")} · ${esc(c.minutes || 0)} min. ${esc(c.em.why || "")}</p>${icd}${warns}`;
    } else {
      body = `<p class="fine">${esc(c.next_level || "Enter the total encounter time on the note to suggest a visit level.")}</p>`;
    }
    host.innerHTML = `<div class="stack"><div class="card"><div class="card-head"><h2>This visit</h2></div>${body}
      <p class="fine" style="margin-top:12px">Suggestions follow the clinic playbook. The provider and billing decide what goes on the superbill. Full billing opens in Classic tools.</p>
      <div class="row" style="margin-top:10px"><button type="button" class="btn" id="jumpNoteBilling">Billing fields on the note</button><button type="button" class="btn primary" data-classic>Open classic tools</button></div>
    </div></div>`;
  }

  DDOS.register("billing", { mount });
})();
