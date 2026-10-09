/* Local users, groups and server-enforced permissions. Administrators only. */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  DDOS.register("users", {
    async mount(host, ctx) {
      const { api, h, toast } = ctx;
      host.innerHTML = `<div class="view-scroll"><div class="users-page"><div class="day-head"><div><h1>Users & groups</h1><p>Local accounts and the permissions they have in this installation.</p></div></div><div class="loading"><div class="spinner"></div>Loading accounts…</div></div></div>`;
      const page = host.querySelector(".users-page");
      let state;
      try { state = await api.get("/api/users"); }
      catch (e) { page.innerHTML += `<div class="empty"><h2>Users unavailable</h2><p>${esc(e.message)}</p></div>`; return; }

      const rerender = () => this.mount(host, ctx);
      const checks = (names, selected, cls = "group-checks") => h("div", { class: cls }, names.map((name) =>
        h("label", {}, h("input", { type: "checkbox", value: name, checked: selected.includes(name) }), name)));
      const picked = (box) => [...box.querySelectorAll('input[type="checkbox"]:checked')].map((x) => x.value);
      const post = async (body, success) => {
        try { await api.post("/api/users", body); toast(success); await rerender(); }
        catch (e) { toast(e.message || "Not saved."); }
      };

      page.innerHTML = "";
      page.append(h("div", { class: "day-head" }, h("div", {}, h("h1", {}, "Users & groups"), h("p", {}, "Accounts are stored on this computer. Passwords are salted and hashed; permissions are checked by the local server."))));
      const grid = h("div", { class: "users-grid" });

      const usersCard = h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Users")));
      const userList = h("div", { class: "admin-list" });
      const groupNames = state.groups.map((g) => g.name);
      for (const user of state.users) {
        const groupBox = checks(groupNames, user.groups || []);
        const active = h("input", { type: "checkbox", checked: user.active });
        const password = h("input", { type: "password", placeholder: "New password (optional)", autocomplete: "new-password" });
        const save = h("button", { class: "btn", type: "button", onclick: () => post({ action: "update_user", id: user.id, active: active.checked,
          groups: picked(groupBox), ...(password.value ? { password: password.value, must_change: false } : {}) }, `${user.username} updated.`) }, "Save");
        userList.append(h("div", { class: "admin-row" },
          h("div", { class: "admin-row-head" }, h("b", {}, user.display_name || user.username), h("span", { class: "mono muted" }, user.username),
            user.must_change ? h("span", { class: "tag orange" }, "Password change due") : null, h("span", { class: "spacer" }),
            h("label", { class: "row" }, active, "Active")),
          h("div", { class: "admin-meta" }, (user.permissions || []).includes("*") ? "Full access" : `${(user.permissions || []).length} permissions`),
          groupBox, h("div", { class: "row" }, password, h("span", { class: "spacer" }), save)));
      }
      usersCard.append(userList);

      const create = h("form", { class: "admin-form", onsubmit: (e) => {
        e.preventDefault();
        const groupBox = create.querySelector(".group-checks");
        post({ action: "create_user", username: create.elements.username.value, display_name: create.elements.display_name.value,
          password: create.elements.password.value, groups: picked(groupBox), active: true, must_change: false }, "User created.");
      } },
      h("label", {}, "Username", h("input", { name: "username", required: true, autocomplete: "off", placeholder: "jdoe" })),
      h("label", {}, "Display name", h("input", { name: "display_name", required: true, autocomplete: "off", placeholder: "Jordan Doe" })),
      h("label", { class: "wide" }, "Temporary password", h("input", { name: "password", type: "password", minlength: 8, required: true, autocomplete: "new-password" })),
      h("div", { class: "wide" }, h("div", { class: "admin-meta" }, "Groups"), checks(groupNames, ["Front Desk"])),
      h("div", { class: "wide row" }, h("span", { class: "muted" }, "Passwords can be replaced from the user row at any time."), h("span", { class: "spacer" }), h("button", { class: "btn primary", type: "submit" }, "Create user")));
      usersCard.append(h("div", { class: "subhead" }, "Add user"), create);

      const groupsCard = h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Groups & permissions")));
      for (const group of state.groups) {
        const permissionChoices = group.name === "Administrators" ? ["*", ...state.permissions] : state.permissions;
        const permBox = checks(permissionChoices, group.permissions || [], "permission-checks");
        const full = permBox.querySelector('input[value="*"]');
        if (full) { full.disabled = true; full.closest("label").title = "Administrators always have full access"; }
        const desc = h("input", { value: group.description || "", placeholder: "What this group is for" });
        groupsCard.append(h("div", { class: "group-card" }, h("h3", {}, group.name), desc, permBox,
          h("div", { class: "row", style: { marginTop: "8px" } }, h("span", { class: "spacer" }),
            h("button", { class: "btn", type: "button", onclick: () => post({ action: "update_group", id: group.id, description: desc.value, permissions: picked(permBox) }, `${group.name} updated.`) }, "Save group"))));
      }
      const newGroup = h("form", { class: "admin-form", onsubmit: (e) => {
        e.preventDefault(); const box = newGroup.querySelector(".permission-checks");
        post({ action: "create_group", name: newGroup.elements.name.value, description: newGroup.elements.description.value, permissions: picked(box) }, "Group created.");
      } }, h("label", {}, "Group name", h("input", { name: "name", required: true })),
      h("label", {}, "Description", h("input", { name: "description" })),
      h("div", { class: "wide" }, checks(state.permissions, [], "permission-checks")),
      h("div", { class: "wide row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", type: "submit" }, "Create group")));
      groupsCard.append(h("div", { class: "subhead" }, "Add group"), newGroup);
      grid.append(usersCard, groupsCard); page.append(grid);
    },
  });
})();
