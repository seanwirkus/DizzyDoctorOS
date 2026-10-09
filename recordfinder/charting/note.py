"""The progress note as data: fill the clinic's PDF form, read one back, name and file it.

A note is a dict of field values keyed by the PDF field names in note_schema.json:
checkboxes are True/False, everything else is a string. fill() writes those values into a
copy of the blank template, so the result is exactly the form the office files today.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "note_schema.json")
TEMPLATE_PATH = os.path.join(HERE, "templates", "progress-note-2025.pdf")

with open(SCHEMA_PATH, encoding="utf-8") as _f:
    SCHEMA: dict = json.load(_f)
FIELDS: dict[str, dict] = SCHEMA["fields"]

# Fields that describe the patient rather than the visit: a new note starts from the last one.
CARRY_FORWARD_PREFIXES = ("hist-", "family-", "social-", "d-", "x-study", "NeuroConsult", "hpi-")
CARRY_FORWARD_FIELDS = {"Name", "DOB", "HPI-age", "nkda", "Allergies", "meds-descr", "labs", "directory",
                        "ChiefComplaint", "OnsetDate", "DizzOnsetDate", "HLOnsetDate", "TinnitusOnsetDate",
                        "GAOnsetDate", "HOnsetDate", "impression-plan-descr"}
# Not carried: the attestation, the encounter time and the exam belong to the new visit.
VISIT_TYPES = ("neuro-consult", "neuro-eval", "neuro-progress", "neuro-urgent")


def blank() -> dict:
    """A new, empty note with the form's own defaults (normal exam boxes, questionnaire boxes)."""
    return {name: spec["default"] for name, spec in FIELDS.items()}


def coerce(values: dict) -> dict:
    """Values from the screen (JSON) into the note's types; unknown names are kept as given."""
    out = {}
    for name, val in values.items():
        spec = FIELDS.get(name)
        if spec is None:
            out[name] = val
        elif spec["type"] == "checkbox":
            out[name] = val in (True, "Yes", "On", "true", 1, "1")
        else:
            out[name] = "" if val is None else str(val)
    return out


def carry_forward(previous: dict, dos: date | None = None) -> dict:
    """A progress note for a return visit, started from the patient's last note."""
    note = blank()
    for name, val in previous.items():
        if name in CARRY_FORWARD_FIELDS or name.startswith(CARRY_FORWARD_PREFIXES):
            note[name] = val
    for name in VISIT_TYPES:
        note[name] = name == "neuro-progress"
    for name in ("hist-nochange", "family-nochange", "social-nochange"):
        note[name] = True
    if note.get("meds-descr"):
        from .medlist import carry       # stopped ones dropped, today's markers off: the next visit reviews the list again
        note["meds-descr"] = carry(note["meds-descr"])
    dos = dos or date.today()
    note["Date"] = dos.strftime("%m/%d/%Y")
    dob = parse_date(previous.get("DOB", ""))
    # the age is today's: from the DOB, or left for the visit to fill in (last visit's age would be stale)
    note["HPI-age"] = str(age_on(dob, dos)) if dob else ""
    note["ChiefComplaint-descr"] = followup_hpi(previous, note.get("HPI-age", ""))
    return note


def followup_hpi(previous: dict, age: str) -> str:
    """The practice's standard opening for a return visit, from the last note."""
    from . import phrases
    prev_hpi = str(previous.get("ChiefComplaint-descr", ""))
    sex = "female" if previous.get("hpi-female") else ("male" if previous.get("hpi-male") else "***")
    title = {"female": "Ms.", "male": "Mr."}.get(sex, "Mr./Ms.")
    last, _first = split_name(previous.get("Name", ""))
    hand = "R" if re.search(r"\bRHD\b", prev_hpi) else ("L" if re.search(r"\bLHD\b", prev_hpi) else "***")
    reason = (previous.get("ChiefComplaint") or "").strip() or "*****"
    if reason[:1].isupper() and reason[1:].islower():
        reason = reason.lower()
    opening = phrases.HPI_OPENING_FOLLOWUP.format(title=title, last=last or "****", age=age or "***", hand=hand, sex=sex,
                                                  reason=reason, last_seen=previous.get("Date", "**/**/**"))
    return opening + "\r\r" + phrases.HPI_CLOSERS[0]


def parse_date(text: str) -> date | None:
    m = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", text or "")
    if not m:
        return None
    y = int(m.group(3))
    y = y + 2000 if y < 100 else y
    try:
        return date(y, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def age_on(dob: date, when: date) -> int:
    return when.year - dob.year - ((when.month, when.day) < (dob.month, dob.day))


def split_name(name: str) -> tuple[str, str]:
    """"Last, First M" or "First Last" -> (last, first)."""
    name = " ".join((name or "").split())
    if "," in name:
        last, first = name.split(",", 1)
        return last.strip(), first.strip()
    parts = name.split(" ")
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (name, "")


def filename(last: str, first: str, dos: date) -> str:
    """The office's convention: "LastFirst mm dd yy.pdf" (compound names run together)."""
    clean = lambda s: re.sub(r"[^A-Za-z]", "", s or "")  # noqa: E731
    return f"{clean(last)}{clean(first)} {dos:%m %d %y}.pdf"


def notes_root(cfg: dict) -> str:
    folders = cfg.get("notes_folders") or []
    return folders[0] if folders else r"V:\1111 Progress Notes"


def final_path(cfg: dict, last: str, first: str, dos: date) -> str:
    return os.path.join(notes_root(cfg), str(dos.year), filename(last, first, dos))


def draft_path(cfg: dict, data_dir: str, last: str, first: str, dos: date) -> str:
    folder = cfg.get("prepped_notes_folder") or os.path.join(data_dir, "Prepped Notes")
    return os.path.join(folder, filename(last, first, dos))


def template_path(cfg: dict | None = None) -> str:
    override = (cfg or {}).get("note_template") or ""
    return override if override and os.path.isfile(override) else TEMPLATE_PATH


# The standard PDF fonts: any viewer draws any text in them, so a field set in one can keep it.
STANDARD_FONTS = {"Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique", "Times-Roman", "Times-Bold",
                  "Times-Italic", "Times-BoldItalic", "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"}


def _form_fonts(doc) -> dict[str, int]:
    """The form's own fonts that are standard PDF fonts, by resource name ("HeBo" -> its object)."""
    acro = doc.xref_get_key(doc.pdf_catalog(), "AcroForm")
    if acro[0] != "xref":
        return {}
    fonts = doc.xref_get_key(int(acro[1].split()[0]), "DR/Font")
    out = {}
    for name, xref in re.findall(r"/([^\s/<>\[\]()]+)\s+(\d+)\s+0\s+R", fonts[1] if fonts[0] == "dict" else ""):
        if doc.xref_get_key(int(xref), "BaseFont")[1].lstrip("/") in STANDARD_FONTS:
            out[name] = int(xref)
    return out


def _keep_font(doc, w, font: str, font_xref: int) -> None:
    """Draw a filled field in the form's own font again. pymupdf only draws Helv/Cour/TiRo/ZaDb itself and
    falls back to Helvetica, which turned the patient's name (Helvetica Bold on the form) regular."""
    ap = doc.xref_get_key(w.xref, "AP/N")
    if ap[0] != "xref" or w.text_font == font:
        return
    ap_xref = int(ap[1].split()[0])
    stream = doc.xref_stream(ap_xref) or b""
    drawn = re.sub(rb"/" + re.escape(w.text_font.encode()) + rb"(\s+[\d.]+\s+Tf)", b"/" + font.encode() + rb"\1", stream)
    if drawn == stream:
        return
    doc.update_stream(ap_xref, drawn)
    doc.xref_set_key(ap_xref, f"Resources/Font/{font}", f"{font_xref} 0 R")
    doc.xref_set_key(w.xref, "DA", f"(/{font} {w.text_fontsize:g} Tf 0 g)")


# Boxes the blank form already prints in its own styling (the practice directory in the left margin). They are only
# written when the practice has changed the text (Settings: practice directory).
KEEP_PRINTED = {"directory"}

# Narrative fields whose printed boxes are intentionally compact on the clinic form.  The AcroForm value stays whole
# (so reopening a filed note loses nothing); when the rendered box cannot show all of it, fill() also appends readable
# continuation pages.  This is deliberately based on the widget's real dimensions rather than character counts.
CONTINUATION_FIELDS = {
    "ChiefComplaint-descr": "History of present illness",
    "meds-descr": "Medications",
    "labs": "Laboratory results",
    "family-other-descr": "History / imaging continued",
    "impression-plan-descr": "Assessment and plan",
    "plan-descr": "Plan notes",
}


def _same_text(a, b) -> bool:
    norm = lambda t: " ".join(str(t or "").replace("\u2019", "'").split())  # noqa: E731
    return not norm(a) or norm(a) == norm(b)


def _wrapped_lines(text: str, width: float, fontsize: float) -> list[str]:
    """Wrap text using the same Helvetica metrics used on continuation pages."""
    import pymupdf

    font = pymupdf.Font("helv")
    max_width = max(12.0, width)
    lines: list[str] = []
    for paragraph in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split()
        current = ""
        for word in words:
            candidate = word if not current else current + " " + word
            if font.text_length(candidate, fontsize=fontsize) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            # Long identifiers and URLs still need to fit rather than running off the page.
            while word and font.text_length(word, fontsize=fontsize) > max_width:
                cut = max(1, int(len(word) * max_width / max(font.text_length(word, fontsize=fontsize), 1)))
                while cut > 1 and font.text_length(word[:cut], fontsize=fontsize) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines


def _overflows(text: str, rect, fontsize: float) -> bool:
    """Conservative visual overflow check for a multiline PDF widget."""
    size = fontsize if fontsize and fontsize > 4 else 8.0
    # Form appearances have a little internal padding and roughly 1.2x leading.
    available_width = max(12.0, rect.width - 5)
    available_lines = max(1, int((rect.height - 4) / (size * 1.2)))
    return len(_wrapped_lines(text, available_width, size)) > available_lines


def _append_continuations(doc, sections: list[tuple[str, str]], patient: str, dos: str) -> None:
    """Append clean, letter-sized narrative pages for form fields that overflow."""
    import pymupdf

    page_size = pymupdf.paper_rect("letter")
    margin, top, bottom = 54.0, 62.0, 50.0
    body_size, leading = 9.5, 13.0
    usable_width = page_size.width - margin * 2
    page = None
    y = top
    continuation_number = 0

    def new_page():
        nonlocal page, y, continuation_number
        page = doc.new_page(width=page_size.width, height=page_size.height)
        continuation_number += 1
        page.insert_text((margin, 35), "DIZZYDOCTOR — NOTE CONTINUATION", fontname="hebo", fontsize=12, color=(0.08, 0.18, 0.24))
        ident = " · ".join(x for x in (patient.strip(), dos.strip()) if x)
        if ident:
            page.insert_text((margin, 51), ident, fontname="helv", fontsize=8.5, color=(0.32, 0.35, 0.38))
        page.insert_text((page_size.width - margin - 82, page_size.height - 24), f"Continuation {continuation_number}", fontname="helv", fontsize=8, color=(0.42, 0.44, 0.47))
        y = top

    new_page()
    for label, text in sections:
        lines = _wrapped_lines(text, usable_width, body_size)
        if y + 30 + min(len(lines), 2) * leading > page_size.height - bottom:
            new_page()
        page.insert_text((margin, y), label, fontname="hebo", fontsize=11, color=(0.04, 0.38, 0.48))
        y += 18
        for line in lines:
            if y + leading > page_size.height - bottom:
                new_page()
                page.insert_text((margin, y), label + " (continued)", fontname="hebo", fontsize=10, color=(0.04, 0.38, 0.48))
                y += 17
            if line:
                page.insert_text((margin, y), line, fontname="helv", fontsize=body_size, color=(0.12, 0.13, 0.14))
            y += leading
        y += 12


def fill(values: dict, out_path: str, template: str = TEMPLATE_PATH) -> str:
    """Write the values into a copy of the blank form. Returns out_path."""
    import pymupdf

    values = coerce(values)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with pymupdf.open(template) as doc:
        fonts = _form_fonts(doc)
        continuation_metrics = {}
        for page in doc:
            for w in page.widgets():
                name = w.field_name or ""
                if name not in values or w.field_type_string == "Button":
                    continue
                val = values[name]
                if name in KEEP_PRINTED and _same_text(val, FIELDS.get(name, {}).get("default", "")):
                    continue                  # the margin as the form prints it: rewriting it would lose its formatting
                font = w.text_font            # what the form asks for, before pymupdf swaps it
                if w.field_type_string == "CheckBox":
                    w.field_value = bool(val)
                else:
                    # The office's notes use \r between lines inside a field.
                    w.field_value = str(val).replace("\r\n", "\n").replace("\n", "\r")
                    if name in CONTINUATION_FIELDS:
                        continuation_metrics.setdefault(name, (pymupdf.Rect(w.rect), float(w.text_fontsize or 8)))
                w.update()
                if font in fonts:
                    _keep_font(doc, w, font, fonts[font])
        continuations = []
        for name, label in CONTINUATION_FIELDS.items():
            text = str(values.get(name) or "").strip()
            metric = continuation_metrics.get(name)
            if text and metric and _overflows(text, *metric):
                continuations.append((label, text))
        if continuations:
            _append_continuations(doc, continuations, str(values.get("Name") or ""), str(values.get("Date") or ""))
        doc.need_appearances(True)
        tmp = out_path + ".tmp"
        doc.save(tmp, garbage=3, deflate=True)
    os.replace(tmp, out_path)
    return out_path


def read(path: str) -> dict:
    """The values of a filled note (any version of the form; unknown fields come along)."""
    import pymupdf

    values = blank()
    seen: set[str] = set()
    with pymupdf.open(path) as doc:
        for page in doc:
            for w in page.widgets():
                name = w.field_name or ""
                if not name or w.field_type_string == "Button":
                    continue
                if w.field_type_string in ("CheckBox", "RadioButton"):
                    values[name] = w.field_value in (True, "Yes", "On") or (
                        isinstance(w.field_value, str) and w.field_value not in ("Off", "", "No"))
                else:
                    text = "" if w.field_value is None else str(w.field_value)
                    # Name / DOB / Date repeat on every page: keep the first page's value unless it is blank.
                    if name not in seen or (not values[name] and text):
                        values[name] = text
                seen.add(name)
    return values


def render_page(path: str, page_number: int, zoom: float = 1.5) -> bytes:
    """A page of a note as PNG, for the on-screen preview."""
    import pymupdf

    with pymupdf.open(path) as doc:
        # Draw the fields as filed. With NeedAppearances set, MuPDF redraws every field itself and only knows
        # plain Helvetica, so the bold name / DOB / date would show regular here but bold in Acrobat.
        acro = doc.xref_get_key(doc.pdf_catalog(), "AcroForm")
        if acro[0] == "xref":
            doc.xref_set_key(int(acro[1].split()[0]), "NeedAppearances", "false")
        page = doc[page_number]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")


@dataclass
class Impression:
    number: int
    diagnosis: str
    plan: str = ""


def impressions(text: str) -> list[Impression]:
    """The numbered impressions in the impression/plan box: "1) Dizziness - Monitor clinically."."""
    out: list[Impression] = []
    for line in (text or "").replace("\r", "\n").split("\n"):
        m = re.match(r"^\s*(\d{1,2})\)\s*(.+)$", line)
        if m:
            body = m.group(2).strip()
            parts = re.split(r"\s+-\s+|:\s+", body, maxsplit=1)
            out.append(Impression(int(m.group(1)), parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""))
        elif out and line.strip():
            out[-1].plan = (out[-1].plan + " " + line.strip()).strip()
    return out


@dataclass
class NoteFile:
    path: str
    dos: date | None
    kind: str = "final"   # or "draft"
    modified: datetime = field(default_factory=datetime.now)
