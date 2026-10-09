"""A provider-created superbill: diagnoses, service lines, and who it belongs to.

Mirrors the progress note flow in note.py: blank() starts one, from_note() starts one from
the open note's coding (visit level, procedures, ICD-10), attach_patient() staples the
demographics and insurance from the chart onto it, and fill() writes the Superbill PDF the
office files. Like the note, a superbill is a plain dict of values so the screen can edit it
as JSON and the server can preview, save and serve it the same way.

The ICD-10 and CPT lists live next to the screen (web/icd.json, web/cpt.json), the same way
the drug list lives in web/meds.json: offline, searchable here and in the browser.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
ICD_JSON = os.path.join(HERE, "web", "icd.json")
CPT_JSON = os.path.join(HERE, "web", "cpt.json")


@lru_cache(maxsize=1)
def icd_database() -> list[dict]:
    """Every ICD-10 the superbill search knows: code, description, keywords."""
    try:
        with open(ICD_JSON, encoding="utf-8") as f:
            rows = json.load(f)
        if isinstance(rows, list) and rows:
            return rows
    except (OSError, ValueError):
        pass
    # without the JSON file, the practice's own diagnosis wordings still work
    from . import phrases
    return [{"code": code, "description": desc, "keywords": desc.lower()} for desc, code in phrases.DIAGNOSES]


@lru_cache(maxsize=1)
def cpt_database() -> list[dict]:
    """Every CPT/HCPCS the superbill search knows: code, description, category."""
    try:
        with open(CPT_JSON, encoding="utf-8") as f:
            rows = json.load(f)
        if isinstance(rows, list) and rows:
            return rows
    except (OSError, ValueError):
        pass
    return []


def search_icd(q: str, limit: int = 12) -> list[dict]:
    """ICD-10 rows whose code starts with (then description contains) what was typed."""
    q = (q or "").strip().lower()
    if not q:
        return []
    compact = q.replace(".", "").replace(" ", "")
    first, then = [], []
    for row in icd_database():
        code, desc = str(row.get("code", "")), str(row.get("description", ""))
        low = f"{code} {desc} {row.get('keywords', '')}".lower()
        if code.lower().replace(".", "").startswith(compact):
            first.append(row)
        elif q in code.lower() or all(w in low for w in q.split()):
            then.append(row)
    return (first + then)[:limit]


def search_cpt(q: str, limit: int = 12) -> list[dict]:
    """CPT rows whose code starts with (then description contains) what was typed."""
    q = (q or "").strip().lower()
    if not q:
        return []
    first, then = [], []
    for row in cpt_database():
        code, desc = str(row.get("code", "")), str(row.get("description", ""))
        if code.lower().startswith(q.replace(" ", "")):
            first.append(row)
        elif q in desc.lower() or q in code.lower():
            then.append(row)
    return (first + then)[:limit]


def icd_for(description: str) -> str:
    """The ICD-10 for an impression's wording, as the superbill prints it."""
    from .coding import icd_for as _icd_for
    return _icd_for(description)


# --------------------------------------------------------------------------- the superbill as data

def blank() -> dict:
    """A new, empty superbill. Diagnoses and procedures are lists of rows;
    demographics and insurance are filled by attach_patient()."""
    return {"Name": "", "DOB": "", "Date": "", "HPI-age": "", "sex": "", "acct": "", "phone": "",
            "insurance": "", "insurance_id": "", "secondary": "", "secondary_id": "",
            "rendering": "", "attending": "", "incident_to": "", "visit": "", "minutes": "", "payer": "",
            "diagnoses": [], "procedures": [], "notes": ""}


def coerce(values: dict) -> dict:
    """Values from the screen (JSON) into the superbill's types; unknown names are kept."""
    out = blank()
    out.update(values or {})
    for key in ("Name", "DOB", "Date", "HPI-age", "sex", "acct", "phone", "insurance", "insurance_id",
                "secondary", "secondary_id", "rendering", "attending", "incident_to", "visit", "minutes",
                "payer", "notes"):
        out[key] = "" if out.get(key) is None else str(out.get(key, ""))
    out["diagnoses"] = [{"code": str(d.get("code", "")), "description": str(d.get("description", ""))}
                        for d in (out.get("diagnoses") or []) if isinstance(d, dict)]
    procs = []
    for p in (out.get("procedures") or []):
        if not isinstance(p, dict):
            continue
        procs.append({"cpt": str(p.get("cpt", "")), "modifier": str(p.get("modifier", "")),
                      "units": str(p.get("units", "") or "1"), "dx": str(p.get("dx", "")),
                      "description": str(p.get("description", ""))})
    out["procedures"] = procs
    return out


def from_note(note_values: dict, payer: str = "") -> dict:
    """A superbill started from the open progress note: the visit level, add-ons and
    procedures from its coding, and an ICD-10 row per numbered impression."""
    from . import coding
    from .note import parse_date, age_on
    v = note_values or {}
    sb = blank()
    sb["Name"] = str(v.get("Name", ""))
    sb["DOB"] = str(v.get("DOB", ""))
    sb["Date"] = str(v.get("Date", ""))
    sb["HPI-age"] = str(v.get("HPI-age", ""))
    if not sb["HPI-age"]:
        dob, dos = parse_date(sb["DOB"]), parse_date(sb["Date"])
        sb["HPI-age"] = str(age_on(dob, dos)) if dob and dos else ""
    sb["sex"] = "F" if v.get("hpi-female") else ("M" if v.get("hpi-male") else "")
    sb["payer"] = payer or ""
    c = coding.code_note(v, payer)
    sb["visit"] = c.visit
    sb["minutes"] = str(c.minutes or "")
    for text, code in c.icd:
        desc = re.sub(r"^\d+\)\s*", "", text).strip()
        sb["diagnoses"].append({"code": code, "description": desc})
    if c.em:
        sb["procedures"].append({"cpt": c.em.code, "modifier": c.em.modifier or "", "units": "1",
                                 "dx": "", "description": c.em.title})
    for a in c.addons:
        units = re.search(r"x(\d+)", a.title)
        sb["procedures"].append({"cpt": a.code, "modifier": "", "units": units.group(1) if units else "1",
                                 "dx": "", "description": a.title})
    for p in c.procedures:
        sb["procedures"].append({"cpt": p.code, "modifier": p.modifier or "", "units": "1",
                                 "dx": "", "description": p.title})
    return sb


def attach_patient(values: dict, chart: dict | None) -> dict:
    """Demographics and insurance from the chart stapled onto the superbill.

    `chart` is what the provider's chart() returns: patient (last, first, dob, acct),
    facts (phone, insurance, secondary, member IDs) and the consensus insurance. Values
    the provider already typed are kept; only blank ones are filled in."""
    from .note import parse_date, age_on
    sb = coerce(values)
    chart = chart or {}
    patient = chart.get("patient") or {}
    facts = chart.get("facts") or {}
    last = str(patient.get("last", "") or facts.get("last", "")).strip()
    first = str(patient.get("first", "") or facts.get("first", "")).strip()
    if not sb["Name"] and (last or first):
        sb["Name"] = f"{last}, {first}".strip(" ,")
    sb["DOB"] = sb["DOB"] or str(patient.get("dob", "") or facts.get("dob", ""))
    sb["acct"] = sb["acct"] or str(patient.get("acct", "") or facts.get("account", ""))
    sb["phone"] = sb["phone"] or str(facts.get("phone", ""))
    sb["insurance"] = sb["insurance"] or str(facts.get("insurance", "") or chart.get("insurance", ""))
    sb["secondary"] = sb["secondary"] or str(facts.get("secondary", ""))
    sb["insurance_id"] = sb["insurance_id"] or str(facts.get("id1", ""))
    sb["secondary_id"] = sb["secondary_id"] or str(facts.get("id2", ""))
    if not sb["HPI-age"] and sb["DOB"]:
        dob = parse_date(sb["DOB"])
        dos = parse_date(sb["Date"]) or date.today()
        sb["HPI-age"] = str(age_on(dob, dos)) if dob else ""
    return sb


def validate(values: dict) -> list[str]:
    """What is missing before this superbill can be filed."""
    v = coerce(values)
    missing = []
    if not v["Name"].strip():
        missing.append("the patient's name")
    if not v["Date"].strip():
        missing.append("the date of service")
    if not v["diagnoses"]:
        missing.append("at least one diagnosis (ICD-10)")
    elif any(not d["code"].strip() for d in v["diagnoses"]):
        missing.append("an ICD-10 code on every diagnosis row")
    if not v["procedures"]:
        missing.append("at least one service line (CPT)")
    elif any(not p["cpt"].strip() for p in v["procedures"]):
        missing.append("a CPT code on every service line")
    if not v["rendering"].strip():
        missing.append("the rendering provider")
    return missing


# --------------------------------------------------------------------------- filing

def filename(last: str, first: str, dos: date) -> str:
    """The office's convention for a provider-made superbill: "LastFirst mm dd yy SB.pdf"."""
    from .note import filename as _note_filename
    base = _note_filename(last, first, dos)
    return base[:-4] + " SB.pdf"


def drafts_root(cfg: dict, data_dir: str) -> str:
    return cfg.get("superbill_draft_folder") or os.path.join(data_dir, "Superbills")


def final_root(cfg: dict) -> str:
    root = cfg.get("superbill_root") or ""
    if root:
        return root
    from .note import notes_root
    return notes_root(cfg)


def draft_path(cfg: dict, data_dir: str, last: str, first: str, dos: date) -> str:
    return os.path.join(drafts_root(cfg, data_dir), filename(last, first, dos))


def final_path(cfg: dict, last: str, first: str, dos: date) -> str:
    return os.path.join(final_root(cfg), str(dos.year), filename(last, first, dos))


def sidecar(pdf_path: str) -> str:
    return pdf_path + ".json"


def list_for(cfg: dict, data_dir: str, last: str = "", first: str = "") -> list[dict]:
    """Draft and filed superbills for a patient (newest first), by filename."""
    from .note import parse_date
    roots = [drafts_root(cfg, data_dir)]
    final = final_root(cfg)
    if final and os.path.isdir(final):
        roots.append(final)
    want = (re.sub(r"[^A-Za-z]", "", last or "") + re.sub(r"[^A-Za-z]", "", first or "")).lower()
    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, _dn, fn in os.walk(root):
            # the filed batches folder holds thousands of scans; only year folders carry provider sheets
            if dp != root and root == final and not re.fullmatch(r"19\d\d|20\d\d", os.path.basename(dp)):
                continue
            for n in fn:
                if not n.lower().endswith(" sb.pdf"):
                    continue
                if want and want not in re.sub(r"[^A-Za-z]", "", n).lower():
                    continue
                full = os.path.join(dp, n)
                m = re.search(r"(\d{2}) (\d{2}) (\d{2,4}) SB\.pdf$", n)
                dos = parse_date(f"{m.group(1)}/{m.group(2)}/{m.group(3)}") if m else None
                try:
                    modified = datetime.fromtimestamp(os.path.getmtime(full))
                except OSError:
                    modified = None
                out.append({"path": full, "name": n, "dos": dos.isoformat() if dos else "",
                            "kind": "draft" if root == roots[0] else "final",
                            "modified": modified.strftime("%m/%d/%Y %I:%M %p") if modified else ""})
                if len(out) >= 200:
                    break
    return sorted(out, key=lambda d: (d["dos"], d["modified"]), reverse=True)


# --------------------------------------------------------------------------- the PDF

def fill(values: dict, out_path: str) -> str:
    """Write the superbill PDF. The values are also kept in a JSON sidecar so the
    sheet can be reopened and edited; returns out_path."""
    import pymupdf

    from .version_info import VERSION
    v = coerce(values)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    ink, faint, red = (0.04, 0.12, 0.17), (0.45, 0.55, 0.6), (0.65, 0.1, 0.1)
    x0, top, width = 40, 36, 532

    def text(xy, s, size=10, color=ink, font="hebo"):
        page.insert_text(xy, s, fontsize=size, fontname=font, color=color)

    def line(y):
        page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x0 + width, y), color=(0.8, 0.84, 0.85), width=0.8)

    text((x0, top), "SUPERBILL", size=17)
    text((x0, top + 16), f"Ian Purcell, M.D., APC  ·  DizzyDoctor {VERSION}", size=8, color=faint, font="helv")
    text((x0 + width - 170, top + 4), f"DOS {v['Date'] or '**/**/****'}", size=11)
    line(top + 26)
    y = top + 44
    text((x0, y), v["Name"] or "****", size=13)
    demos = "  ·  ".join(x for x in (
        f"DOB {v['DOB']}" if v["DOB"] else "", f"{v['HPI-age']} y/o {v['sex']}".strip() if v["HPI-age"] or v["sex"] else "",
        f"Pt # {v['acct']}" if v["acct"] else "", v["phone"]) if x)
    if demos:
        text((x0, y + 15), demos, size=9, color=faint, font="helv")
        y += 15
    ins = "  ·  ".join(x for x in (
        f"Primary: {v['insurance']}" + (f"  ID {v['insurance_id']}" if v["insurance_id"] else "") if v["insurance"] else "",
        f"Secondary: {v['secondary']}" + (f"  ID {v['secondary_id']}" if v["secondary_id"] else "") if v["secondary"] else "") if x)
    text((x0, y + 15), ins or "No insurance on file", size=9, color=faint, font="helv")
    y += 30
    prov = "  ·  ".join(x for x in (
        f"Rendering: {v['rendering']}" if v["rendering"] else "Rendering: ****",
        f"Attending: {v['attending']}" if v["attending"] else "",
        f"Incident-to: {v['incident_to']}" if v["incident_to"] else "",
        f"{v['visit']} · {v['minutes']} min".strip(" ·") if v["visit"] or v["minutes"] else "") if x)
    text((x0, y), prov, size=9)
    y += 22

    def table(title, headers, rows, widths):
        nonlocal y, page
        text((x0, y), title, size=11)
        y += 6
        xs = [x0]
        for w in widths[:-1]:
            xs.append(xs[-1] + w)
        y += 10
        for i, hd in enumerate(headers):
            page.insert_text((xs[i], y), hd, fontsize=8, fontname="helv", color=faint)
        y += 4
        page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x0 + width, y), color=(0.8, 0.84, 0.85), width=0.8)
        y += 4                                    # a textbox's first baseline sits about 9pt below its top
        if not rows:
            page.insert_text((x0, y + 9), "—", fontsize=9, fontname="helv", color=faint)
            y += 30
            return
        for row in rows:
            if y > 700:
                page = doc.new_page(width=612, height=792)
                y = 50
            # the description column wraps; its lines set the row height
            desc = str(row[-1] or "")
            chars = max(20, int(widths[-1] / 5.2))
            lines_needed = max(1, (len(desc) // chars) + 1) if desc else 1
            row_h = 12 * min(lines_needed, 4) + 4
            for i, cell in enumerate(row):
                bold = (len(row) == 3 and i == 1) or (len(row) != 3 and i == 0)
                page.insert_textbox(pymupdf.Rect(xs[i], y, xs[i] + widths[i] - 8, y + row_h),
                                    str(cell or ""), fontsize=9, fontname="hebo" if bold else "helv", color=ink)
            y += row_h
        y += 20                                   # room for the next title's cap height (it is drawn at its baseline)

    table("Diagnoses (ICD-10)", ["#", "ICD-10", "Description"],
          [[str(i + 1), d["code"] or "***", d["description"]] for i, d in enumerate(v["diagnoses"])],
          [28, 76, 428])
    table("Services (CPT)", ["CPT", "Mod", "Units", "Dx", "Description"],
          [[p["cpt"] or "***", p["modifier"], p["units"], p["dx"], p["description"]] for p in v["procedures"]],
          [70, 44, 44, 44, 330])
    if v["notes"].strip():
        text((x0, y), "Notes", size=11)
        y += 14
        page.insert_textbox(pymupdf.Rect(x0, y, x0 + width, 740), v["notes"].strip(),
                            fontsize=9, fontname="helv", color=ink)
    foot = f"Provider-created superbill  ·  {datetime.now():%m/%d/%Y %I:%M %p}  ·  DizzyDoctor {VERSION}"
    text((x0, 762), foot, size=7, color=faint, font="helv")
    if validate(v):
        text((x0, 750), "DRAFT — " + "; ".join(validate(v)) + " still needed.", size=8, color=red)
    doc.set_metadata({"title": f"Superbill {v['Date']}".strip(), "subject": v["Name"],
                      "keywords": f"DizzyDoctor;superbill;dos={v['Date']};acct={v['acct']}",
                      "creator": "DizzyDoctor", "producer": "DizzyDoctor",
                      "creationDate": pymupdf.get_pdf_now(), "modDate": pymupdf.get_pdf_now()})
    tmp = out_path + ".tmp"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, out_path)
    try:
        with open(sidecar(out_path), "w", encoding="utf-8") as f:
            json.dump(v, f, indent=1)
    except OSError:
        pass
    return out_path


def read(path: str) -> dict:
    """A saved superbill's values, from its JSON sidecar (the PDF is the filed copy)."""
    if not path:
        return blank()
    for cand in (path if path.endswith(".json") else sidecar(path), path):
        if cand.endswith(".json") and os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8") as f:
                    return coerce(json.load(f))
            except (OSError, ValueError):
                return blank()
    return blank()


def imported(values: dict, row: dict | None) -> dict:
    """Fill a superbill from one locally uploaded CSV/JSON patient row.

    Header spelling varies between exports, so common aliases are accepted. Existing clinical work wins; an upload
    fills demographics and can seed diagnosis/service rows when those columns are present.
    """
    v, row = coerce(values), {re.sub(r"[^a-z0-9]+", "_", str(k).strip().lower()).strip("_"): val for k, val in (row or {}).items()}
    aliases = {
        "DOB": ("dob", "date_of_birth", "birth_date"), "acct": ("acct", "account", "account_number", "patient_id", "mrn"),
        "phone": ("phone", "telephone", "mobile"), "insurance": ("insurance", "primary_insurance", "payer"),
        "insurance_id": ("insurance_id", "member_id", "primary_member_id"), "secondary": ("secondary", "secondary_insurance"),
        "secondary_id": ("secondary_id", "secondary_member_id"), "rendering": ("rendering", "provider", "rendering_provider"),
        "attending": ("attending", "attending_provider"), "incident_to": ("incident_to",), "visit": ("visit", "visit_type"),
        "minutes": ("minutes", "time"), "sex": ("sex", "gender"), "notes": ("notes", "note"),
    }
    def first(keys):
        return next((str(row[k]).strip() for k in keys if k in row and str(row[k]).strip()), "")
    name = first(("name", "patient_name"))
    if not name:
        last, first_name = first(("last", "last_name", "surname")), first(("first", "first_name", "given_name"))
        name = f"{last}, {first_name}".strip(" ,")
    if not v["Name"] and name:
        v["Name"] = name
    for field, keys in aliases.items():
        val = first(keys)
        if val and not str(v.get(field) or "").strip():
            v[field] = val
    if not v["Date"]:
        v["Date"] = first(("date", "dos", "date_of_service", "service_date"))

    def split_codes(raw: str) -> list[str]:
        return [x.strip() for x in re.split(r"[;|,]", raw or "") if x.strip()]
    if not v["diagnoses"]:
        codes = split_codes(first(("icd10", "icd_10", "diagnosis_code", "diagnosis_codes", "dx")))
        descs = split_codes(first(("diagnosis", "diagnoses", "diagnosis_description", "dx_description")))
        v["diagnoses"] = [{"code": code, "description": descs[i] if i < len(descs) else ""} for i, code in enumerate(codes)]
    if not v["procedures"]:
        codes = split_codes(first(("cpt", "cpt_code", "cpt_codes", "procedure_code", "procedure_codes")))
        descs = split_codes(first(("procedure", "procedures", "procedure_description", "cpt_description")))
        modifiers = split_codes(first(("modifier", "modifiers")))
        v["procedures"] = [{"cpt": code, "modifier": modifiers[i] if i < len(modifiers) else "", "units": "1", "dx": "",
                             "description": descs[i] if i < len(descs) else ""} for i, code in enumerate(codes)]
    return coerce(v)


def combine(paths: list[str], out_path: str) -> str:
    """Combine provider-created sheets into one day/provider PDF batch."""
    import pymupdf
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc = pymupdf.open()
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        with pymupdf.open(path) as src:
            doc.insert_pdf(src)
    if not len(doc):
        doc.close()
        raise ValueError("The batch has no superbill pages.")
    tmp = out_path + ".tmp"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, out_path)
    return out_path
