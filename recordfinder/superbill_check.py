"""Check a day's superbill batches before they go to billing (playbooks/superbill-pre-upload-review.md).

A batch ("EC 10.07.26.pdf") is the provider's printed schedule for the day, then one packet per patient: the
superbill, then that patient's own pages. The checker reads every page once (OCR, on this PC), then for each
superbill:

  1. lines the page up with the clinic's form (superbill_form.json: where every printed code sits) using the
     printed codes OCR finds on the page, so a scan that is shifted, tilted or upside down still reads;
  2. takes the blank form away (superbill_form.png) and keeps what's left: the provider's own ink;
  3. gives each mark to the row it sits on - a circled code or an X beside it is that code, a circled name in
     the PROVIDER box is the rendering provider, an X in the IMP / MP column is the attending, and so on;
  4. reads the handwritten name / DOB / DOS with the same OCR and finds the patient on the printed schedule.

Then the playbook's rules run over what was read (visit level, add-ons, provider box, Medicare incident-to,
code pairs the form itself forbids, the schedule's no-shows...). Every finding says what was seen and where,
so a person can check it in seconds; nothing on the scan is changed.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from . import ocr, superbills

HERE = os.path.dirname(os.path.abspath(__file__))
FORM_DPI = 100

# --------------------------------------------------------------------------- the paper form

# Every row the clinic's superbill prints, by box: (code, label). Rows with two codes ("Right BPPV Left") are
# listed once per code. "MOD25 ..." rows print no code: the line itself is what gets marked.
FORM_ROWS: dict[str, list[tuple[str, str]]] = {
    "visit": [
        ("99204", "New patient level 4 (45 min)"), ("99205", "New patient level 5 (60 min)"),
        ("99213", "Follow-up level 3 (20-29 min)"), ("99214", "Follow-up level 4 (30-39 min)"),
        ("99215", "Follow-up level 5 (40-54 min)"), ("G2211", "Add-on G2211"),
        ("MOD25 EMG/NCV", "EMG/NCV Add Mod 25"), ("MOD25 TYMP", "TYMP Add Mod 25"),
        ("MOD25 VNG", "VNG Add Mod 25"), ("MOD25 CDP", "CDP Add Mod 25"), ("MOD25 EEG", "EEG Add Mod 25"),
    ],
    "goggle": [
        ("92285", "Eye photography"), ("92541", "Spontaneous nystagmus"), ("92542", "Positional nystagmus"),
        ("92546", "Chair"), ("95992", "Canalith repositioning"), ("95886", "EMG 5 or more muscles"),
        ("95908", "NCV 3-4 studies"), ("95909", "NCV 5-6 studies"), ("95910", "NCV 7-8 studies"),
        ("95911", "NCV 9-10 studies"), ("95913", "NCV >12 studies"),
    ],
    "procedures": [
        ("64405-50", "Occipital nerve block, bilateral"), ("64405", "Occipital nerve block, unilateral"),
        ("64400-50", "Supraorbital nerve block, bilateral"), ("64400", "Supraorbital nerve block, unilateral"),
        ("20552", "Trigger point inj 1-2"), ("20553", "Trigger point inj 3+"),
        ("20526-50", "Carpal tunnel injection, bilateral"), ("20526", "Carpal tunnel injection, unilateral"),
        ("69210", "Cerumen impaction removal"), ("95977", "Neurostimulator programming (Inspire)"),
        ("95970", "Neurophysiological monitoring (Inspire)"),
    ],
    "procedure dx": [
        ("M54.81", "Occipital neuralgia"), ("M79.2", "Neuralgia & neuritis"), ("M79.10", "Myalgia"),
        ("M79.12", "Myalgia of auxiliary muscles, head/neck"), ("R51", "Headache"),
        ("G56.01", "RT carpal tunnel syndrome"), ("G56.02", "LT carpal tunnel syndrome"),
        ("G43.719", "Chronic migraine w/o aura"), ("H61.23", "Cerumen impaction, bilateral"),
        ("H61.21", "Impacted cerumen, right"), ("H61.22", "Impacted cerumen, left"),
        ("96116", "Montreal Cognitive Assessment"), ("64640", "Qutenza patch"),
    ],
    "otoneurology": [
        ("H81.10", "BPPV, unspecified"), ("H81.11", "BPPV, right"), ("H81.12", "BPPV, left"),
        ("H81.13", "BPPV, bilateral"), ("H82.9", "Vertiginous syndrome"), ("H81.93", "Unsp disorder vestibular function"),
        ("R42", "Dizziness and giddiness"), ("H83.2X9", "Labyrinthine dysfunction unsp"),
        ("H83.2X1", "Labyrinthine dysfunction, right"), ("H83.2X2", "Labyrinthine dysfunction, left"),
        ("H81.4", "Vertigo of central origin"), ("R11.11", "Vomiting w/o nausea"), ("R11.2", "Nausea with vomiting"),
        ("R27.8", "Other lack of coordination"), ("R26.0", "Ataxic gait"), ("R26.89", "Other abnormalities of gait"),
        ("H90.5", "SNHL"), ("H93.19", "Tinnitus"), ("H92.09", "Otalgia"), ("H69.80", "Eustachian tube dysfunction"),
        ("D33.3", "Acoustic neuroma"), ("R51.9", "Headache"), ("G43.909", "Migraine, not intractable"),
        ("G43.719", "Chronic migraine w/o aura"), ("G43.109", "Migraine w/ aura"), ("G43.819", "Migraine variant"),
        ("F07.81", "Postconcussive syndrome"), ("J32.9", "Sinusitis, chronic"), ("I69.90", "Stroke, unspecified"),
        ("G45.9", "TIA"), ("G30.9", "Alzheimer's"), ("F01.50", "Vascular dementia"),
        ("F02.80", "Dementia in other diseases"), ("R41.2", "Retrograde amnesia"),
        ("G31.84", "Mild cognitive impairment"), ("R41.3", "Memory difficulties"), ("R41.82", "Altered mental status"),
        ("F41.1", "Anxiety"), ("F32.9", "Depressive disorder"), ("R45.86", "Emotional lability"),
        ("G91.1", "Obstructive hydrocephalus"), ("G91.2", "Normal pressure hydrocephalus"),
        ("H81.23", "Vestibular neuronitis, bilateral"), ("H81.311", "Vestibular neuronitis, right (as printed)"),
        ("H81.312", "Vestibular neuronitis, left (as printed)"), ("H83.09", "Labyrinthitis"),
        ("H81.01", "Meniere's, right"), ("H81.02", "Meniere's, left"), ("H81.03", "Meniere's, bilateral"),
        ("H81.09", "Meniere's, unspecified ear"),
    ],
    "nerve": [
        ("M48.02", "Cervical spinal stenosis"), ("M48.04", "Thoracic spinal stenosis"),
        ("M48.061", "Lumbar spinal stenosis"), ("G54.2", "Cervical foraminal stenosis"),
        ("G54.3", "Thoracic foraminal stenosis"), ("G54.4", "Lumbosacral foraminal stenosis"),
        ("M54.16", "Radiculopathy, lumbar"), ("M54.12", "Radiculopathy, cervical"), ("M54.2", "Cervicalgia"),
        ("M79.10", "Myalgia"), ("M54.81", "Occipital neuralgia"), ("M79.2", "Neuralgia & neuritis"),
        ("M54.6", "Pain in thoracic spine"), ("M54.5", "Low back pain"), ("G60.9", "Peripheral neuropathy"),
        ("G57.30", "Peroneal neuropathy"), ("G56.01", "Carpal tunnel, right"), ("G56.02", "Carpal tunnel, left"),
        ("R20.1", "Hypoesthesia"), ("R20.2", "Paresthesia"), ("R20.9", "Unspecified disturbance of skin"),
        ("G50.0", "Trigeminal neuralgia"), ("G51.0", "Bell's palsy"), ("I65.21", "Carotid stenosis, right"),
        ("I65.22", "Carotid stenosis, left"), ("I65.23", "Carotid stenosis, bilateral"),
        ("I65.29", "Carotid stenosis, unspecified"), ("I67.1", "Cerebral aneurysm"),
        ("D33.2", "Benign neoplasm of brain"), ("D33.0", "Benign neoplasm, supratentorial"),
        ("D33.1", "Benign neoplasm, infratentorial"), ("H53.121", "Transient visual loss, right"),
        ("H53.122", "Transient visual loss, left"), ("H53.123", "Transient visual loss, bilateral"),
        ("H53.129", "Transient visual loss, unspecified"), ("H53.2", "Diplopia"), ("H54.7", "Visual loss"),
        ("H53.9", "Visual disturbance"), ("M26.609", "TMJ disorder"), ("G47.9", "Sleep disorder"),
        ("G47.01", "Insomnia due to medical condition"), ("G47.419", "Narcolepsy"), ("G47.61", "PLMS"),
        ("G47.33", "Obstructive sleep apnea"), ("G25.81", "Restless leg syndrome"),
    ],
    "epilepsy": [
        ("G40.901", "Epilepsy, unspecified"), ("G40.309", "Generalized epilepsy"), ("G40.209", "Complex partial"),
        ("G40.109", "Simple partial"), ("R56.9", "Convulsion"), ("R53.82", "Chronic fatigue"), ("R55", "Syncope"),
        ("I95.1", "Orthostatic hypotension"), ("R03.0", "Elevated blood pressure"), ("I49.9", "Cardiac arrhythmia"),
        ("G25.0", "Essential tremor"), ("R25.1", "Tremor"), ("G21.19", "Drug-induced parkinsonism"),
        ("G21.8", "Other secondary parkinsonism"), ("R47.01", "Aphasia"), ("R47.1", "Dysarthria"),
        ("M62.838", "Muscle spasm"), ("G25.3", "Myoclonus"), ("R29.2", "Abnormal reflex"),
        ("M62.81", "Generalized muscle weakness"), ("I69.359", "Hemiparesis after stroke"),
        ("D51.9", "B12 deficiency anemia"), ("G80.8", "Cerebral palsy"),
        ("G20.A1", "PD w/o dyskinesia, w/o fluctuations"), ("G20.A2", "PD w/o dyskinesia, with fluctuations"),
        ("G20.B1", "PD with dyskinesia, w/o fluctuations"), ("G20.B2", "PD with dyskinesia, with fluctuations"),
        ("G20.C", "Parkinsonism, unspecified"), ("G35.A", "Relapsing-remitting MS"), ("G35.D", "MS, unspecified"),
    ],
}


def _older_rows() -> dict[str, list[tuple[str, str]]]:
    """The earlier printing still used for EMG/NCV days: "Modifier 25" lines, Cervicalgia in the procedure
    diagnoses, pre-2025 codes (M48.06, M26.60, M62.83, G35) and an ADDITIONAL ICD-10 CODES box instead of MS rows."""
    swap = {"M48.061": ("M48.06", "Lumbar spinal stenosis"), "M26.609": ("M26.60", "TMJ disorder"),
            "M62.838": ("M62.83", "Muscle spasm"), "G40.901": ("G40.909", "Epilepsy, unspecified")}
    rows = {}
    for box, items in FORM_ROWS.items():
        out = []
        for code, label in items:
            if box == "procedure dx" and code == "M79.12":
                out.append(("M54.2", "Cervicalgia"))
            elif box == "epilepsy" and code in ("G35.A", "G35.D"):
                if code == "G35.A":
                    out.append(("G35", "Multiple sclerosis"))
            else:
                out.append(swap.get(code, (code, label)))
        rows[box] = out
    return rows


# Codes the printed form offers that a claim can't carry as printed, or that don't say what the row says.
FORM_CODE_NOTES = {
    "R51": "R51 has not been billable since 10/1/2020; the headache code is R51.9",
    "M54.5": "M54.5 has not been billable since 10/1/2021; low back pain is M54.50 / M54.51 / M54.59",
    "M48.06": "M48.06 needs a 6th character: M48.061 (no neurogenic claudication) or M48.062 (with)",
    "M26.60": "M26.60 needs a 6th character (M26.601-M26.609)",
    "M62.83": "M62.83 needs a 6th character: muscle spasm is M62.838 (other), M62.830 (back), M62.831 (calf)",
    "G35": "G35 alone is not billable from 10/1/2025; MS is G35.A (relapsing-remitting) ... G35.D (unspecified)",
    "H81.311": "H81.311 is aural vertigo, right ear; the row says vestibular neuronitis, which is H81.21 (right)",
    "H81.312": "H81.312 is aural vertigo, left ear; the row says vestibular neuronitis, which is H81.22 (left)",
    "G40.901": "G40.901 is epilepsy WITH status epilepticus; the row says 'Epilepsy, unspecified' (usually G40.909)",
}

# The boxes, in the form's frame (PDF points of the reference scan): x range, and which part of the page.
BOX_X = {"visit": (18, 166), "goggle": (166, 282), "procedures": (282, 446), "procedure dx": (446, 600),
         "otoneurology": (12, 207), "nerve": (207, 416), "epilepsy": (416, 600)}
TOP_Y = (74, 236)            # the procedure boxes; the diagnosis boxes are below
HEADER_Y = (52, 74)          # PATIENT NAME / DATE OF BIRTH / DATE OF SERVICE, handwritten
HEADER_X = {"name": (20, 335), "dob": (335, 457), "dos": (457, 600)}
MOD25_WORDS = {"MOD25 EMG/NCV": "EMG", "MOD25 TYMP": "TYMP", "MOD25 VNG": "VNG", "MOD25 CDP": "CDP", "MOD25 EEG": "EEG"}


@dataclass
class Cell:
    code: str
    label: str
    box: str                 # FORM_ROWS key
    rect: tuple              # where the printed code (or the row's label) sits, form points


@dataclass
class Form:
    name: str                               # FORM_VERSIONS key
    size: tuple                             # (W, H) in points
    cells: list[Cell]
    anchors: list[tuple]                    # (text, cx, cy): printed words used to line a scan up
    providers: dict                         # surname (upper) -> (cy, x0, x1)
    columns: dict                           # "left" / "right" -> cx of the attending columns (and YES / NO)
    yes_no: dict                            # "incident_to" / "tricare" -> cy
    printed: object = None                  # bool array at FORM_DPI: where the blank form has ink

    @classmethod
    def load(cls, name: str) -> "Form":
        _rows, json_name, png_name = FORM_VERSIONS[name]
        with open(os.path.join(HERE, json_name), encoding="utf-8") as f:
            d = json.load(f)
        form = cls(name, tuple(d["size"]), [Cell(c["code"], c["label"], c["box"], tuple(c["rect"])) for c in d["cells"]],
                   [tuple(a) for a in d["anchors"]], {k: tuple(v) for k, v in d["providers"].items()},
                   d["columns"], d["yes_no"])
        form.printed = _load_mask(os.path.join(HERE, png_name))
        return form

    def save(self, folder: str = HERE) -> None:
        _rows, json_name, png_name = FORM_VERSIONS[self.name]
        d = {"name": self.name, "size": list(self.size), "dpi": FORM_DPI,
             "cells": [{"code": c.code, "label": c.label, "box": c.box, "rect": [round(v, 1) for v in c.rect]} for c in self.cells],
             "anchors": [[t, round(x, 1), round(y, 1)] for t, x, y in self.anchors],
             "providers": {k: [round(v, 1) for v in vals] for k, vals in self.providers.items()},
             "columns": {k: round(v, 1) for k, v in self.columns.items()},
             "yes_no": {k: round(v, 1) for k, v in self.yes_no.items()}}
        with open(os.path.join(folder, json_name), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        save_mask(self.printed, os.path.join(folder, png_name))


def _load_mask(png_path: str):
    import numpy as np
    import pymupdf
    pix = pymupdf.Pixmap(png_path)
    if pix.n != 1:
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) < 128


# Printings of the superbill the clinic uses: rows, map, blank image. A scan is read with whichever lines up best.
FORM_VERSIONS = {
    "current": (FORM_ROWS, "superbill_form.json", "superbill_form.png"),
    "older": (_older_rows(), "superbill_form_older.json", "superbill_form_older.png"),
}
_FORMS: dict[str, Form] = {}


def forms() -> list[Form]:
    for name, (_rows, json_name, _png) in FORM_VERSIONS.items():
        if name not in _FORMS and os.path.exists(os.path.join(HERE, json_name)):
            _FORMS[name] = Form.load(name)
    return list(_FORMS.values())


# --------------------------------------------------------------------------- lining a scan up with the form

def _key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def fit_affine(pairs: list[tuple]) -> tuple:
    """Least-squares affine from page points to form points: pairs of ((px, py), (fx, fy)).
    Returns (a, b, c, d, e, f) with fx = a*px + c*py + e, fy = b*px + d*py + f (PyMuPDF's Matrix order)."""
    import numpy as np
    src = np.array([[p[0], p[1], 1.0] for p, _ in pairs])
    dst = np.array([q for _, q in pairs], dtype=float)
    sol, *_ = np.linalg.lstsq(src, dst, rcond=None)
    (a, b), (c, d), (e, f) = sol
    return float(a), float(b), float(c), float(d), float(e), float(f)


def apply(m: tuple, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


@dataclass
class Alignment:
    matrix: tuple            # page points -> form points
    matched: int             # printed words used
    residual: float          # median distance after fitting, points

    @property
    def good(self) -> bool:
        return self.matched >= 12 and self.residual < 2.5

    @property
    def upside_down(self) -> bool:
        return self.matrix[0] < 0 and self.matrix[3] < 0


def align(words: list, anchors: list[tuple]) -> Alignment | None:
    """Line a page up with the form from the printed words both have once (codes mostly). Outliers - a word
    OCR misread into another printed word, or the provider's handwriting - are dropped and the fit redone."""
    import numpy as np
    ref: dict[str, list] = {}
    for t, x, y in anchors:
        ref.setdefault(_key(t), []).append((x, y))
    page: dict[str, list] = {}
    for w in words:
        k = _key(w.text)
        if len(k) >= 4:
            page.setdefault(k, []).append((w.cx, w.cy))
    pairs = [(page[k][0], ref[k][0]) for k in page if k in ref and len(page[k]) == 1 and len(ref[k]) == 1]
    if len(pairs) < 6:
        return None
    m = fit_affine(pairs)
    for limit in (12.0, 5.0, 2.5):
        err = [np.hypot(*np.subtract(apply(m, *p), q)) for p, q in pairs]
        keep = [pq for pq, e in zip(pairs, err) if e <= limit]
        if len(keep) < 6:
            break
        pairs = keep
        m = fit_affine(pairs)
    err = sorted(np.hypot(*np.subtract(apply(m, *p), q)) for p, q in pairs)
    return Alignment(m, len(pairs), float(err[len(err) // 2]))


def render_in_form(page, m: tuple, size: tuple, dpi: int = FORM_DPI):
    """The page drawn in the form's frame at `dpi`, as a grayscale array (0 = black)."""
    import numpy as np
    import pymupdf
    k = dpi / 72
    a, b, c, d, e, f = m
    pix = page.get_pixmap(matrix=pymupdf.Matrix(a * k, b * k, c * k, d * k, e * k, f * k), colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    W, H = round(size[0] * k), round(size[1] * k)
    out = np.full((H, W), 255, dtype=np.uint8)
    x0, y0 = pix.x, pix.y
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    w = min(W - dx0, pix.width - sx0)
    h = min(H - dy0, pix.height - sy0)
    if w > 0 and h > 0:
        out[dy0:dy0 + h, dx0:dx0 + w] = img[sy0:sy0 + h, sx0:sx0 + w]
    return out


def words_in_form(words: list, m: tuple) -> list:
    """OCR words moved into the form's frame."""
    out = []
    for w in words:
        xs, ys = zip(*(apply(m, x, y) for x, y in ((w.x0, w.y0), (w.x1, w.y0), (w.x0, w.y1), (w.x1, w.y1))))
        out.append(ocr.Word(w.text, min(xs), min(ys), max(xs), max(ys)))
    return out


# --------------------------------------------------------------------------- building the form (once, by a developer)

_CONFUSE = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "M": "H", "N": "H", "Z": "2", "Q": "0", "D": "0"})


def _code_key(text: str) -> str:
    """A code as OCR may read it: letters it confuses on these forms folded together ("M79.10" ~ "H79.1O")."""
    return _key(text).translate(_CONFUSE)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def ocr_image(gray, size: tuple) -> list:
    """OCR a grayscale array that covers a page of `size` points; words come back in those points."""
    import pymupdf
    h, w = gray.shape
    pix = pymupdf.Pixmap(pymupdf.csGRAY, w, h, bytes(gray.tobytes()), False)
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    page.insert_image(page.rect, pixmap=pix)
    try:
        return ocr.words_of(ocr.ocr_page(page, dpi=200))
    finally:
        doc.close()


def build_form(name: str, samples: list[tuple], reference: int = 0, log=print) -> Form:
    """Make a printing's map and blank image from scanned superbills: samples are (pdf path, page index, OCR words).

    Every page is lined up with the reference page. The pixel-wise median of them all is the blank form (each
    provider writes in a different place, so handwriting drops out); each code's place is the median of where OCR
    read it across the pages, and the alignment anchors are the printed words read in the same place on most."""
    import numpy as np
    import pymupdf
    ref_path, ref_index, ref_words = samples[reference]
    with pymupdf.open(ref_path) as d:
        size = (d[ref_index].rect.width, d[ref_index].rect.height)
    counts: dict[str, int] = {}
    for w in ref_words:
        counts[_key(w.text)] = counts.get(_key(w.text), 0) + 1
    anchors = [(w.text, w.cx, w.cy) for w in ref_words if counts[_key(w.text)] == 1 and len(_key(w.text)) >= 4]
    stack, pages = [], []
    for path, index, words in samples:
        al = align(words, anchors)
        if al is None or not al.good:
            log(f"skipped {os.path.basename(path)} page {index + 1}: doesn't line up")
            continue
        with pymupdf.open(path) as d:
            stack.append(render_in_form(d[index], al.matrix, size))
        pages.append(words_in_form(words, al.matrix))
    log(f"{len(stack)} pages lined up")
    printed = np.median(np.stack(stack), axis=0) < 128
    # The median loses thin rules (a pixel or two off on most pages), so line every page up again tile by tile
    # with that first blank and keep what's dark on enough of them: print is, handwriting never is.
    for _round in range(2):
        counts = np.zeros(printed.shape, dtype=np.int32)
        for gray in stack:
            dark = gray < 150
            _fitted, shifts = _fit_tiles(dark, printed)
            moved = np.zeros_like(dark)
            for (y0, x0), (dy, dx) in shifts.items():
                y1, x1 = min(dark.shape[0], y0 + TILE), min(dark.shape[1], x0 + TILE)
                src = np.pad(dark, SHIFT)[y0 + SHIFT + dy:y1 + SHIFT + dy, x0 + SHIFT + dx:x1 + SHIFT + dx]
                moved[y0:y1, x0:x1] = src
            counts += moved
        printed = counts >= (0.3 if len(stack) >= 20 else 0.5) * len(stack)
    # Only what printed words or straight rules explain is the form, unless it's on nearly every page: a provider
    # who circles their own name on every sheet must not end up in the blank form.
    rows = FORM_VERSIONS[name][0]
    explained = _straight_runs(printed, 12) | _word_mask(pages, printed.shape)
    printed = (printed & explained) | (counts >= 0.9 * len(stack))
    k = FORM_DPI / 72
    # Whatever handwriting survives in the header boxes is not part of the form.
    y0, y1 = int((HEADER_Y[0] + 3.5) * k), int((HEADER_Y[1] - 4) * k)
    row = printed[y0:y1]
    keep = row.mean(axis=0) > 0.9                            # the boxes' vertical borders
    row[:, ~keep] = False
    return _map_form(name, rows, pages, size, printed)


def _straight_runs(mask, length: int):
    """Pixels on a horizontal or vertical run of at least `length` (the form's rules)."""
    import numpy as np

    def runs(m):
        c = np.cumsum(np.pad(m, ((0, 0), (1, 0))).astype(np.int32), axis=1)
        full = (c[:, length:] - c[:, :-length]) == length            # window starting at x is all ink
        out = np.zeros_like(m)
        for k in range(length):
            out[:, k:k + full.shape[1]] |= full
        return out
    return runs(mask) | runs(mask.T).T


def _word_mask(pages: list[list], shape: tuple, dpi: int = FORM_DPI):
    """Boxes of words OCR read in the same place on at least half the pages (printed text), 1 pt bigger."""
    import numpy as np
    spots: dict[str, list] = {}
    for words in pages:
        for w in words:
            spots.setdefault(_key(w.text), []).append(w)
    k = dpi / 72
    out = np.zeros(shape, dtype=bool)
    for ws in spots.values():
        if len(ws) < len(pages) / 2:
            continue
        groups: list[list] = []
        for w in ws:
            home = next((g for g in groups if abs(g[0].cx - w.cx) < 5 and abs(g[0].cy - w.cy) < 4), None)
            if home is None:
                groups.append([w])
            else:
                home.append(w)
        for group in groups:
            if len(group) < len(pages) / 2:
                continue
            x0, y0, x1, y1 = _median_rect([(w.x0, w.y0, w.x1, w.y1) for w in group])
            out[max(0, int((y0 - 1) * k)):int((y1 + 1) * k) + 1, max(0, int((x0 - 1) * k)):int((x1 + 1) * k) + 1] = True
    return out


def _median_rect(rects: list[tuple]) -> tuple:
    import numpy as np
    return tuple(float(v) for v in np.median(np.array(rects), axis=0))


def _cells_on(rows_by_box: dict, words: list, size: tuple) -> list[Cell]:
    """The form's code cells as one page's OCR reads them (words already in the form's frame)."""
    cells, used = [], set()
    for box, rows in rows_by_box.items():
        bx0, bx1 = BOX_X[box]
        top = box in ("visit", "goggle", "procedures", "procedure dx")
        y0, y1 = TOP_Y if top else (TOP_Y[1], size[1] - 20)
        region = [w for w in words if bx0 <= w.cx < bx1 and y0 <= w.cy < y1]
        for code, label in rows:
            if code.startswith("MOD25"):
                word = MOD25_WORDS[code]
                hits = [w for w in region if _key(word) in _key(w.text) and w.x0 < bx0 + 60 and w.cy > 160]
                if hits:
                    w = min(hits, key=lambda w: w.cy)
                    cells.append(Cell(code, label, box, (bx1 - 36, w.y0, bx1 - 6, w.y1)))
                continue
            want = _code_key(code)
            label_words = [_key(t) for t in re.findall(r"[A-Za-z]{4,}", label)]
            best, best_s = None, 0.0
            for w in region:
                if id(w) in used:
                    continue
                s = _similar(_code_key(w.text), want)
                if s < (0.99 if len(want) <= 3 else 0.7):
                    continue
                line = [_key(v.text) for v in region if abs(v.cy - w.cy) < 3.5]
                s += 0.3 * sum(any(_similar(lw, t) > 0.8 for t in line) for lw in label_words) / max(1, len(label_words))
                if s > best_s:
                    best, best_s = w, s
            if best is not None:
                used.add(id(best))
                cells.append(Cell(code, label, box, (best.x0, best.y0, best.x1, best.y1)))
    return cells


def _map_form(name: str, rows_by_box: dict, pages: list[list], size: tuple, printed) -> Form:
    import numpy as np
    W, H = size
    found: dict[tuple, list] = {}
    labels: dict[tuple, str] = {}
    for words in pages:
        for c in _cells_on(rows_by_box, words, size):
            found.setdefault((c.box, c.code), []).append(c.rect)
            labels[(c.box, c.code)] = c.label
    cells = []
    for box, rows in rows_by_box.items():
        for code, label in rows:
            rects = found.get((box, code), [])
            if len(rects) >= max(3, len(pages) // 5):
                cells.append(Cell(code, label, box, _median_rect(rects)))

    def pooled(test) -> list:
        return [w for words in pages for w in words if test(w)]
    in_box = lambda w: w.cx > W * 0.68 and w.cy > H * 0.74
    providers = {}
    for s in ("PURCELL", "PATEL", "YARI", "PERKINS", "COPELAND", "CAMARILLO"):
        hits = pooled(lambda w: in_box(w) and _similar(_key(w.text), s) >= 0.75)
        if hits:
            providers[s] = (float(np.median([w.cy for w in hits])), 478.0, 543.0)
    left = pooled(lambda w: in_box(w) and w.cx < W * 0.76 and _key(w.text) in ("IMP", "YES", "TES"))
    right = pooled(lambda w: in_box(w) and w.cx > W * 0.92 and _key(w.text) in ("MP", "NO", "KO", "MF", "HP"))
    columns = {"left": float(np.median([w.cx for w in left])) if left else 0.0,
               "right": float(np.median([w.cx for w in right])) if right else 0.0}
    yes_no = {}
    for row, word in (("incident_to", "MEDICARE"), ("tricare", "TRICARE")):
        hits = pooled(lambda w: in_box(w) and _similar(_key(w.text), word) >= 0.8)
        if hits:
            yes_no[row] = float(np.median([w.cy for w in hits]))
    # anchors: printed words read once per page, in the same place on at least half the pages
    spots: dict[str, list] = {}
    for words in pages:
        counts: dict[str, int] = {}
        for w in words:
            counts[_key(w.text)] = counts.get(_key(w.text), 0) + 1
        for w in words:
            k = _key(w.text)
            if counts[k] == 1 and len(k) >= 4 and w.cy > HEADER_Y[1]:
                spots.setdefault(k, []).append((w.text, w.cx, w.cy))
    anchors = []
    for k, hits in spots.items():
        if len(hits) < len(pages) / 2:
            continue
        xs, ys = np.array([h[1] for h in hits]), np.array([h[2] for h in hits])
        mx, my = float(np.median(xs)), float(np.median(ys))
        if np.median(np.hypot(xs - mx, ys - my)) < 1.5:
            anchors.append((k, mx, my))
    return Form(name, size, cells, anchors, providers, columns, yes_no, printed)


def save_mask(printed, png_path: str) -> None:
    import numpy as np
    import pymupdf
    img = np.where(printed, 0, 255).astype(np.uint8)
    h, w = img.shape
    pymupdf.Pixmap(pymupdf.csGRAY, w, h, bytes(img.tobytes()), False).save(png_path)


# --------------------------------------------------------------------------- reading the provider's marks

INK_MIN = 14          # pixels at FORM_DPI: smaller specks are scanner dust
GRID = 2              # strokes are grouped on a grid of GRID x GRID pixels, which also bridges small gaps


@dataclass
class Blob:
    x0: float
    y0: float
    x1: float
    y1: float
    n: int            # ink pixels

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def contains(self, x: float, y: float, pad: float = 0.0) -> bool:
        return self.x0 - pad <= x <= self.x1 + pad and self.y0 - pad <= y <= self.y1 + pad

    def rect(self) -> tuple:
        return (round(self.x0, 1), round(self.y0, 1), round(self.x1, 1), round(self.y1, 1))


def _dilate(mask, r: int):
    import numpy as np
    out = mask.copy()
    h, w = mask.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx or dy:
                src = mask[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)]
                out[max(0, dy):h - max(0, -dy), max(0, dx):w - max(0, -dx)] |= src
    return out


TILE, SHIFT = 96, 3      # the blank form is fitted again per tile (pixels): scanners stretch a page unevenly


def _tiles(shape):
    h, w = shape
    for y0 in range(0, h, TILE):
        for x0 in range(0, w, TILE):
            yield y0, x0, min(h, y0 + TILE), min(w, x0 + TILE)


def _fit_tiles(dark, printed):
    """The blank form moved, tile by tile, by up to SHIFT pixels to wherever it best covers the page's dark pixels."""
    import numpy as np
    h, w = dark.shape
    padded = np.pad(printed[:h, :w], SHIFT)
    fitted = np.zeros_like(dark)
    shifts = {}
    for y0, x0, y1, x1 in _tiles(dark.shape):
        tile = dark[y0:y1, x0:x1]
        best, best_n = (0, 0), -1
        if tile.any():
            for dy in range(-SHIFT, SHIFT + 1):
                for dx in range(-SHIFT, SHIFT + 1):
                    cand = padded[y0 + SHIFT - dy:y1 + SHIFT - dy, x0 + SHIFT - dx:x1 + SHIFT - dx]
                    n = int(np.count_nonzero(tile & cand))
                    if n > best_n:
                        best, best_n = (dy, dx), n
        dy, dx = best
        shifts[(y0, x0)] = best
        fitted[y0:y1, x0:x1] = padded[y0 + SHIFT - dy:y1 + SHIFT - dy, x0 + SHIFT - dx:x1 + SHIFT - dx]
    return fitted, shifts


def ink_mask(gray, printed):
    """The provider's ink: dark pixels that aren't part of the blank form (fitted per tile, then ~1 pt of slack)."""
    dark = gray < 150
    fitted, _ = _fit_tiles(dark, printed)
    return dark & ~_dilate(fitted, 1)


def blobs_of(ink, dpi: int = FORM_DPI) -> list[Blob]:
    """Connected strokes, in form points. Strokes a pixel or two apart (a circle broken where it crosses a
    printed line) count as one."""
    import numpy as np
    h, w = ink.shape
    hh, ww = h // GRID, w // GRID
    coarse = ink[:hh * GRID, :ww * GRID].reshape(hh, GRID, ww, GRID).sum(axis=(1, 3))
    occupied = coarse > 0
    joined = _dilate(occupied, 1)
    seen = np.zeros_like(joined)
    k = 72 / dpi * GRID
    out = []
    ys, xs = np.nonzero(joined)
    for y, x in zip(ys.tolist(), xs.tolist()):
        if seen[y, x]:
            continue
        stack, cells = [(y, x)], []
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            cells.append((cy, cx))
            for ny in (cy - 1, cy, cy + 1):
                for nx in (cx - 1, cx, cx + 1):
                    if 0 <= ny < hh and 0 <= nx < ww and joined[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        real = [(cy, cx) for cy, cx in cells if occupied[cy, cx]]
        n = int(sum(coarse[cy, cx] for cy, cx in real))
        if n < INK_MIN or not real:
            continue
        ry, rx = zip(*real)
        b = Blob(min(rx) * k, min(ry) * k, (max(rx) + 1) * k, (max(ry) + 1) * k, n)
        if min(b.w, b.h) <= 2 * k or min(b.w, b.h) <= 4.5 and max(b.w, b.h) >= 6 * min(b.w, b.h):
            continue                    # a sliver of a printed rule the fit missed, not a pen stroke
        out.append(b)
    return out


@dataclass
class Mark:
    code: str
    label: str
    box: str
    rect: tuple           # the ink, form points
    how: str              # "circled" | "beside"


@dataclass
class Reading:
    """What the provider marked on one superbill, in the form's frame."""
    name: str = ""
    dob_text: str = ""
    dos_text: str = ""
    dob_candidates: list[str] = field(default_factory=list)  # ordinary OCR, then the high-resolution header pass
    dos_candidates: list[str] = field(default_factory=list)
    marks: list[Mark] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)      # circled surnames, form order
    attending: list[tuple] = field(default_factory=list)    # (column "left"/"right", surname of the row)
    incident_to: list[str] = field(default_factory=list)    # "YES" / "NO" (both = scribbled over)
    tricare: list[str] = field(default_factory=list)
    written: list[tuple] = field(default_factory=list)      # handwritten words outside the header: (text, rect)
    stray: list[Blob] = field(default_factory=list)          # ink that isn't on any row
    rects: dict = field(default_factory=dict)                # where things are, for the review sheet

    @property
    def codes(self) -> list[str]:
        return list(dict.fromkeys(m.code for m in self.marks))

    def has(self, *codes: str) -> bool:
        return any(c in self.codes for c in codes)

    def marked(self, box: str) -> list[str]:
        return list(dict.fromkeys(m.code for m in self.marks if m.box == box))


_LOOKALIKE = str.maketrans("авекмнорстухиздАВЕКМНОРСТУХИЗД", "aBekMHopcTyxu3gABEKMHOPCTYXU3D")


def _ascii(text: str) -> str:
    """Handwriting OCR sometimes answers in Cyrillic look-alikes or with stray accents ("вискнат" is BUCKHOUT)."""
    return re.sub(r"[^\x20-\x7e]", "", text.translate(_LOOKALIKE))


_HEADER_LABELS = ("PATIENT", "NAME", "DATE", "BIRTH", "SERVICE", "OF")
_CODE_TEXT = re.compile(r"^[A-TV-Z]\d{2}(?:\.[0-9A-Z]{1,4})?$|^\d{5}(?:-\d{2})?$|^G\d{4}$")


def _handwritten(words: list, ink, printed, dpi: int = FORM_DPI) -> list:
    """Words OCR read whose dark pixels are mostly the provider's ink, not the form's print."""
    k = dpi / 72
    out = []
    H, W = ink.shape
    for w in words:
        x0, y0 = max(0, int(w.x0 * k)), max(0, int(w.y0 * k))
        x1, y1 = min(W, int(w.x1 * k) + 1), min(H, int(w.y1 * k) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        mine = int(ink[y0:y1, x0:x1].sum())
        form_ink = int(printed[y0:y1, x0:x1].sum())
        if mine >= 6 and mine > 1.2 * form_ink:
            out.append(w)
    return out


def split_tall(b: Blob, ink, rows_y: list[float] = (), dpi: int = FORM_DPI) -> list[Blob]:
    """Circled numbers on rows next to each other (diagnosis 1, 2, 3 in order) touch and read as one tall blob:
    cut it where the ink pinches, between one circle and the next, or else between the form's rows (a column of
    X's touching tip to tip doesn't pinch)."""
    import numpy as np
    k = dpi / 72
    y0, y1 = int(b.y0 * k), int(b.y1 * k) + 1
    x0, x1 = int(b.x0 * k), int(b.x1 * k) + 1
    sub = ink[y0:y1, x0:x1]
    prof = np.count_nonzero(sub, axis=1).astype(float)
    if len(prof) < 3 or b.w > 45:
        return []
    filled = sub.any(axis=1)
    span = np.array([(np.nonzero(r)[0][-1] - np.nonzero(r)[0][0]) if r.any() else 0 for r in sub])
    if np.count_nonzero(span[filled] >= 6 * k) < 0.5 * np.count_nonzero(filled):
        return []      # a line (an arrow, a bracket) is thin all the way down; a stack of circles isn't
    smooth = np.convolve(prof, np.ones(3) / 3, mode="same")
    low = 0.35 * smooth.max()
    min_h = int(7 * k)
    cuts, last = [0], 0
    for y in range(min_h, len(smooth) - min_h):
        if smooth[y] <= low and smooth[y] == smooth[max(0, y - min_h // 2):y + min_h // 2 + 1].min() and y - last >= min_h:
            cuts.append(y)
            last = y
    cuts.append(len(smooth))
    if len(cuts) <= 2:
        inside = [y for y in rows_y if b.y0 - 3 <= y <= b.y1 + 3]
        if len(inside) < 2:
            return []
        cuts = [0] + [int(((u + v) / 2 - b.y0) * k) for u, v in zip(inside, inside[1:])
                      if 0 < ((u + v) / 2 - b.y0) * k < len(smooth)] + [len(smooth)]
    out = []
    for a, c in zip(cuts, cuts[1:]):
        piece = sub[a:c]
        rows_, cols = np.nonzero(piece)
        if len(rows_) < INK_MIN:
            continue
        piece = Blob((x0 + cols.min()) / k, (y0 + a + rows_.min()) / k, (x0 + cols.max() + 1) / k,
                     (y0 + a + rows_.max() + 1) / k, int(len(rows_)))
        if min(piece.w, piece.h) >= 6:
            out.append(piece)
    return out


def ringed(ink, rect: tuple, dpi: int = FORM_DPI) -> bool:
    """Is this printed code circled? Ink just left and just right of it on its line (the top and bottom of a circle
    often run along the table's rules and go with them). Works when the circle came apart into pieces."""
    k = dpi / 72
    x0, y0, x1, y1 = rect
    cy = (y0 + y1) / 2
    H, W = ink.shape

    def ink_count(a, b, c, d):
        a, b, c, d = max(0, int(a * k)), max(0, int(b * k)), min(W, int(c * k) + 1), min(H, int(d * k) + 1)
        return int(ink[b:d, a:c].sum()) if c > a and d > b else 0

    # A few uncovered pixels from the printed code are common after a scan is deskewed. A real pen circle has a
    # visible stroke on both sides. Seven pixels at 100 dpi is still less than a 1 pt x 4 pt stroke, but rejects
    # the small print residue that otherwise turns untouched rows into charges.
    return ink_count(x0 - 8, cy - 3, x0 + 1, cy + 3) >= 7 and \
        ink_count(x1 - 1, cy - 3, x1 + 8, cy + 3) >= 7


def _nearest_row(rows: dict, y: float, within: float):
    best = min(rows.items(), key=lambda kv: abs(kv[1] - y), default=None)
    return best[0] if best is not None and abs(best[1] - y) <= within else None


def read_page(gray, words: list, f: Form) -> Reading:
    """Read one superbill drawn in the form's frame (gray) with its OCR words (already in the form's frame)."""
    W, H = f.size
    ink = ink_mask(gray, f.printed)
    r = Reading()
    head = sorted((w for w in words if HEADER_Y[0] - 2 <= w.cy <= HEADER_Y[1] + 2
                   and not any(_similar(_key(w.text), lab) >= 0.75 for lab in _HEADER_LABELS)), key=lambda w: w.x0)
    for part, (x0, x1) in HEADER_X.items():
        text = _ascii(" ".join(w.text for w in head if x0 <= w.cx < x1))
        setattr(r, {"name": "name", "dob": "dob_text", "dos": "dos_text"}[part], text.strip())
    r.rects["header"] = (HEADER_X["name"][0], HEADER_Y[0], HEADER_X["dos"][1], HEADER_Y[1])
    k = FORM_DPI / 72
    for part, (x0, x1) in HEADER_X.items():
        r.rects["ink_" + part] = int(ink[int((HEADER_Y[0] - 3) * k):int((HEADER_Y[1] + 1) * k),
                                         int((x0 + 3) * k):int((x1 - 3) * k)].sum())

    hand = [w for w in _handwritten(words, ink, f.printed) if w.cy > HEADER_Y[1] + 3]
    writing = []
    for w in hand:
        t = w.text.strip(".,;:()[]{}'\"")
        if len(re.sub(r"[^A-Za-z0-9]", "", t)) >= 3:
            r.written.append((t, (round(w.x0, 1), round(w.y0, 1), round(w.x1, 1), round(w.y1, 1))))
            writing.append(w)

    cells_by_box: dict[str, list[Cell]] = {}
    for c in f.cells:
        cells_by_box.setdefault(c.box, []).append(c)
    left, right = f.columns.get("left", 0), f.columns.get("right", 0)
    prov_rows = dict(f.providers)
    prov_y = {k: v[0] for k, v in prov_rows.items()}
    # A phone scan can bow or stretch the paper slightly. The global affine fit remains excellent at the code
    # grid while being a whole provider row out by the bottom of the page. Re-anchor these few rows to the
    # provider surnames printed on this page, so the writing is compared with the row actually beside it.
    for surname, expected_y in list(prov_y.items()):
        hits = [w.cy for w in words if 455 <= w.cx <= 565 and abs(w.cy - expected_y) <= 20
                and _similar(_key(w.text), _key(surname)) >= 0.78]
        if hits:
            hits.sort()
            prov_y[surname] = hits[len(hits) // 2]
    prov_top = min(prov_y.values(), default=H) - 12
    prov_bottom = max(f.yes_no.values(), default=H) + 7
    r.rects["provider_box"] = (418, prov_top, W - 4, max(prov_y.values(), default=H) + 6)
    if "incident_to" in f.yes_no:
        y = f.yes_no["incident_to"]
        r.rects["incident_to"] = (418, y - 6, W - 4, y + 6)

    rings = []
    for c in f.cells:
        if ringed(ink, c.rect):
            ring = (c.rect[0] - 10, c.rect[1] - 8, c.rect[2] + 10, c.rect[3] + 8)
            rings.append(ring)
            r.marks.append(Mark(c.code, c.label, c.box, tuple(round(v, 1) for v in ring), "circled"))

    def in_ring(b):
        for x0, y0, x1, y1 in rings:
            w = max(0.0, min(b.x1, x1) - max(b.x0, x0))
            h = max(0.0, min(b.y1, y1) - max(b.y0, y0))
            if w * h >= 0.7 * b.w * b.h:
                return True
        return False

    queue = blobs_of(ink)
    blob_attending: list[tuple[str, str]] = []
    while queue:
        b = queue.pop(0)
        if b.cy < HEADER_Y[1] + 2:
            continue
        # handwriting OCR could read (a note, a written-in code) is reported as writing, not as a mark
        if any(b.contains(w.cx, w.cy) and (w.x1 - w.x0) * (w.y1 - w.y0) >= 0.35 * b.w * b.h for w in writing):
            continue
        if b.cx > 415 and prov_top <= b.cy <= prov_bottom:
            row = _nearest_row({**prov_y, **f.yes_no}, b.cy, 6.5)
            on_left = left and b.x0 <= left + 16 and b.x1 >= left - 12
            on_right = right and b.x0 <= right + 9 and b.x1 >= right - 9
            on_names = b.x1 > 470 and b.x0 < 552
            if row in f.yes_no:
                answers = (["YES"] if on_left else []) + (["NO"] if on_right else [])
                target = r.incident_to if row == "incident_to" else r.tricare
                # The tiny uncovered tail of the printed YES / NO is not a mark. Actual circles and Xs in this
                # box are substantially taller and darker, even on a faint scan.
                if b.n >= 30 and b.h >= 8:
                    target.extend(a for a in answers if a not in target)
                else:
                    answers = []
                if not answers:
                    r.stray.append(b)
                continue
            if row is None:
                r.stray.append(b)
                continue
            # Provider circles often join an attending X or a long pen stroke into one multi-row component.
            # Its centroid can then land on the adjacent surname. Provider rows are scored locally below.
            if b.n >= 30 and b.h >= 8:
                if on_left:
                    blob_attending.append(("left", row))
                if on_right:
                    blob_attending.append(("right", row))
            if not (on_names or on_left or on_right):
                r.stray.append(b)
            continue
        top = b.cy < TOP_Y[1]
        here = {k: v for k, v in BOX_X.items() if (k in ("visit", "goggle", "procedures", "procedure dx")) == top}
        if in_ring(b):
            continue
        if b.w > 32:
            r.stray.append(b)
            continue
        home = [k for k, (x0, x1) in here.items() if x0 <= b.cx < x1]
        # an X or tick just left of a box's labels marks that box's row, even though it sits in the box before
        nxt = [k for k, (x0, x1) in here.items() if x0 > 30 and x0 - 18 <= b.cx <= x0 + 4]
        cy = lambda c: (c.rect[1] + c.rect[3]) / 2
        if b.h > 26:
            ys = sorted(cy(c) for k in home + nxt for c in cells_by_box.get(k, []))
            rows_y = [y for i, y in enumerate(ys) if i == 0 or y - ys[i - 1] > 3]
            pieces = split_tall(b, ink, rows_y)
            if pieces:
                queue[:0] = pieces
            else:
                r.stray.append(b)
            continue
        near = [c for k in home for c in cells_by_box.get(k, []) if abs(cy(c) - b.cy) <= 6]
        touching = [c for c in near if min(b.x1, c.rect[2] + 2) - max(b.x0, c.rect[0] - 2) >= 0.5 * b.w]
        if touching:
            # A slash directly through a printed code is too hard to distinguish from imperfectly removed print.
            # Circles are already found above; Xs and ticks belong in the narrow mark column beside a section.
            r.stray.append(b)
            continue
        elif nxt and min(b.w, b.h) >= 4 and max(b.w, b.h) <= 4 * min(b.w, b.h):
            near = [c for k in nxt for c in cells_by_box.get(k, []) if abs(cy(c) - b.cy) <= 6]
        else:
            # A mark can cross a printed code or sit in the small tick column immediately to the left of a
            # section. Ink elsewhere in the wide label area is usually a note, arrow, or uncovered print.
            r.stray.append(b)
            continue
        if not near:
            r.stray.append(b)
            continue
        best_dy = min(abs(cy(c) - b.cy) for c in near)
        row = [c for c in near if abs(cy(c) - b.cy) <= best_dy + 1.5]
        c = min(row, key=lambda c: abs((c.rect[0] + c.rect[2]) / 2 - b.cx))
        r.marks.append(Mark(c.code, c.label, c.box, b.rect(), "beside"))

    def band_count(x0: float, y0: float, x1: float, y1: float) -> int:
        scale = FORM_DPI / 72
        return int(ink[max(0, int(y0 * scale)):min(ink.shape[0], int(y1 * scale)),
                       max(0, int(x0 * scale)):min(ink.shape[1], int(x1 * scale))].sum())

    # Read each provider row from a narrow band. This remains stable when a large circle, an X and a trailing
    # pen stroke are connected into a single blob, and it ignores the narrow remnants of the printed grid.
    name_scores = {row: band_count(470, y - 5, 552, y + 5) for row, y in prov_y.items()}
    if name_scores and max(name_scores.values()) >= 50:
        strongest = max(name_scores.values())
        r.providers = [row for row, score in name_scores.items() if score >= max(45, strongest * .78)]
    for side, x in (("left", left), ("right", right)):
        if not x:
            continue
        scores = sorted(((band_count(x - 9, y - 5, x + 9, y + 5), row) for row, y in prov_y.items()),
                        reverse=True)
        if scores and scores[0][0] >= 20 and (len(scores) == 1 or scores[0][0] >= scores[1][0] + 10):
            r.attending.append((side, scores[0][1]))
        else:
            candidates = [row for marked_side, row in blob_attending if marked_side == side]
            if candidates:
                # A single locally-read provider circle fixes the row when the X and circle are one component.
                row = r.providers[0] if len(r.providers) == 1 else candidates[0]
                r.attending.append((side, row))
    # a piece of a circle that came apart where the pen lifted is part of that circle, not a mark of its own
    circles = [m.rect for m in r.marks if m.how == "circled"]

    def part_of_circle(m):
        x0, y0, x1, y1 = m.rect
        for cx0, cy0, cx1, cy1 in circles:
            overlap = min(y1, cy1) - max(y0, cy0)
            gap = max(cx0 - x1, x0 - cx1)
            if overlap >= 0.6 * (y1 - y0) and gap <= 8 and (x1 - x0) <= 12:
                return True
        return False
    r.marks = [m for m in r.marks if m.how == "circled" or not part_of_circle(m)]
    return r


# --------------------------------------------------------------------------- reading a batch

def _ocr_page_job(args) -> tuple[int, str]:
    """Worker process: OCR one page of a batch. Returns (page index, OCR lines as JSON)."""
    path, index = args
    import pymupdf
    with pymupdf.open(path) as doc:
        return index, ocr.dumps(ocr.ocr_page(doc[index]))


def read_batch(path: str, store=None, workers: int = 4, progress=None, cancel=None) -> list:
    """OCR lines for every page of a batch: from the store's cache when the file hasn't changed, else read now
    (several pages at a time) and cached."""
    import pymupdf
    st = os.stat(path)
    cached = store.get_ocr(path, st.st_size, st.st_mtime) if store is not None else {}
    with pymupdf.open(path) as doc:
        n = len(doc)
    pages: list = [cached.get(i) for i in range(n)]
    todo = [i for i in range(n) if pages[i] is None]
    done = n - len(todo)

    def keep(i, lines):
        nonlocal done
        pages[i] = lines
        done += 1
        if store is not None:
            store.put_ocr(path, st.st_size, st.st_mtime, i, lines)
        if progress:
            progress(done, n)

    if todo and workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=min(workers, len(todo))) as ex:
            for i, raw in ex.map(_ocr_page_job, [(path, i) for i in todo]):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("cancelled")
                keep(i, ocr.loads(raw))
    else:
        with pymupdf.open(path) as doc:
            for i in todo:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("cancelled")
                keep(i, ocr.ocr_page(doc[i]))
    return pages


# --------------------------------------------------------------------------- the printed schedule



@dataclass
class Booking:
    """A patient on the schedule page the batch starts with."""
    text: str
    info: dict
    x0: float
    y0: float
    page: int                 # 0-based page of the batch
    status: str = ""          # "no show" / "rescheduled" / "cancelled", from handwriting next to it
    status_text: str = ""

    @property
    def name(self) -> str:
        i = self.info
        return f"{i.get('last', '')}, {i.get('first', '')}".strip(", ")

    @property
    def dob(self) -> date | None:
        return self.info.get("dob")

    @property
    def medicare(self) -> str:
        """"primary", "secondary" or "" from what the booking says about Medicare."""
        ins, sec = self.info.get("insurance", ""), self.info.get("secondary", "")
        medi = r"\bMCR\b|\bMedicare\b|Medi-?Medi"
        if re.search(medi, ins, re.I) and not re.search(r"advantage|HMO", ins, re.I):
            return "primary"
        if re.search(medi, sec, re.I):
            return "secondary"
        if not ins and re.search(medi, self.text, re.I) and not re.search(r"advantage", self.text, re.I):
            return "primary"
        return ""

    @property
    def hmo(self) -> bool:
        return bool(re.search(r"\bHMO\b|\bSCMG\b|\bSharp\b|\bScripps\b|Health\s?Net|\bAuth\b", self.text, re.I))

    @property
    def new_referral(self) -> bool:
        """Booked the way new patients are: a chief complaint and a referring provider."""
        return bool(self.info.get("complaint") and self.info.get("referral"))


def _upright(lines: list) -> list:
    """A schedule scanned upside down comes back with every line right-to-left: turn its words the right way up
    (negated coordinates keep every distance, which is all the grouping below uses)."""
    rev = sum(1 for ln in lines if len(ln) > 1 and ln[0].x0 > ln[-1].x0)
    fwd = sum(1 for ln in lines if len(ln) > 1 and ln[0].x0 < ln[-1].x0)
    if rev <= fwd:
        return lines
    out = []
    for ln in lines:
        words = [ocr.Word(w.text, -w.x1, -w.y1, -w.x0, -w.y0) for w in ln]
        out.append(sorted(words, key=lambda w: w.x0))
    return sorted(out, key=lambda ln: (min(w.y0 for w in ln), ln[0].x0))


def _segments(line: list) -> list[list]:
    """OCR sometimes reads two side-by-side appointments as one line; split it where the words are far apart."""
    out, cur = [], [line[0]]
    for a, b in zip(line, line[1:]):
        if b.x0 - a.x1 > 14:
            out.append(cur)
            cur = []
        cur.append(b)
    out.append(cur)
    return out


_WORD = r"(?:[A-Z][a-z'][A-Za-z'\-]*|[A-Z]'[A-Z][a-z]+|Mc[A-Z][a-z]+)"
# "Last, First" (or "Last. First") followed soon by an account number or DOB: one appointment
_BOOKED = re.compile(rf"(?P<last>{_WORD}(?:[ \-](?:(?:de|la|del|van|von|da|di|le)\s)?{_WORD})*)\s?[,.]\s*"
                     rf"(?P<first>[A-Z][A-Za-z'\-]+)\S*(?P<rest>.{{0,40}}?)"
                     rf"(?=(?:Acc\w*\s*#?\s*)?(?<![/\-\d])\d{{4,7}}\b(?![/\-])|\bDO[BE]\b|\s*$)")
_NOT_NAME = {"Injection", "Injections", "Copay", "See", "Below", "Chair", "Talk", "No", "New", "Pt", "Ref", "Cc",
             "Sec", "Ins", "Call", "Per", "Needs", "Bring", "Medicare", "Notes", "Lunch"}
_TITLES = {"MD", "DO", "PA", "PAC", "NP", "DDS", "PHD", "DR", "OD", "DPM", "AUD"}
_TAGGED = re.compile(r"^[#*]|^(?:[A-Z]{2}|NB|JC|IC|KD|CC)\b")      # schedule cells start with the MA's initials
_ALL_DAY = re.compile(r"\d{1,2}:\d{2}\s*[ap]m\s*-", re.I)
_DOB_TEXT = re.compile(r"\bDO[BE]\s*[:;#]?\s*\d{1,2}/\d{1,2}/(\S*)", re.I)
_STATUS = (("no show", re.compile(r"\b[n4w][oae]\s?-?\s?s[hft]|\bno\s+s(?:h|k)[a-z]{2}\b|\bsh[oa]w\b|\bn/?s\b|\bdns\b", re.I)),
           ("rescheduled", re.compile(r"\bre-?s?ched|\bresh?ed|\br/s\b", re.I)),
           ("cancelled", re.compile(r"\bcanc|\bcnx\b|\bcanx\b", re.I)))


def _status_of(text: str) -> str:
    return next((name for name, rx in _STATUS if rx.search(text)), "")


def read_schedule(cover: list[tuple[int, list]]) -> list[Booking]:
    """Bookings on the schedule page(s): (page index, OCR lines) for each cover page."""
    from . import schedule
    bookings: list[Booking] = []
    notes = []
    for page, lines in cover:
        segs = [s for ln in _upright([ln for ln in lines if ln]) for s in _segments(ln)]
        segs.sort(key=lambda s: (s[0].y0, s[0].x0))
        texts = [" ".join(w.text for w in s) for s in segs]
        starts = []          # (segment, char offset, match)
        for i, t in enumerate(texts):
            found = list(_BOOKED.finditer(t))
            status_note = _status_of(t)
            begins_with_status = re.match(r"\s*(?:no\b|n/?s\b|dns\b|re-?s?ched|r/s\b|canc|cnx|canx)", t, re.I)
            if not found and status_note and len(t) <= 24 and (segs[i][0].x0 < 350 or begins_with_status):
                notes.append((page, segs[i], t))
            starts += [(i, m) for m in found]
        for n, (i, m) in enumerate(starts):
            s = segs[i]
            nxt = next((m2.start() for i2, m2 in starts[n + 1:] if i2 == i), None)
            parts = [texts[i][m.start():nxt]]
            # the rest of the appointment: lines just below in the same column, up to the next appointment
            last_y = s[0].y0
            if nxt is None:
                for j in range(i + 1, len(segs)):
                    t = segs[j]
                    if t[0].y0 - last_y > 14:
                        break
                    if abs(t[0].x0 - s[0].x0) < 8:
                        if _BOOKED.search(texts[j]):
                            break
                        parts.append(texts[j])
                        last_y = t[0].y0
            full = " ".join(parts)
            if _ALL_DAY.search(texts[i]) or not (_TAGGED.search(texts[i]) or re.search(r"\bDO[BE]\b|(?<![/\-\d])\d{4,7}\b", full)):
                continue
            last = m.group("last").split()
            while last and last[0] in _NOT_NAME:
                last.pop(0)
            first = m.group("first")
            if not last or re.sub(r"\W", "", first).upper() in _TITLES or re.sub(r"\W", "", last[-1]).upper() in _TITLES:
                continue        # a referring doctor ("Ref: Jane Roe, MD"), not a patient
            info = schedule.parse_subject(full)
            year = _DOB_TEXT.search(full)
            if info.get("dob") and (info["dob"].year < 1900 or (year and not re.match(r"\d{4}", year.group(1)))):
                info["dob"] = None      # cut off by the narrow column ("09/12/194", "07/20/19")
            info["last"], info["first"] = " ".join(last), first
            if not info.get("account"):
                acct = re.match(r"\s*(?:Acc\w*\s*#?\s*)?(\d{4,7})\b", full[m.end():])
                if acct:
                    info["account"] = acct.group(1)
            x0 = s[0].x0 if m.start() == 0 else next((w.x0 for w in s if w.text.startswith(m.group("last").split()[0][:3])), s[0].x0)
            bookings.append(Booking(full, info, x0, s[0].y0, page))
    for page, s, text in notes:
        x = s[0].x0
        y = (min(w.y0 for w in s) + max(w.y1 for w in s)) / 2
        all_same = [b for b in bookings if b.page == page]
        same = all_same
        left = [b for b in all_same if b.x0 <= x + 5]
        if left:   # the note is written in, or just right of, the column it's about
            col = max(b.x0 for b in left)
            same = [b for b in left if b.x0 >= col - 25]
        if not same:
            continue
        b = min(same, key=lambda b: abs(b.y0 + 5 - y))
        if abs(b.y0 + 5 - y) > 30 and all_same:
            # A handwritten arrow can carry "no show" into Outlook's task pane, far to the right of the schedule
            # column. In that case vertical position is the useful evidence.
            b = min(all_same, key=lambda b: abs(b.y0 + 5 - y))
        if abs(b.y0 + 5 - y) > 30 or b.status:
            continue
        b.status = _status_of(text)
        b.status_text = text.strip("→-> ")
    return bookings


# --------------------------------------------------------------------------- who each sheet belongs to

def _dates_in(text: str) -> list[tuple[int, int, int | None]]:
    """(month, day, year or None) a handwritten date may mean: "2-20-06", "9/28/65", "01/011950", "10626"."""
    t = text.translate(str.maketrans("OoIlSsZzB|", "0011552281"))
    out = []
    for m in re.finditer(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]?\s*(\d{4}|\d{2})(?!\d)", t):
        mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = int(y) if len(y) == 4 else None
        if len(y) == 2:
            year = 2000 + int(y)
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append((mo, d, year))
    digits = re.sub(r"\D", "", t)
    if len(digits) in (6, 8):
        mo, d = int(digits[:2]), int(digits[2:4])
        y = int(digits[4:]) if len(digits) == 8 else 2000 + int(digits[4:])
        if 1 <= mo <= 12 and 1 <= d <= 31 and (mo, d, y) not in out:
            out.append((mo, d, y))
    return out


def date_matches(text: str, d: date) -> str:
    """"full" when the handwriting reads as this date, "partial" when month and day do, else ""."""
    best = ""
    for mo, dd, y in _dates_in(text):
        if (mo, dd) == (d.month, d.day):
            if y is None or y == d.year or y % 100 == d.year % 100:
                return "full"
            best = "partial"
    return best


def _agreed_date_read(texts: list[str]) -> tuple[int, int, int | None] | None:
    """A date three independent OCR passes read the same way, or None when handwriting is ambiguous."""
    reads = [ds[0] for t in texts if (ds := _dates_in(t))]
    return reads[0] if len(reads) >= 3 and all(v == reads[0] for v in reads[1:]) else None


def _confusable_year(got: int | None, expected: int) -> bool:
    """Treat a lone handwritten 0/6 substitution as uncertain, not as evidence of a wrong year."""
    if got is None:
        return False
    a, b = str(got), str(expected)
    differences = [(x, y) for x, y in zip(a, b) if x != y]
    return len(a) == len(b) and len(differences) == 1 and set(differences[0]) == {"0", "6"}


def _ambiguous_day_read(text: str, expected: date, got_month: int, got_day: int) -> bool:
    """OCR often inserts a 1 before a single handwritten day when it loses the month/day slash."""
    normalized = text.translate(str.maketrans("OoIlSsZzB|", "0011552281"))
    prefix = re.match(r"\s*(\d{2})(.*)", normalized)
    no_separator_after_month = bool(prefix and not re.match(r"\s*[-/.]", prefix.group(2)))
    return got_month == expected.month and expected.day < 10 and got_day == 10 + expected.day \
        and no_separator_after_month


def _name_score(header: str, b: Booking) -> tuple[float, float]:
    """(last, first) name likeness 0..1 between the handwritten name and a booking."""
    toks = [_key(t) for t in re.findall(r"[A-Za-z][A-Za-z'\-]+", header) if len(t) >= 2]
    if not toks:
        return 0.0, 0.0
    last = _key(b.info.get("last", ""))
    first = _key(b.info.get("first", ""))
    parts = [_key(p) for p in re.split(r"[\s\-]+", b.info.get("last", "")) if len(p) > 1]
    sl = max((_similar(t, x) for t in toks for x in [last, *parts] if x), default=0.0)
    sf = max((_similar(t, first) for t in toks), default=0.0) if first else 0.0
    return sl, sf


def match_sheets(sheets: list, bookings: list[Booking], texts: list[str]) -> None:
    """Give each sheet the booking it belongs to (sheet.booking, sheet.match_why). Handwritten name and DOB, the
    printed pages behind the sheet (cards, forms) and the scan order all count."""
    if not bookings:
        return
    scores = []
    for si, sh in enumerate(sheets):
        for bi, b in enumerate(bookings):
            why, s = [], 0.0
            if sh.reading is not None:
                sl, sf = _name_score(sh.reading.name, b)
                if max(sl, sf) >= 0.75:
                    s += 45 * sl * (sl >= 0.6) + 25 * sf * (sf >= 0.6)
                    why.append(f"handwritten name ({sh.reading.name})")
                d = date_matches(sh.reading.dob_text, b.dob) if b.dob else ""
                if d:
                    s += 40 if d == "full" else 15
                    why.append("handwritten DOB")
            q = superbills.PatientQuery(first=b.info.get("first", ""), last=b.info.get("last", ""),
                                        account=b.info.get("account", ""), dob=b.dob)
            ps, pwhy = superbills.score_packet(texts, sh.packet, q)
            if ps:
                s += ps
                why += pwhy
            expected = bi * len(sheets) / max(1, len(bookings))
            s += max(0.0, 8 - 2 * abs(si - expected))
            if s >= 30:
                scores.append((s, si, bi, why))
    taken_s, taken_b = set(), set()
    for s, si, bi, why in sorted(scores, key=lambda t: -t[0]):
        if si in taken_s or bi in taken_b:
            continue
        taken_s.add(si)
        taken_b.add(bi)
        sheets[si].booking = bookings[bi]
        sheets[si].match_why = why
        sheets[si].match_score = round(s)


# --------------------------------------------------------------------------- the rules

NEW_VISIT = {"99204", "99205"}
FOLLOW_UP = {"99213", "99214", "99215"}
VISITS = NEW_VISIT | FOLLOW_UP
EMG_CODES = {"95886", "95908", "95909", "95910", "95911", "95913"}
# Both sides marked where the form (or ICD-10) has one code for both: (right, left, bilateral, what)
BILATERAL = [("G56.01", "G56.02", "G56.03", "carpal tunnel"), ("H81.11", "H81.12", "H81.13", "BPPV"),
             ("H61.21", "H61.22", "H61.23", "impacted cerumen"), ("I65.21", "I65.22", "I65.23", "carotid stenosis"),
             ("H83.2X1", "H83.2X2", "H83.2X3", "labyrinthine dysfunction"), ("H81.01", "H81.02", "H81.03", "Meniere's"),
             ("H53.121", "H53.122", "H53.123", "transient visual loss"),
             ("H81.311", "H81.312", "H81.23", "vestibular neuronitis")]
# An unspecified code next to a specific one of the same kind
UNSPECIFIED = [("H81.10", {"H81.11", "H81.12", "H81.13"}), ("H83.2X9", {"H83.2X1", "H83.2X2"}),
               ("H53.129", {"H53.121", "H53.122", "H53.123"}), ("I65.29", {"I65.21", "I65.22", "I65.23"}),
               ("H81.09", {"H81.01", "H81.02", "H81.03"}), ("R20.9", {"R20.1", "R20.2"}),
               ("G20.C", {"G20.A1", "G20.A2", "G20.B1", "G20.B2"}), ("D33.2", {"D33.0", "D33.1"}),
               ("G35.D", {"G35.A"})]
# A symptom that is part of a diagnosis also marked
SYMPTOM_OF = [({"R51.9", "R51"}, ("G43.",), "headache with a migraine diagnosis"),
              ({"R42"}, ("H81.1", "H81.0", "H81.2", "H81.3"), "dizziness with a vestibular diagnosis"),
              ({"R25.1"}, ("G25.0", "G20."), "tremor with essential tremor / Parkinson's"),
              ({"R41.3"}, ("G30.9", "G31.84", "F01.50", "F02.80"), "memory difficulty with a cognitive diagnosis")]
NOT_BILLABLE = {"R51", "M54.5", "M48.06", "M26.60", "M62.83", "G35"}
MOD25_TEST = {"MOD25 EMG/NCV": "EMG/NCV", "MOD25 TYMP": "tympanometry", "MOD25 VNG": "VNG",
              "MOD25 CDP": "CDP", "MOD25 EEG": "EEG"}
LEVELS = ("fix", "check", "note")


def is_procedure(code: str) -> bool:
    return bool(re.fullmatch(r"\d{5}(?:-50)?", code))


def is_diagnosis(code: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]\d{2}(?:\.[0-9A-Z]{1,4})?", code))


@dataclass
class Finding:
    level: str                       # "fix" (before upload), "check" (a person looks), "note" (worth knowing)
    text: str
    rect: tuple | None = None        # where on the sheet, form points


@dataclass
class Sheet:
    packet: superbills.Packet
    form: str = ""
    alignment: Alignment | None = None
    reading: Reading | None = None
    booking: Booking | None = None
    match_why: list = field(default_factory=list)
    match_score: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def page(self) -> int:
        return self.packet.superbill + 1

    @property
    def patient(self) -> str:
        if self.booking is not None:
            return self.booking.name
        return (self.reading.name if self.reading else "") or "(name not read)"

    def add(self, level: str, text: str, rect: tuple | None = None) -> None:
        self.findings.append(Finding(level, text, rect))

    @property
    def worst(self) -> str:
        return next((lv for lv in LEVELS if any(f.level == lv for f in self.findings)), "")


def _rect_of(r: Reading, *codes: str):
    rects = [m.rect for m in r.marks if m.code in codes]
    if not rects:
        return None
    return (min(x[0] for x in rects), min(x[1] for x in rects), max(x[2] for x in rects), max(x[3] for x in rects))


def _attending_sides(roster) -> dict:
    """"left" / "right" attending column -> the physician's roster entry (or just a name)."""
    out = {}
    for p in roster:
        if p.is_physician and p.side in ("left", "right") and p.side not in out:
            out[p.side] = p
    return out


def _marked_provider_rows(r: Reading) -> list[str]:
    """Provider rows evidenced by the clearest mark.

    The attending X is compact and centered on one row. A provider circle is wider and can touch the names above
    and below, so a single X row wins when both are available.
    """
    x_rows = list(dict.fromkeys(row for _side, row in r.attending))
    return x_rows if len(x_rows) == 1 else list(dict.fromkeys(r.providers))


def check_sheet(sh: Sheet, day: date, provider, roster, attending_rules: dict | None = None) -> None:
    """The pre-upload playbook's checks on one sheet; findings go on sh.findings."""
    r = sh.reading
    if r is None:
        sh.add("check", "Couldn't line this sheet up with the superbill form (skewed, cut off or a different "
                        "form): check it by eye.")
        return
    codes = set(r.codes)
    b = sh.booking
    W = 600

    # --- header
    for part, label in (("name", "Patient name"), ("dob", "DOB"), ("dos", "DOS")):
        if r.rects.get("ink_" + part, 0) < 12:
            x0, x1 = HEADER_X[part]
            sh.add("fix", f"{label} is blank.", (x0, HEADER_Y[0], x1, HEADER_Y[1]))
    x0, x1 = HEADER_X["dos"]
    dos_reads = r.dos_candidates or [r.dos_text]
    if r.rects.get("ink_dos", 0) >= 12 and not any(date_matches(t, day) == "full" for t in dos_reads):
        agreed = _agreed_date_read(dos_reads)
        if agreed is not None:
            mo, dd, year = agreed
            if (mo, dd) == (day.month, day.day) and not _confusable_year(year, day.year):
                sh.add("check", f"DOS reads \"{r.dos_text}\": the year doesn't look like {day:%Y}.",
                       (x0, HEADER_Y[0], x1, HEADER_Y[1]))
            elif (mo, dd) != (day.month, day.day) and not _ambiguous_day_read(r.dos_text, day, mo, dd):
                sh.add("check", f"DOS reads \"{r.dos_text}\", not {day:%m/%d/%Y}.",
                       (x0, HEADER_Y[0], x1, HEADER_Y[1]))
    dob_reads = r.dob_candidates or [r.dob_text]
    if b is not None and b.dob and r.rects.get("ink_dob", 0) >= 12 \
            and not any(date_matches(t, b.dob) == "full" for t in dob_reads):
        agreed = _agreed_date_read(dob_reads)
        if agreed is not None:
            x0, x1 = HEADER_X["dob"]
            sh.add("check", f"DOB reads \"{r.dob_text}\"; the schedule has {b.dob:%m/%d/%Y}.",
                   (x0, HEADER_Y[0], x1, HEADER_Y[1]))

    # --- schedule
    if b is None:
        sh.add("check", f"Not matched to anyone on the schedule page (name reads \"{r.name or '?'}\"). "
                        "Check the name, or whether the visit was added after the schedule printed.",
               (HEADER_X["name"][0], HEADER_Y[0], HEADER_X["name"][1], HEADER_Y[1]))
    elif b.status:
        sh.add("check", f"The schedule has \"{b.status_text}\" written next to {b.name}, but there is a superbill "
                        "for them. Was the patient seen?")

    # --- visit level and add-ons
    visits = sorted(codes & VISITS)
    procs = sorted(c for c in codes if is_procedure(c) and c not in VISITS)
    dx = sorted(c for c in codes if is_diagnosis(c))
    top_ink = [bl for bl in r.stray if TOP_Y[0] <= bl.cy < TOP_Y[1] and bl.n >= 30]
    if not visits and not procs:
        if top_ink:
            sh.add("check", "No visit level or procedure could be read, but there is ink in that area: check the "
                            "marked row.", _union([bl.rect() for bl in top_ink]))
        else:
            sh.add("fix", "No visit level or procedure is marked.", (18, 80, 166, 160))
    if len(visits) > 1:
        sh.add("fix", f"More than one visit level is marked: {', '.join(visits)}.", _rect_of(r, *visits))
    if "G2211" in codes and not visits:
        sh.add("fix", "G2211 is marked without a visit level.", _rect_of(r, "G2211"))
    mod25 = sorted(c for c in codes if c.startswith("MOD25"))
    if mod25 and not visits:
        sh.add("fix", "A modifier-25 line is marked without a visit level.", _rect_of(r, *mod25))
    if visits and set(visits) <= FOLLOW_UP and b is not None and b.new_referral:
        sh.add("note", "Booked like a new patient (chief complaint and referral on the schedule) but a follow-up "
                       "level is marked; new if not seen in 3 years.", _rect_of(r, *visits))
    if b is not None and b.medicare and "G2211" in codes and (mod25 or procs):
        sh.add("check", "G2211 with a same-day procedure or modifier-25 test: Medicare doesn't pay G2211 when the "
                        "visit carries modifier 25.", _rect_of(r, "G2211", *mod25))
    for c in mod25:
        test = MOD25_TEST[c]
        if c == "MOD25 EMG/NCV":
            if not codes & EMG_CODES and not getattr(sh, "emg_same_day", False):
                sh.add("check", "EMG/NCV modifier 25 is marked, but no EMG/NCV is on this sheet or on the day's "
                                "EMG/NCV batch for this patient.", _rect_of(r, c))
        else:
            sh.add("note", f"{test} modifier 25 is marked: make sure the {test} was done today (testing calendar).",
                   _rect_of(r, c))

    # --- procedures
    if "95992" in codes and not codes & {"H81.11", "H81.12", "H81.13"}:
        sh.add("fix", "95992 (canalith repositioning) needs a sided BPPV code (H81.11, H81.12 or H81.13): the form's "
                      "own rule.", _rect_of(r, "95992"))
    if "20553" in codes and "M54.2" in codes:
        sh.add("fix", "20553 (trigger points) with M54.2 (cervicalgia): the form says not to use them together.",
               _rect_of(r, "20553", "M54.2"))
    if codes & EMG_CODES and not superbills.norm_code(getattr(sh, "batch_code", "")).endswith("EMGNCV"):
        if getattr(sh, "emg_same_day", False):
            sh.add("fix", "EMG/NCV codes are on this clinic sheet and on the patient's EMG/NCV sheet the same day: "
                          "billed twice?", _rect_of(r, *(codes & EMG_CODES)))
        else:
            sh.add("note", "EMG/NCV codes on a clinic sheet (EMG/NCV days usually have their own batch).",
                   _rect_of(r, *(codes & EMG_CODES)))

    # --- diagnoses
    dx_ink = [bl for bl in r.stray if TOP_Y[1] <= bl.cy < 610 and bl.n >= 30]
    if not dx:
        if dx_ink:
            sh.add("check", "No diagnosis code could be read, but there is ink in the diagnosis area: check the "
                            "marked rows.", _union([bl.rect() for bl in dx_ink]))
        else:
            sh.add("fix", "No diagnosis is marked.", (12, 240, W, 560))
    for c in sorted(codes & set(FORM_CODE_NOTES)):
        sh.add("fix" if c in NOT_BILLABLE else "check", FORM_CODE_NOTES[c] + ".", _rect_of(r, c))
    for right, left, both, what in BILATERAL:
        if right in codes and left in codes:
            sh.add("fix", f"Right and left {what} are both marked: code {both} (bilateral) instead.",
                   _rect_of(r, right, left))
    for unspec, specific in UNSPECIFIED:
        if unspec in codes and codes & specific:
            sh.add("note", f"{unspec} (unspecified) is marked with {', '.join(sorted(codes & specific))}: usually dropped.",
                   _rect_of(r, unspec))
    for symptom, prefixes, what in SYMPTOM_OF:
        s = codes & symptom
        d = sorted(c for c in codes if c.startswith(prefixes))
        if s and d:
            sh.add("note", f"{', '.join(sorted(s))} with {', '.join(d)}: a symptom that's part of a confirmed "
                           f"diagnosis is usually not coded ({what}).", _rect_of(r, *s))

    # --- provider box
    expected = provider.surname.upper() if provider is not None else ""
    # the rendering provider is circled, or (some providers' habit) marked only by the attending X on their row
    circled = r.providers
    marked = _marked_provider_rows(r)
    box = r.rects.get("provider_box")
    if not marked:
        sh.add("fix", "No rendering provider is marked (name circled, or an X on their row).", box)
    elif len(marked) > 1:
        if expected and expected in marked:
            # A wide circle can touch an adjacent printed name. The batch's provider is still the unambiguous
            # choice when their row is one of the rows touched.
            marked = [expected]
        else:
            sh.add("check", f"More than one provider looks marked: {', '.join(c.title() for c in marked)}.", box)
    elif expected and marked[0] != expected:
        sh.add("check", f"{marked[0].title()} is marked as the provider, but this is {provider.name}'s batch.", box)
    rendering = next((p for p in roster if len(marked) == 1 and p.surname.upper() == marked[0]), provider)
    sides = _attending_sides(roster)
    att = sorted({side for side, _row in r.attending})
    if rendering is not None and not rendering.is_physician:
        if not att:
            sh.add("fix", "No attending physician is marked (X in the IMP or MP column on the provider's row).", box)
        elif len(att) > 1:
            sh.add("check", "Both attending columns (IMP and MP) are marked.", box)
        else:
            phys = sides.get(att[0])
            payer = " ".join([b.info.get("insurance", ""), b.text]) if b is not None else ""
            for pay, excluded in ((attending_rules or {}).get("payer_excludes") or {}).items():
                if phys is not None and re.search(rf"\b{re.escape(pay)}\b", payer, re.I) and phys.code in excluded:
                    sh.add("fix", f"{pay} patient with {phys.name} as attending: the clinic never bills {pay} under "
                                  f"{phys.code}.", box)
    medicare = b.medicare if b is not None else ""
    if not medicare and not (b is not None and b.info.get("insurance")) and payers_on_packet(sh) \
            and rendering is not None and not rendering.is_physician:
        medicare = "card"
    yn = r.incident_to
    if rendering is not None and not rendering.is_physician and medicare:
        if not yn:
            level = "fix" if medicare == "primary" else "check"
            why = {"primary": "Medicare patient", "secondary": "Medicare is secondary",
                   "card": "A Medicare card is in the packet"}[medicare]
            sh.add(level, f"{why}: YES or NO must be circled on \"Medicare incident-to met\".", r.rects.get("incident_to"))
        elif len(yn) > 1:
            sh.add("check", "Both YES and NO look marked on \"Medicare incident-to met\".", r.rects.get("incident_to"))
    if "YES" in yn and len(yn) == 1 and codes & NEW_VISIT:
        sh.add("fix", "Incident-to YES on a new-patient visit: a new patient can't be incident-to.", r.rects.get("incident_to"))
    if b is not None and b.hmo and not _has_auth_page(sh):
        sh.add("note", "HMO / auth plan: no authorization or referral page found behind the sheet. Compare what was "
                       "done with the auth.")

    # --- writing and stray ink
    hand_codes = [t for t, _rc in r.written if _CODE_TEXT.match(t.upper().replace("O", "0"))]
    if hand_codes:
        sh.add("note", "Handwritten codes on the sheet: " + ", ".join(dict.fromkeys(hand_codes)) + ". Make sure billing "
                       "can read them.", _union([rc for t, rc in r.written if t in hand_codes]))


def _union(rects: list):
    rects = [r for r in rects if r]
    if not rects:
        return None
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def payers_on_packet(sh: Sheet) -> bool:
    return "MEDICARE" in getattr(sh, "packet_payers", set())


def _has_auth_page(sh: Sheet) -> bool:
    return bool(getattr(sh, "auth_pages", 0))


# --------------------------------------------------------------------------- a day's batches

@dataclass
class BatchResult:
    path: str
    batch: superbills.Batch | None
    provider: object                     # rules.Provider for the batch's code, or None
    pages: int
    cover: list[int]
    bookings: list[Booking]
    sheets: list[Sheet]
    findings: list[Finding] = field(default_factory=list)   # about the batch as a whole

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def is_emg(self) -> bool:
        return self.batch is not None and superbills.norm_code(self.batch.code).endswith("EMGNCV")

    def counts(self) -> dict:
        out = {lv: 0 for lv in LEVELS}
        for sh in self.sheets:
            if sh.worst:
                out[sh.worst] += 1
        return out


def roster_for(cfg: dict | None) -> tuple[list, dict]:
    """The provider roster and attending rules from settings; when running from source without settings, the
    clinic's checked-in config."""
    from . import rules
    cfg = dict(cfg or {})
    if not cfg.get("roster"):
        path = os.path.join(os.path.dirname(HERE), "config", "clinic.json")
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = {**json.load(fh), **{k: v for k, v in cfg.items() if v}}
        except (OSError, ValueError):
            pass
    return rules.roster_from(cfg), cfg.get("attending_rules") or {}


def read_sheet(page, lines: list, versions: list[Form]) -> tuple[str, Alignment | None, Reading | None]:
    """Line the page up with each printing of the form, keep the one it matches best, and read the marks."""
    words = ocr.words_of(lines)
    best = None
    for f in versions:
        al = align(words, f.anchors)
        # the printing the page really is matches clearly more of its printed words, more tightly
        if al is not None and al.good and (best is None or (al.matched, -al.residual) > (best[1].matched, -best[1].residual)):
            best = (f, al)
    if best is None:
        return "", None, None
    f, al = best
    gray = render_in_form(page, al.matrix, f.size)
    reading = read_page(gray, words_in_form(words, al.matrix), f)
    _refine_header(page, f, al, reading)
    return f.name, al, reading


def _refine_header(page, f: Form, al: Alignment, reading: Reading) -> None:
    """Read the three handwritten header boxes once more at high resolution.

    Full-page OCR is intentionally 200 dpi so a large batch stays fast. Thin handwritten 1/0/6 digits benefit
    greatly from two small passes at different resolutions. A mismatch is flagged only when all three readings
    agree; this prevents a clear handwritten 6 from becoming 0 in a false DOS warning.
    """
    y0, y1 = HEADER_Y[0] - 10, HEADER_Y[1] + 8
    values = {part: [] for part in HEADER_X}
    for dpi in (300, 360):
        try:
            gray = render_in_form(page, al.matrix, f.size, dpi=dpi)
            k = dpi / 72
            clip = gray[max(0, int(y0 * k)):min(gray.shape[0], int(y1 * k)), :]
            words = ocr_image(clip, (f.size[0], y1 - y0))
        except Exception:
            continue
        words = [ocr.Word(w.text, w.x0, w.y0 + y0, w.x1, w.y1 + y0) for w in words]
        hand = [w for w in words if HEADER_Y[0] - 2 <= w.cy <= HEADER_Y[1] + 2
                and not any(_similar(_key(w.text), lab) >= 0.75 for lab in _HEADER_LABELS)]
        for part, (x0, x1) in HEADER_X.items():
            value = _ascii(" ".join(w.text for w in sorted(hand, key=lambda w: w.x0) if x0 <= w.cx < x1)).strip()
            if value:
                values[part].append(value)
    for part, found in values.items():
        if not found:
            continue
        attr = {"name": "name", "dob": "dob_text", "dos": "dos_text"}[part]
        old = getattr(reading, attr)
        if part in ("dob", "dos"):
            setattr(reading, part + "_candidates", [old, *found])
        setattr(reading, attr, found[0])


_AUTH = re.compile(r"authori[sz]ation|\bauth\s?#|referral\s+(?:number|#|no)|approved\s+(?:visits|services)", re.I)


def check_batch(path: str, pages: list, versions: list[Form] | None = None) -> BatchResult:
    """Read one batch (its OCR lines already in hand): the schedule, every superbill, who each belongs to."""
    import pymupdf
    versions = versions if versions is not None else forms()
    texts = [ocr.text_of(lines) for lines in pages]
    cover, packets = superbills.split_packets(texts)
    bookings = read_schedule([(i, pages[i]) for i in cover])
    sheets = []
    with pymupdf.open(path) as doc:
        for pk in packets:
            name, al, reading = read_sheet(doc[pk.superbill], pages[pk.superbill], versions)
            sh = Sheet(pk, name, al, reading)
            printed = "\n".join(texts[p] for p in pk.pages[1:])
            sh.packet_payers = superbills.payers_in(printed)
            sh.auth_pages = sum(1 for p in pk.pages[1:] if _AUTH.search(texts[p]))
            sheets.append(sh)
    match_sheets(sheets, bookings, texts)
    batch = superbills.parse_batch_name(path)
    return BatchResult(path, batch, None, len(pages), cover, bookings, sheets)


def _same_person(a: Sheet, b: Sheet) -> bool:
    if a.booking is not None and b.booking is not None:
        ia, ib = a.booking.info, b.booking.info
        if ia.get("dob") and ia.get("dob") == ib.get("dob"):
            return _similar(_key(ia.get("last", "")), _key(ib.get("last", ""))) >= 0.75
        return _key(ia.get("last", "")) == _key(ib.get("last", "")) and _key(ia.get("first", "")) == _key(ib.get("first", ""))
    na = a.reading.name if a.reading else ""
    nb = b.reading.name if b.reading else ""
    return bool(na and nb) and _similar(_key(na), _key(nb)) >= 0.8


def apply_rules(results: list[BatchResult], cfg: dict | None = None) -> None:
    """Run the playbook checks on every sheet, with what the day's other batches say (the EMG/NCV batch)."""
    from . import rules
    roster, att_rules = roster_for(cfg)
    by_day: dict = {}
    for res in results:
        if res.batch is not None:
            by_day.setdefault(res.batch.day, []).append(res)
    for res in results:
        res.provider = rules.by_code(roster, res.batch.code) if res.batch is not None else None
        day = res.batch.day if res.batch is not None else date.today()
        emg = [s for other in by_day.get(day, []) if other.is_emg and other is not res for s in other.sheets
               if s.reading is not None and set(s.reading.codes) & EMG_CODES]
        res.findings = []
        for sh in res.sheets:
            sh.findings = []
            sh.batch_code = res.batch.code if res.batch is not None else ""
            sh.emg_same_day = res.is_emg or any(_same_person(sh, e) for e in emg)
            check_sheet(sh, day, res.provider, roster, att_rules)
        matched = {id(sh.booking) for sh in res.sheets if sh.booking is not None}
        for b in res.bookings:
            if id(b) in matched:
                continue
            if b.status:
                res.findings.append(Finding("note", f"{b.name}: \"{b.status_text}\" on the schedule, no superbill (as expected)."))
            else:
                res.findings.append(Finding("check", f"{b.name} is on the schedule but no superbill was found for them "
                                                     "(seen? a phone visit? a sheet missing from the batch?)."))
        if not res.bookings:
            res.findings.append(Finding("check", "Couldn't read a schedule page at the front of the batch, so sheets "
                                                 "weren't checked against it."))
        if not res.sheets:
            res.findings.append(Finding("check", "No superbills found in this batch."))


def check_batches(paths: list[str], store=None, cfg: dict | None = None, workers: int = 4,
                  progress=None, cancel=None) -> list[BatchResult]:
    """Check every batch (PDF) given. progress(message) is called as it goes."""
    versions = forms()
    if not versions:
        raise RuntimeError("The superbill form map is missing from the app.")
    results = []
    for n, path in enumerate(paths, 1):
        label = f"{os.path.basename(path)} ({n} of {len(paths)})"
        say = (lambda d, t, label=label: progress(f"Reading {label}: page {d} of {t}")) if progress else None
        pages = read_batch(path, store, workers, say, cancel)
        if progress:
            progress(f"Checking {label}")
        results.append(check_batch(path, pages, versions))
    apply_rules(results, cfg)
    return results


def batch_files(paths: list[str]) -> list[str]:
    """PDFs to check: the files given, and the PDFs in any folder given, in name order."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(os.path.join(p, n) for n in os.listdir(p) if n.lower().endswith(".pdf")
                          and not n.lower().startswith(("superbill review", "superbill manual review")))
        elif p.lower().endswith(".pdf") and not os.path.basename(p).lower().startswith(
                ("superbill review", "superbill manual review")):
            out.append(p)
    return list(dict.fromkeys(os.path.abspath(p) for p in out))


# --------------------------------------------------------------------------- the review copy

LEVEL_COLOR = {"fix": (0.85, 0.1, 0.1), "check": (0.95, 0.55, 0.0), "note": (0.15, 0.4, 0.85)}
LEVEL_WORD = {"fix": "FIX", "check": "CHECK", "note": "NOTE"}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rows(results: list[BatchResult]) -> list[dict]:
    """Every finding as a flat row (for the list in the app and the CSV)."""
    out = []
    for res in results:
        prov = getattr(res.provider, "name", "") or (res.batch.code if res.batch else "")
        day = f"{res.batch.day:%m/%d/%Y}" if res.batch else ""
        for f in res.findings:
            out.append(dict(batch=res.name, day=day, provider=prov, page="", patient="", dob="", insurance="",
                            level=f.level, finding=f.text))
        for sh in res.sheets:
            b = sh.booking
            for f in sh.findings:
                out.append(dict(batch=res.name, day=day, provider=prov, page=sh.page, patient=sh.patient,
                                dob=f"{b.dob:%m/%d/%Y}" if b is not None and b.dob else "",
                                insurance=(b.info.get("insurance", "") if b is not None else ""),
                                level=f.level, finding=f.text))
    order = {lv: i for i, lv in enumerate(LEVELS)}
    return out if not out else sorted(out, key=lambda r: (r["batch"], r["page"] or 0, order[r["level"]]))


def write_csv(results: list[BatchResult], path: str) -> None:
    import csv
    data = rows(results)
    cols = ["batch", "day", "provider", "page", "patient", "dob", "insurance", "level", "finding"]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, cols)
        w.writeheader()
        w.writerows(data)
    os.replace(tmp, path)


def manual_rows(results: list[BatchResult]) -> list[dict]:
    """One human-review row per superbill. No handwritten code or pen-mark interpretation is exported."""
    out = []
    for res in results:
        provider = getattr(res.provider, "name", "") or (res.batch.code if res.batch else "")
        day = f"{res.batch.day:%m/%d/%Y}" if res.batch else ""
        for sh in res.sheets:
            b = sh.booking if sh.match_score >= 60 else None
            packet_end = max(sh.packet.pages, default=sh.packet.superbill) + 1
            out.append({
                "batch": res.name,
                "day": day,
                "batch_provider": provider,
                "source_page": sh.page,
                "packet_through_page": packet_end,
                "suggested_schedule_match_verify": b.name if b is not None else "",
                "dob_verify": f"{b.dob:%m/%d/%Y}" if b is not None and b.dob else "",
                "insurance_verify": b.info.get("insurance", "") if b is not None else "",
                "authorization_pages_detected": getattr(sh, "auth_pages", 0),
                "review_status_ok_fix_ask": "",
                "reviewer_notes": "",
            })
    return out


def write_manual_csv(results: list[BatchResult], path: str) -> None:
    """Write the fill-in worklist paired with the manual review packet."""
    import csv
    data = manual_rows(results)
    cols = list(data[0]) if data else ["batch", "day", "batch_provider", "source_page",
                                       "packet_through_page", "suggested_schedule_match_verify",
                                       "dob_verify", "insurance_verify", "authorization_pages_detected",
                                       "review_status_ok_fix_ask", "reviewer_notes"]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, cols)
        w.writeheader()
        w.writerows(data)
    os.replace(tmp, path)


_CSS = """
* { font-family: sans-serif; }
body { font-size: 9.5pt; line-height: 1.25; }
h1 { font-size: 15pt; margin: 0 0 4pt 0; }
h2 { font-size: 12pt; margin: 12pt 0 4pt 0; page-break-after: avoid; }
h3 { font-size: 10pt; margin: 8pt 0 2pt 0; page-break-after: avoid; }
p, li { margin: 2pt 0; }
.sheet { page-break-inside: avoid; break-inside: avoid; margin: 4pt 0 6pt 0; }
.fix { color: #d91a1a; font-weight: bold; }
.check { color: #e08a00; font-weight: bold; }
.note { color: #2666d9; font-weight: bold; }
.muted { color: #666666; }
table { border-collapse: collapse; }
td, th { padding: 1pt 5pt 1pt 0; text-align: left; vertical-align: top; }
"""


def _manual_summary_html(results: list[BatchResult]) -> str:
    h = ["<h1>Superbill manual review queue</h1>",
         '<p><b>This packet does not interpret handwriting, circles, arrows or checked codes.</b> The previous '
         "automatic mark detector was withdrawn because connected pen strokes could be assigned to the wrong "
         "rows. Each source superbill follows unchanged, with a consistent checklist beside it. Suggested schedule "
         "matches are only navigation aids and must be verified against the page.</p>",
         "<table><tr><th>Batch</th><th>Provider</th><th>Scheduled</th><th>Superbills</th><th>Difference</th></tr>"]
    for res in results:
        scheduled, sheets = len(res.bookings), len(res.sheets)
        difference = sheets - scheduled
        h.append(f"<tr><td>{_esc(res.name)}</td><td>{_esc(getattr(res.provider, 'name', '') or '?')}</td>"
                 f"<td>{scheduled}</td><td>{sheets}</td><td>{difference:+d}</td></tr>")
    h.append("</table><h2>Review sequence</h2><ol>"
             "<li>Verify patient, DOB and DOS against the schedule and note.</li>"
             "<li>Verify visit level, add-ons and performed procedures against the note.</li>"
             "<li>Verify diagnoses, specificity and form-rule conflicts.</li>"
             "<li>Verify rendering provider, attending column and Medicare incident-to selection.</li>"
             "<li>Verify authorization when HMO/AUTH paperwork is present.</li>"
             "<li>Record OK, FIX or ASK and the exact issue in the companion CSV.</li></ol>"
             '<p class="muted">The original PDFs are embedded unchanged. This queue deliberately contains no '
             "machine-drawn finding boxes.</p>")
    return "".join(h)


def write_manual_review(results: list[BatchResult], path: str) -> int:
    """Create the safe review queue: every source superbill, no automatic code claims and no finding boxes."""
    import io
    import pymupdf
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=_manual_summary_html(results), user_css=_CSS)
    where = pymupdf.Rect(36, 36, 576, 756)
    more = 1
    while more:
        dev = writer.begin_page(pymupdf.paper_rect("letter"))
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    out = pymupdf.open("pdf", buf.getvalue())
    count = 0
    for res in results:
        with pymupdf.open(res.path) as src:
            for sh in res.sheets:
                _manual_sheet_page(out, src, res, sh)
                count += 1
    tmp = path + ".tmp.pdf"
    out.save(tmp, garbage=3, deflate=True)
    out.close()
    os.replace(tmp, path)
    return count


def _manual_sheet_page(out, src, res: BatchResult, sh: Sheet) -> None:
    import pymupdf
    sp = src[sh.packet.superbill]
    W, H = sp.rect.width, sp.rect.height
    page = out.new_page(width=W + PANEL, height=max(H, 792))
    turn = sp.rotation == 180 or bool(sh.alignment is not None and sh.alignment.upside_down)
    page.show_pdf_page(pymupdf.Rect(0, 0, W, H), src, sh.packet.superbill, rotate=180 if turn else 0)
    b = sh.booking if sh.match_score >= 60 else None
    title = b.name if b is not None else f"Source page {sh.page} - match not confirmed"
    packet_end = max(sh.packet.pages, default=sh.packet.superbill) + 1
    html = [f"<h2>{_esc(title)}</h2>",
            f'<p class="muted">{_esc(res.name)}, source page {sh.page} of {res.pages}; packet through page '
            f"{packet_end}</p>"]
    if b is not None:
        bits = [f"DOB {b.dob:%m/%d/%Y}" if b.dob else "", b.info.get("insurance", ""),
                ("Sec: " + b.info["secondary"]) if b.info.get("secondary") else ""]
        html.append(f"<p><b>Suggested schedule match - verify:</b><br>{_esc(b.name)}"
                    f"<br>{_esc(', '.join(x for x in bits if x))}</p>")
    else:
        html.append("<p><b>Match the patient manually.</b> Handwriting was not used as a trusted identifier.</p>")
    auth_pages = getattr(sh, "auth_pages", 0)
    if auth_pages:
        html.append(f"<p><b>Authorization clue:</b> {auth_pages} packet page"
                    f"{'s' if auth_pages != 1 else ''} contained authorization wording; verify it.</p>")
    html.append("<h3>Reviewer checklist</h3><p>"
                "[ ] Patient / DOB / DOS<br>"
                "[ ] Visit level / add-ons<br>"
                "[ ] Procedures / same-day tests<br>"
                "[ ] Diagnoses / specificity<br>"
                "[ ] Provider / attending<br>"
                "[ ] Medicare incident-to<br>"
                "[ ] HMO / authorization</p>"
                "<h3>Decision</h3><p>[ ] OK &nbsp;&nbsp; [ ] FIX &nbsp;&nbsp; [ ] ASK</p>"
                '<p class="muted">Put the exact correction or question in the CSV worklist. Do not amend the '
                "source scan from this packet.</p>")
    story = pymupdf.Story(html="".join(html), user_css=_CSS + "body{font-size:9pt} h2{font-size:11pt}")
    panel = pymupdf.Rect(W, 0, W + PANEL, page.rect.height)
    page.draw_rect(panel, color=None, fill=(0.97, 0.97, 0.97))
    story.place(pymupdf.Rect(W + 10, 18, W + PANEL - 10, page.rect.height - 18))
    _draw_story(page, story)


def _tag(level: str) -> str:
    return f'<span class="{level}">{LEVEL_WORD[level]}</span>'


def _summary_html(results: list[BatchResult]) -> str:
    h = ["<h1>Superbill batch check</h1>",
         '<p class="muted"><b>FIX</b>: correct before upload (route to the provider if it is their call). '
         "<b>CHECK</b>: look at the page; the checker can't be sure. <b>NOTE</b>: worth knowing, usually no change. "
         "Every superbill with a FIX or CHECK follows this summary with the spot boxed. The checker reads "
         "handwriting and pen marks from a scan: it finds where to look, a person decides.</p>",
         "<table><tr><th>Batch</th><th>Provider</th><th>Scheduled</th><th>Sheets</th>"
         '<th class="fix">Fix</th><th class="check">Check</th><th class="note">Note</th></tr>']
    for res in results:
        c = res.counts()
        no_shows = sum(1 for b in res.bookings if b.status)
        sched = f"{len(res.bookings)}" + (f" ({no_shows} not seen)" if no_shows else "")
        h.append(f"<tr><td>{_esc(res.name)}</td><td>{_esc(getattr(res.provider, 'name', '') or '?')}</td>"
                 f"<td>{sched}</td><td>{len(res.sheets)}</td><td>{c['fix']}</td><td>{c['check']}</td><td>{c['note']}</td></tr>")
    h.append("</table>")
    form_issues = {}
    for res in results:
        for sh in res.sheets:
            for c in (sh.reading.codes if sh.reading else []):
                if c in FORM_CODE_NOTES:
                    form_issues[c] = form_issues.get(c, 0) + 1
    if form_issues:
        h.append("<h2>The printed form itself</h2><p>These codes are printed on the superbill but can't be billed as "
                 "printed. Fixing the form stops them coming back:</p><ul>")
        for c, n in sorted(form_issues.items(), key=lambda kv: -kv[1]):
            h.append(f"<li><b>{c}</b> ({n} sheet{'s' if n != 1 else ''}): {_esc(FORM_CODE_NOTES[c])}.</li>")
        h.append("</ul>")
    for res in results:
        h.append(f"<h2>{_esc(res.name)}</h2>")
        for f in res.findings:
            h.append(f"<p>{_tag(f.level)} {_esc(f.text)}</p>")
        for sh in res.sheets:
            if not sh.findings:
                continue
            b = sh.booking
            extra = []
            if b is not None and b.dob:
                extra.append(f"DOB {b.dob:%m/%d/%Y}")
            if b is not None and b.info.get("insurance"):
                extra.append(_esc(b.info["insurance"]))
            codes = ", ".join(sh.reading.codes) if sh.reading else ""
            h.append(f'<div class="sheet"><h3>Page {sh.page}: {_esc(sh.patient)}'
                     + (f' <span class="muted">({"; ".join(extra)})</span>' if extra else "") + "</h3>")
            if codes:
                h.append(f'<p class="muted">Read as: {_esc(codes)}</p>')
            for f in sorted(sh.findings, key=lambda f: LEVELS.index(f.level)):
                h.append(f"<p>{_tag(f.level)} {_esc(f.text)}</p>")
            h.append("</div>")
        clean = sum(1 for sh in res.sheets if not sh.findings)
        if clean:
            h.append(f'<p class="muted">{clean} other sheet{"s" if clean != 1 else ""}: nothing found.</p>')
    return "".join(h)


def write_review(results: list[BatchResult], path: str) -> int:
    """The review copy: a summary, then every superbill with a FIX or CHECK, with the spots boxed and listed
    beside it. Returns the number of sheet pages in it."""
    import io
    import pymupdf
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=_summary_html(results), user_css=_CSS)
    where = pymupdf.Rect(36, 36, 576, 756)
    more = 1
    while more:
        dev = writer.begin_page(pymupdf.paper_rect("letter"))
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    out = pymupdf.open("pdf", buf.getvalue())
    sheets = 0
    for res in results:
        flagged = [sh for sh in res.sheets if any(f.level in ("fix", "check") for f in sh.findings)]
        if not flagged:
            continue
        with pymupdf.open(res.path) as src:
            for sh in flagged:
                _sheet_page(out, src, res, sh)
                sheets += 1
    tmp = path + ".tmp.pdf"
    out.save(tmp, garbage=3, deflate=True)
    out.close()
    os.replace(tmp, path)
    return sheets


PANEL = 250


def _sheet_page(out, src, res: BatchResult, sh: Sheet) -> None:
    import pymupdf
    sp = src[sh.packet.superbill]
    W, H = sp.rect.width, sp.rect.height
    page = out.new_page(width=W + PANEL, height=max(H, 792))
    # show_pdf_page embeds the source's unrotated coordinate space, so honor a scanner's /Rotate 180 metadata
    # explicitly. The alignment fallback covers image-only PDFs whose pixels, rather than page metadata, are turned.
    turn = sp.rotation == 180 or bool(sh.alignment is not None and sh.alignment.upside_down)
    page.show_pdf_page(pymupdf.Rect(0, 0, W, H), src, sh.packet.superbill, rotate=180 if turn else 0)
    inv = ~pymupdf.Matrix(*sh.alignment.matrix) if sh.alignment is not None else None
    findings = sorted(sh.findings, key=lambda f: LEVELS.index(f.level))
    for n, f in enumerate(findings, 1):
        if f.rect is None or inv is None:
            continue
        r = pymupdf.Rect(f.rect) * inv
        if turn:
            r = pymupdf.Rect(W - r.x1, H - r.y1, W - r.x0, H - r.y0)
        r = pymupdf.Rect(r.x0 - 3, r.y0 - 3, r.x1 + 3, r.y1 + 3)
        color = LEVEL_COLOR[f.level]
        page.draw_rect(r, color=color, width=1.6)
        tag = pymupdf.Rect(r.x0, r.y0 - 11, r.x0 + 12, r.y0)
        page.draw_rect(tag, color=color, fill=color)
        page.insert_text((tag.x0 + 2.5, tag.y1 - 2), str(n), fontsize=8, color=(1, 1, 1), fontname="hebo")
    b = sh.booking
    html = [f"<h2>{_esc(sh.patient)}</h2>",
            f'<p class="muted">{_esc(res.name)}, page {sh.page} of {res.pages}</p>']
    if b is not None:
        bits = [f"DOB {b.dob:%m/%d/%Y}" if b.dob else "", b.info.get("insurance", ""),
                ("Sec: " + b.info["secondary"]) if b.info.get("secondary") else ""]
        html.append(f'<p class="muted">Schedule: {_esc(", ".join(x for x in bits if x))}</p>')
    if sh.reading is not None:
        shown_provider = _marked_provider_rows(sh.reading)
        html.append(f'<p class="muted">Read as: {_esc(", ".join(sh.reading.codes) or "nothing marked")}; '
                    f'provider {_esc(", ".join(p.title() for p in shown_provider) or "none")}; '
                    f'incident-to {_esc("/".join(sh.reading.incident_to) or "blank")}</p>')
    html.append("<ol>")
    for n, f in enumerate(findings, 1):
        html.append(f"<li>{_tag(f.level)} {_esc(f.text)}</li>")
    html.append("</ol>")
    story = pymupdf.Story(html="".join(html), user_css=_CSS + "body{font-size:9pt} h2{font-size:11pt}")
    box = pymupdf.Rect(W + 10, 18, W + PANEL - 10, page.rect.height - 18)
    page.draw_rect(pymupdf.Rect(W, 0, W + PANEL, page.rect.height), color=None, fill=(0.97, 0.97, 0.97))
    story.place(box)
    _draw_story(page, story)


def _draw_story(page, story) -> None:
    """Story.draw needs a device; render the placed story into a scratch PDF and stamp it on the page."""
    import io
    import pymupdf
    buf = io.BytesIO()
    w = pymupdf.DocumentWriter(buf)
    dev = w.begin_page(page.rect)
    story.draw(dev)
    w.end_page()
    w.close()
    with pymupdf.open("pdf", buf.getvalue()) as tmp:
        page.show_pdf_page(page.rect, tmp, 0, overlay=True)


def review_paths(out_dir: str, results: list[BatchResult]) -> tuple[str, str]:
    days = sorted({r.batch.day for r in results if r.batch is not None})
    stamp = f"{days[0]:%Y-%m-%d}" + (f" to {days[-1]:%Y-%m-%d}" if len(days) > 1 else "") if days else "batches"
    base = os.path.join(out_dir, f"Superbill manual review {stamp}")
    return base + ".pdf", base + ".csv"


# --------------------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="superbill_check", description="Check scanned superbill batches before upload.")
    ap.add_argument("paths", nargs="+", help="batch PDFs, or folders of them")
    ap.add_argument("--out", help="folder for the review PDF and CSV (default: next to the first batch)")
    ap.add_argument("--workers", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--no-cache", action="store_true", help="don't use the app's OCR cache")
    a = ap.parse_args(argv)
    from . import config
    files = batch_files(a.paths)
    if not files:
        print("No PDFs found.")
        return 1
    store = None
    if not a.no_cache:
        from .index import Store
        os.makedirs(config.DATA_DIR, exist_ok=True)
        store = Store(config.DB_PATH)
    results = check_batches(files, store, config.load(), a.workers, progress=lambda m: print(m, flush=True))
    out_dir = a.out or os.path.dirname(os.path.abspath(files[0]))
    pdf, csv_path = review_paths(out_dir, results)
    n = write_manual_review(results, pdf)
    write_manual_csv(results, csv_path)
    for res in results:
        print(f"{res.name}: {len(res.bookings)} scheduled, {len(res.sheets)} superbills")
    print(f"Manual review: {pdf} ({n} source sheets; no automatic mark boxes)\nWorklist: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
