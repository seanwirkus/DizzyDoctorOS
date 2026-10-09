/* The body map: recognizable anterior and posterior figures whose regions are dermatomes, motor-root myotomes, or
   peripheral-nerve territories. Inline SVG, no dependencies; every region is a <path data-id="L5-R"> with a <title>.
   The catalog of ids, labels and colours is /static/dermatomes.json, shared with distributions.py. */
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const DDOS = window.DDOS = window.DDOS || { modules: {}, register(name, mod) { this.modules[name] = mod; } };
  const W = 220, H = 480, CX = 110;
  // the head: an ellipse, the same on every figure
  const HEAD = { cx: CX, cy: 46, rx: 30, ry: 36 };
  const hx = (y) => HEAD.rx * Math.sqrt(Math.max(0, 1 - ((y - HEAD.cy) / HEAD.ry) ** 2));   // half-width at a height
  const hy = (x) => HEAD.ry * Math.sqrt(Math.max(0, 1 - ((x - HEAD.cx) / HEAD.rx) ** 2));   // half-height at an x

  function svgEl(tag, attrs = {}) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) el.setAttribute(k, String(v));
    return el;
  }
  const f1 = (n) => (Math.round(n * 10) / 10).toString();
  const poly = (pts) => "M" + pts.map(([x, y]) => f1(x) + "," + f1(y)).join("L") + "Z";
  // mirror a path across the midline (x -> 220 - x), flipping arc sweep flags
  function mirror(d) {
    return d.replace(/([ML])(-?[\d.]+),(-?[\d.]+)/g, (_, c, x, y) => `${c}${f1(W - +x)},${y}`)
            .replace(/A([\d.]+),([\d.]+) 0 0 ([01]) (-?[\d.]+),(-?[\d.]+)/g, (_, rx, ry, sw, x, y) => `A${rx},${ry} 0 0 ${sw === "0" ? 1 : 0} ${f1(W - +x)},${y}`);
  }

  // ------------------------------------------------------------------ the head (viewer-left half; mirrored for the other side)
  // Front: the face in thirds (V1 brow and scalp, V2 cheek, V3 jaw). Back: occiput (greater occipital medially, lesser
  // occipital laterally), then C2 and C3 bands down the neck.
  const face = () => {
    const yEye = 46, yLip = 62, yChin = HEAD.cy + HEAD.ry;
    const xEye = CX - hx(yEye), xLip = CX - hx(yLip);
    return [
      ["V1", `M${CX},${f1(HEAD.cy - HEAD.ry)} A30,36 0 0 0 ${f1(xEye)},${yEye} L${CX},${yEye} Z`],
      ["V2", `M${CX},${yEye} L${f1(xEye)},${yEye} A30,36 0 0 0 ${f1(xLip)},${yLip} L${CX},${yLip} Z`],
      ["V3", `M${CX},${yLip} L${f1(xLip)},${yLip} A30,36 0 0 0 ${CX},${yChin} Z`],
    ];
  };
  const occiput = () => {
    const xG = 92, yG = HEAD.cy - hy(xG), yC2 = 60, yC3 = 76, xN = 97;
    return [
      ["GON", `M${CX},${f1(HEAD.cy - HEAD.ry)} A30,36 0 0 0 ${xG},${f1(yG)} L${xG},${yC2} L${CX},${yC2} Z`],
      ["LON", `M${xG},${f1(yG)} A30,36 0 0 0 ${f1(CX - hx(yC2))},${yC2} L${xG},${yC2} Z`],
      ["C2", `M${CX},${yC2} L${f1(CX - hx(yC2))},${yC2} A30,36 0 0 0 ${f1(CX - hx(yC3))},${yC3} L${CX},${yC3} Z`],
      ["C3", `M${CX},${yC3} L${f1(CX - hx(yC3))},${yC3} A30,36 0 0 0 ${xN},${f1(HEAD.cy + hy(xN))} L${xN},94 L${CX},94 Z`],
    ];
  };
  const neckFront = () => [["C3", `M${CX},78 L97,78 L97,94 L${CX},94 Z`]];
  const shoulder = () => [["C4", `M${CX},94 L97,94 L62,112 L${CX},112 Z`]];
  const EAR = { cx: 77, cy: 50, rx: 5.5, ry: 9.5 };

  // Anatomical contours and landmarks sit above the tiled territories. They make the figure read as a person rather
  // than a stack of rectangles without intercepting clicks on the clinical regions underneath.
  const OUTLINE = "M110,10 C92,10 80,24 80,46 C80,64 86,76 97,82 L97,94 C83,97 71,104 60,112 C50,119 47,139 44,160 L37,205 L29,282 L20,309 C15,324 18,340 28,341 C38,341 42,329 40,316 L47,286 L55,207 L63,161 L68,119 C72,151 73,187 74,213 C75,235 68,250 66,267 L68,312 L74,356 L77,401 L78,444 L78,472 C78,481 89,484 96,478 C101,472 99,459 99,444 L100,402 L105,356 L110,312 L110,268";
  const LANDMARKS = {
    front: [
      "M93,43 Q99,40 104,43", "M116,43 Q121,40 127,43", "M110,45 L108,55 L112,56", "M103,65 Q110,69 117,65",
      "M80,117 Q94,108 110,114 Q126,108 140,117", "M110,113 L110,247", "M82,142 Q96,134 108,143", "M112,143 Q124,134 138,142",
      "M106,195 Q110,198 114,195", "M75,245 Q91,254 110,252 Q129,254 145,245", "M74,356 Q87,350 100,356", "M120,356 Q133,350 146,356",
    ],
    back: [
      "M91,48 Q110,42 129,48", "M110,82 L110,259", "M77,125 Q92,111 106,130 Q92,148 78,147", "M143,125 Q128,111 114,130 Q128,148 142,147",
      "M79,225 Q94,235 110,230 Q126,235 141,225", "M75,245 Q91,254 110,252 Q129,254 145,245", "M74,356 Q87,350 100,356", "M120,356 Q133,350 146,356",
    ],
  };

  // ------------------------------------------------------------------ the trunk
  // half-width of the trunk at a height: shoulders 112, waist 205, hips 258, bottom 268
  const TRUNK = [[112, 48], [205, 36], [258, 44], [268, 44]];
  function trunkHalf(y) {
    for (let i = 1; i < TRUNK.length; i++) {
      const [y0, w0] = TRUNK[i - 1], [y1, w1] = TRUNK[i];
      if (y <= y1) return w0 + (w1 - w0) * (y - y0) / (y1 - y0);
    }
    return TRUNK[TRUNK.length - 1][1];
  }
  // bands down the viewer-left half of the trunk, [id, y0, y1]
  const band = (id, y0, y1) => [id, poly([[CX - trunkHalf(y0), y0], [CX, y0], [CX, y1], [CX - trunkHalf(y1), y1]])];
  function bands(list) {
    let y = 112; const out = [];
    for (const [id, h] of list) { out.push(band(id, y, y + h)); y += h; }
    return out;
  }
  const T = (h) => Array.from({ length: 11 }, (_, i) => [`T${i + 2}`, h]);
  const trunkFront = () => bands([...T(12.55), ["L1", 18]]);
  const trunkBack = () => [...bands([...T(11.27), ["L1", 11], ["L2", 10], ["L3", 11]]), ["S345", poly([[CX, 240], [CX - 10, 268], [CX, 268]])]];
  const trunkInert = () => [[null, poly([[CX - trunkHalf(112), 112], [CX, 112], [CX, 268], [CX - trunkHalf(268), 268], [CX - trunkHalf(258), 258], [CX - trunkHalf(205), 205]])]];

  // ------------------------------------------------------------------ limbs
  // A limb is a run of joints {x, y, w} (w = full width there). Each segment between joints is cut into strips across
  // its width, s = -1 (lateral, the outer edge) to +1 (medial, toward the midline). Joint cross-sections use the
  // bisector so neighbouring segments tile exactly.
  function limb(joints, stripsBySegment) {
    const dirs = [];
    for (let i = 0; i < joints.length - 1; i++) {
      const dx = joints[i + 1].x - joints[i].x, dy = joints[i + 1].y - joints[i].y, len = Math.hypot(dx, dy) || 1;
      dirs.push([dx / len, dy / len]);
    }
    const normals = joints.map((_, i) => {
      const a = dirs[Math.max(0, i - 1)], b = dirs[Math.min(dirs.length - 1, i)];
      const nx = (a[1] + b[1]) / 2, ny = -(a[0] + b[0]) / 2, len = Math.hypot(nx, ny) || 1;    // (dy, -dx): toward +x for a downward axis
      return [nx / len, ny / len];
    });
    const pt = (j, s) => [joints[j].x + normals[j][0] * s * joints[j].w / 2, joints[j].y + normals[j][1] * s * joints[j].w / 2];
    const out = [];
    stripsBySegment.forEach((strips, i) => {
      for (const [id, s0, s1] of strips) out.push([id, poly([pt(i, s0), pt(i, s1), pt(i + 1, s1), pt(i + 1, s0)])]);
    });
    return out;
  }
  // viewer-left arm and leg (the other side is mirrored); medial is +x here
  const ARM = [{ x: 60, y: 116, w: 22 }, { x: 52, y: 160, w: 20 }, { x: 46, y: 206, w: 18 }, { x: 36, y: 286, w: 14 }, { x: 31, y: 306, w: 22 }, { x: 26, y: 340, w: 18 }];
  const LEG = [{ x: 88, y: 262, w: 40 }, { x: 88, y: 312, w: 34 }, { x: 88, y: 356, w: 28 }, { x: 88, y: 400, w: 24 }, { x: 90, y: 444, w: 16 }, { x: 90, y: 472, w: 24 }];
  const all = (id) => [[id, -1, 1]];
  const hand3 = [["C6", -1, -0.33], ["C7", -0.33, 0.33], ["C8", 0.33, 1]];
  const LIMBS = {
    dermatome: {
      front: {
        arm: [[["C5", -1, 0.1], ["T2", 0.1, 1]], [["C5", -1, 0.1], ["T2", 0.1, 1]], [["C6", -1, 0.2], ["T1", 0.2, 1]], hand3, hand3],
        leg: [all("L2"), all("L3"), [["L5", -1, 0.1], ["L4", 0.1, 1]], [["L5", -1, 0.1], ["L4", 0.1, 1]], [["S1", -1, -0.4], ["L5", -0.4, 0.35], ["L4", 0.35, 1]]],
      },
      back: {
        arm: [[["C5", -1, -0.3], ["C7", -0.3, 0.3], ["T2", 0.3, 1]], [["C5", -1, -0.3], ["C7", -0.3, 0.3], ["T2", 0.3, 1]], [["C6", -1, -0.3], ["C7", -0.3, 0.3], ["C8", 0.3, 1]], hand3, hand3],
        leg: [[["S1", -1, -0.2], ["S2", -0.2, 1]], [["S1", -1, -0.2], ["S2", -0.2, 1]], [["L5", -1, -0.6], ["S1", -0.6, 0.3], ["S2", 0.3, 1]], [["L5", -1, -0.6], ["S1", -0.6, 0.3], ["S2", 0.3, 1]], all("S1")],
      },
    },
    peripheral: {
      front: {
        arm: [all(null), all(null), all(null), [["median", -1, 0.25], ["ulnar", 0.25, 1]], [["median", -1, 0.25], ["ulnar", 0.25, 1]]],
        leg: [[["LFCN", -1, -0.15], [null, -0.15, 1]], [["LFCN", -1, -0.15], [null, -0.15, 1]], [["peroneal", -1, 0.15], ["saphenous", 0.15, 1]], [["peroneal", -1, 0.15], ["saphenous", 0.15, 1]],
              [["sural", -1, -0.6], ["peroneal", -0.6, 0.45], ["saphenous", 0.45, 1]]],
      },
      back: {
        arm: [[[null, -1, -0.6], ["radial", -0.6, 0.3], [null, 0.3, 1]], [[null, -1, -0.6], ["radial", -0.6, 0.3], [null, 0.3, 1]], [["radial", -1, 0.3], [null, 0.3, 1]], [["radial", -1, 0.3], ["ulnar", 0.3, 1]], [["radial", -1, 0.3], ["ulnar", 0.3, 1]]],
        leg: [[["LFCN", -1, -0.75], [null, -0.75, 1]], [["LFCN", -1, -0.75], [null, -0.75, 1]], [["sural", -1, 0.4], ["saphenous", 0.4, 1]], [["sural", -1, 0.4], ["saphenous", 0.4, 1]], [["sural", -1, 0], [null, 0, 1]]],
      },
    },
    myotome: {
      front: {
        arm: [all("C5"), all("C6"), [["C6", -1, 0], ["C7", 0, 1]], [["C8", -1, 0.25], ["T1", 0.25, 1]], [["C8", -1, 0.25], ["T1", 0.25, 1]]],
        leg: [[["L1", -1, 0], ["L2", 0, 1]], all("L3"), [["L5", -1, 0], ["L4", 0, 1]], [["L5", -1, 0], ["S1", 0, 1]], all("S1")],
      },
      back: {
        arm: [all("C5"), [["C6", -1, 0], ["C7", 0, 1]], all("C7"), [["C8", -1, 0.25], ["T1", 0.25, 1]], [["C8", -1, 0.25], ["T1", 0.25, 1]]],
        leg: [[["L5", -1, 0], ["S2", 0, 1]], [["S1", -1, 0], ["S2", 0, 1]], [["L5", -1, 0], ["S1", 0, 1]], all("S1"), all("S1")],
      },
    },
  };

  const MYOTOME_ACTIONS = {
    C2: "neck flexion and extension", C3: "lateral neck flexion", C4: "shoulder elevation",
    C5: "shoulder abduction", C6: "elbow flexion and wrist extension", C7: "elbow extension and wrist flexion",
    C8: "finger flexion and thumb extension", T1: "finger abduction and adduction",
    L1: "hip flexion", L2: "hip flexion", L3: "knee extension", L4: "ankle dorsiflexion",
    L5: "great-toe extension and hip abduction", S1: "ankle plantar flexion and eversion", S2: "knee flexion",
  };

  // ------------------------------------------------------------------ a figure
  // Which side of the patient the viewer-left half is: facing you, the patient's right is on your left.
  function figure(view, face_) {
    const leftSide = face_ === "front" ? "R" : "L", rightSide = leftSide === "R" ? "L" : "R";
    const pieces = [];           // [id|null, d, side]
    const nerveOnly = (id) => (view === "peripheral" && /^C[234]$/.test(id || "") ? null : id);   // the neck is a silhouette on the nerve view
    const add = (list, side) => { for (const [id, d] of list) pieces.push([nerveOnly(id), side === leftSide ? d : mirror(d), side]); };
    for (const side of [leftSide, rightSide]) {
      if (view === "myotome") {
        if (face_ === "front") { add([["C2", neckFront()[0][1]]], side); add([["C4", shoulder()[0][1]]], side); add([[null, face()[0][1]], [null, face()[1][1]], [null, face()[2][1]]], side); }
        else { add([["C3", occiput()[3][1]]], side); add([["C4", shoulder()[0][1]]], side); add([[null, occiput()[0][1]], [null, occiput()[1][1]], [null, occiput()[2][1]]], side); }
      } else if (face_ === "front") { add(neckFront(), side); add(shoulder(), side); add(face(), side); }
      else { add(shoulder(), side); add(occiput(), side); }
      const trunk = view === "dermatome" ? (face_ === "front" ? trunkFront() : trunkBack()) : trunkInert();
      add(trunk, side);
      const limbs = LIMBS[view][face_];
      add(limb(ARM, limbs.arm), side);
      add(limb(LEG, limbs.leg), side);
    }
    const viewName = view === "dermatome" ? "dermatomes" : view === "myotome" ? "myotomes" : "peripheral nerves";
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "bodymap", role: "group", "aria-label": `${face_ === "front" ? "Anterior" : "Posterior"} body: ${viewName}` });
    // the inert pieces first, then the regions, so a region's edge is never under a silhouette
    for (const [id, d] of pieces.filter((p) => !p[0])) svg.append(svgEl("path", { d, class: "inert" }));
    for (const [id, d, side] of pieces.filter((p) => p[0])) {
      const full = `${id}-${side}`;
      const p = svgEl("path", { d, class: "region", "data-id": full, tabindex: "0", role: "button", "aria-label": detail(full, view) });
      const t = svgEl("title"); t.textContent = detail(full, view); p.append(t);
      svg.append(p);
    }
    svg.append(svgEl("path", { d: OUTLINE, class: "anatomy-outline" }));
    svg.append(svgEl("path", { d: OUTLINE, class: "anatomy-outline", transform: `translate(${W} 0) scale(-1 1)` }));
    for (const d of LANDMARKS[face_]) svg.append(svgEl("path", { d, class: "anatomy-landmark" }));
    for (const side of [leftSide, rightSide]) {
      const cx = side === leftSide ? EAR.cx : W - EAR.cx;
      const full = `ear-${side}`;
      const e = svgEl("ellipse", { cx, cy: EAR.cy, rx: EAR.rx, ry: EAR.ry, class: "region ear", "data-id": full, tabindex: "0", role: "button", "aria-label": regionLabel(full) });
      const t = svgEl("title"); t.textContent = regionLabel(full); e.append(t);
      svg.append(e);
    }
    const mark = (x, text) => { const t = svgEl("text", { x, y: 14, class: "side-mark", "text-anchor": "middle" }); t.textContent = text; svg.append(t); };
    mark(14, leftSide); mark(W - 14, rightSide);
    const cap = svgEl("text", { x: CX, y: H - 4, class: "figure-caption", "text-anchor": "middle" }); cap.textContent = face_ === "front" ? "Anterior" : "Posterior"; svg.append(cap);
    return svg;
  }

  let catalog = null, catalogPromise = null;
  function loadCatalog() {
    return catalogPromise || (catalogPromise = fetch("/static/dermatomes.json", { credentials: "same-origin" }).then((r) => r.json()).then((c) => (catalog = c)));
  }
  function regionLabel(full) {
    const m = /^(.+)-([LR])$/.exec(full || "");
    if (!m) return full;
    const spec = catalog && catalog.regions.find((r) => r.id === m[1]);
    const side = m[2] === "L" ? "left" : "right";
    if (m[1] === "ear") return `${side[0].toUpperCase()}${side.slice(1)} ear`;
    return `${spec ? spec.label : m[1]} ${side}`;
  }
  function detail(full, view) {
    const base = String(full || "").replace(/-[LR]$/, "");
    const action = view === "myotome" && MYOTOME_ACTIONS[base];
    return action ? `${regionLabel(full)} myotome — ${action}` : regionLabel(full);
  }
  // the base ids a view draws, so the screen can say when a mark is only on the other view
  function idsInView(view) {
    const ids = new Set(view === "myotome" ? ["ear"] : ["V1", "V2", "V3", "GON", "LON", "ear"]);
    if (view === "dermatome") { for (let i = 2; i <= 12; i++) ids.add(`T${i}`); for (const id of ["C2", "C3", "C4", "L1", "L2", "L3", "S345"]) ids.add(id); }
    if (view === "myotome") for (const id of Object.keys(MYOTOME_ACTIONS)) ids.add(id);
    for (const face_ of ["front", "back"]) for (const part of ["arm", "leg"]) for (const seg of LIMBS[view][face_][part]) for (const [id] of seg) if (id) ids.add(id);
    return ids;
  }

  DDOS.bodymap = { catalog: loadCatalog, get data() { return catalog; }, figure, label: regionLabel, detail, idsInView, VIEWS: ["dermatome", "myotome", "peripheral"] };
})();
