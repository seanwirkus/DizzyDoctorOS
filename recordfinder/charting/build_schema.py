"""Build note_schema.json from the blank progress note PDF.

    python -m recordfinder.charting.build_schema

Re-run when the form changes. The PDF's field names, types, positions, choices and default
values come from the file; the section each field belongs to and its on-screen label come
from the tables below (the PDF only knows field names such as "worse-highgain").
"""
from __future__ import annotations

import json
import os
import re

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "templates", "progress-note-2025.pdf")
OUT = os.path.join(HERE, "note_schema.json")

SECTIONS = [
    ("header", "Patient", 1), ("visit", "Visit", 1), ("hpi", "History of present illness", 1),
    ("dizziness", "Dizziness and lack of coordination", 1), ("hearing", "Hearing loss", 1),
    ("tinnitus", "Tinnitus", 2), ("gait", "Gait ataxia", 2), ("headache", "Headache", 2),
    ("pmh", "Past medical / surgical history", 2), ("family", "Family history", 2),
    ("social", "Social history", 2), ("ros", "Review of systems", 2), ("labs", "Labs", 2),
    ("vitals", "Constitutional", 3), ("exam", "Otoneurologic exam", 3), ("goggles", "IR oculography / positional testing", 3),
    ("studies", "Imaging / studies", 4), ("impression", "Impression / plan", 4), ("time", "Encounter time", 4),
    ("plan", "Plan", 4), ("followup", "Next appointment", 4), ("referrals", "Referrals", 4),
    ("orders", "Imaging / orders", 4), ("attestation", "Attestation", 4),
]

# field name (or prefix, when it ends with "-") -> section
SECTION_OF = {
    "Name": "header", "DOB": "header", "Date": "header",
    "neuro-": "visit", "NeuroConsult": "visit", "directory": "visit",
    "HPI-age": "hpi", "hpi-": "hpi", "nkda": "hpi", "ChiefComplaint": "hpi", "OnsetDate": "hpi",
    "Allergies": "hpi", "meds-descr": "hpi",
    "dizziness": "dizziness", "Dizz": "dizziness", "assoc-": "dizziness", "worse-": "dizziness",
    "course-": "dizziness", "occur-": "dizziness", "duration-": "dizziness", "better-": "dizziness",
    "adl-": "dizziness", "k-": "dizziness",
    "hearingloss": "hearing", "HL": "hearing", "hl": "hearing", "b-": "hearing",
    "tinnitus": "tinnitus", "Tinnitus": "tinnitus", "t-": "tinnitus",
    "gaitataxia": "gait", "GA": "gait", "ga-": "gait",
    "headache": "headache", "HOnsetDate": "headache", "h-": "headache",
    "hist-": "pmh", "family-": "family", "social-": "social", "d-": "social", "review-": "ros", "labs": "labs",
    "const-": "vitals", "WEIGHT": "vitals", "HEIGHT": "vitals", "BMI": "vitals", "TEMP": "vitals",
    "normal": "exam", "abnormal": "exam", "findings": "exam", "dtr-": "exam", "weber": "exam", "rinne-": "exam",
    "debris-": "exam",
    "goggle": "goggles", "dixhall-": "goggles", "supine-": "goggles", "gaze-": "goggles", "see-epley": "goggles",
    "imp-": "goggles",
    "x-study": "studies", "impression-plan-descr": "impression",
    "appt-time": "time", "time-proc": "time", "addnl-time": "time",
    "plan-": "plan", "v-": "plan",
    "next-appt": "followup", "nextappt-descr": "followup",
    "referral1": "referrals", "referral2": "referrals",
    "Imaging": "orders", "PTcheck": "orders", "referral-for": "orders",
    "imp-mp": "attestation", "Imp/MP": "attestation",
}

# Labels. Checkbox names are "<prefix>-<key>"; the key's label is looked up here unless the
# whole name has an entry.
KEY_LABELS = {
    "vertigo": "Vertigo", "cbd": "Chronic baseline disequilibrium", "diseq": "Disequilibrium", "headache": "Headache",
    "attacks": "Attacks", "tinnitus": "Tinnitus", "hearingloss": "Hearing loss", "auralfullness": "Aural fullness",
    "aural": "Aural fullness", "as": "AS", "ad": "AD", "au": "AU", "standing": "Standing", "walking": "Walking",
    "walking-in-dark": "Walking in dark", "transitioning": "Transitioning to standing from sitting or supine",
    "pitching": "Pitching head up / down", "yaw": "Yaw to left or right", "supine": "Lying supine in bed",
    "stress": "Stress / fatigue", "opto": "Optokinetic hypersensitivity",
    "highgain": "Any high-gain head acceleration in nonspecific directions", "parox": "Paroxysmal",
    "chronic": "Chronic", "stable": "Stable", "worsening": "Worsening", "worse": "Worsening", "improving": "Improving",
    "improve": "Improving", "sitting": "Sitting", "seconds": "Seconds", "minutes": "Minutes", "hours": "Hours",
    "days": "Days", "sit": "Sit", "headstill": "Hold head still in a neutral position", "resolves": "Resolves on own",
    "meds": "Take medications", "other": "Other", "none": "No clear relieving or exacerbating factors",
    "mild": "Mild", "mod": "Moderate", "severe": "Severe", "sev": "Severe", "right": "Right", "left": "Left",
    "bilat": "Bilateral", "hearingaids": "Wear hearing aids", "loudbg": "Loud background noise",
    "notab": "None of the above", "hearingfluc": "Fluctuation", "fluc": "Fluctuation", "pulsatile": "Pulsatile",
    "nonpulsatile": "Non-pulsatile", "freq-hi": "Frequency high", "freq-med": "Frequency medium",
    "freq-low": "Frequency low", "cane": "Use cane", "walker": "Use walker", "chair": "Wheelchair",
    "sitrest": "Sit or rest", "walk": "Walk", "corners": "Turn corners quickly", "orthostasis": "Orthostasis",
    "veerleft": "Veer to left", "veerright": "Veer to right", "bradykinesia": "Bradykinesia",
    "mental": "Mental status decline", "weakness": "Weakness of legs", "sensory": "Sensory loss in legs",
    "bowel": "Bowel incontinence", "bladder": "Bladder incontinence", "falls-yes": "Falls: yes",
    "falls-no": "Falls: no", "global": "Global", "frontal": "Frontal", "occipital": "Occipital", "diet": "Dietary",
    "noise": "Loud background noise", "lights": "Lights", "norelief": "No clear relieving factors",
    "noexac": "No clear exacerbating factors", "sharp": "Sharp", "dull": "Dull", "throb": "Throbbing",
    "const": "Constant", "pressure": "Pressure", "scotomas": "Visual scotomas", "aura": "Aura", "hearing": "Hearing loss",
    "nausea": "Nausea", "emesis": "Vomiting", "photo": "Photophobia", "phono": "Phonophobia",
    "water": "Hydration therapy", "pq": "Also see patient questionnaire", "hypt": "Hypertension",
    "migraine": "Migraine headaches", "nochange": "No changes in follow-up", "trauma": "Hx of head trauma w/ LOC",
    "dyslip": "Dyslipidemia", "cad": "Coronary artery disease", "ear": "History of severe ear infections",
    "diab": "Diabetes", "arrh": "Cardiac arrhythmias", "ototox": "Hx of ototoxic drug exposure", "stroke": "Stroke",
    "cervical": "Cervical disease", "lastmens": "Last menstrual period", "mother-alive": "Mother alive",
    "mother-dec": "Mother deceased", "father-alive": "Father alive", "father-dec": "Father deceased",
    "denies": "Denies tobacco / ETOH / drugs", "tobacco": "Tobacco", "tobacco-remote": "Tobacco, remote",
    "etoh": "ETOH", "etoh-history": "Alcohol abuse history", "etoh-remote": "ETOH, remote", "drugs": "Drugs",
    "drugs-remote": "Drugs, remote", "pq-today": "See patient questionnaire dated today",
    "full10": "Full 10-system ROS performed and negative except as mentioned", "kerat": "Keratinous debris",
    "cerumen": "Cerumen occluding tympanic membrane", "normal": "Normal", "torsion": "Torsional", "fatigue": "Fatigable",
    "latency": "Latency", "atyp": "Atypical", "atypical": "Atypical", "leftbeat": "Left beat", "rightbeat": "Right beat",
    "downbeat": "Down beat", "upbeat": "Upbeat", "geo": "Geotropic", "ageo": "Ageotropic", "down": "Down beat",
    "up": "Upbeat", "leftevoked": "Left evoked", "rightevoked": "Right evoked", "rightpost": "Right posterior canal BPPV",
    "leftpost": "Left posterior canal BPPV", "rightcupu": "Right cupulolithiasis", "leftcupu": "Left cupulolithiasis",
    "rightnyst": "Right sustained nystagmus", "leftnyst": "Left sustained nystagmus",
    "leftlateral": "Left lateral canal BPPV", "rightlateral": "Right lateral canal BPPV",
    "leftanterior": "Left anterior canal BPPV", "rightanterior": "Right anterior canal BPPV",
    "balancelog": "Balance log", "halog": "HA log", "fall": "Fall precautions",
    "continue": "Continue home vis/vest exercises", "prophy": "Advised two forms of birth control on these medications",
    "adverse": "Discussed indications / contraindications / adverse effects of",
    "horizontal": "Keep head greater than earth horizontal", "sleep": "Sleep head 45 degrees",
    "return": "Return to clinic ASAP with vertigo / worsening symptoms",
    "driving": "Instructed not to drive when experiencing episodes of vertigo or disequilibrium",
    "weeks": "Weeks", "months": "Months", "male": "Male", "female": "Female", "consult": "Consult",
    "eval": "Evaluation", "progress": "Progress note", "urgent": "Urgent add-on",
}
NAME_LABELS = {
    "nkda": "NKDA", "dizziness": "Dizziness and lack of coordination", "dizziness-denies": "Patient denies",
    "hearingloss": "Hearing loss", "hearingloss-denies": "Patient denies", "tinnitus": "Tinnitus",
    "tinnitus-denies": "Patient denies", "gaitataxia": "Gait ataxia", "gaitataxia-denies": "Patient denies",
    "headache": "Headache", "headache-denies": "Patient denies", "hist-other": "Other",
    "see-epley": "See Epley Chair / CRM Chair report", "PTcheck": "PT", "imp-mp": "Attestation",
    "time-proc1": "Additionally, I spent up to", "time-proc2": "Additionally, I spent up to",
    "ga-assoc-weakness-denies": "Denies leg weakness", "ga-assoc-sensory-denies": "Denies leg sensory loss",
    "ga-assoc-bowel-denies": "Denies bowel incontinence", "ga-assoc-bladder-denies": "Denies bladder incontinence",
    "hist-trauma-denies": "Denies head trauma w/ LOC", "hist-ear-denies": "Denies severe ear infections",
    "hist-ototox-denies": "Denies ototoxic drug exposure", "hist-pq": "Also see patient questionnaire",
    "family-pq": "Also see patient questionnaire", "social-pq": "Also see patient questionnaire",
    "hist-nochange": "No changes in follow-up", "family-nochange": "No changes in follow-up",
    "social-nochange": "No changes in follow-up", "social-denies": "Denies tobacco / ETOH / drugs",
    "Name": "Patient name", "DOB": "Date of birth", "Date": "Date of service", "HPI-age": "Age",
    "ChiefComplaint": "Chief complaint", "OnsetDate": "Date of onset", "Allergies": "Allergies",
    "ChiefComplaint-descr": "HPI", "meds-descr": "Medications", "NeuroConsultReqBy": "Consult requested by",
    "NeuroConsultCC-1": "CC", "NeuroConsultCC-2": "CC", "NeuroConsultCC-3": "CC", "NeuroConsultPMD": "PMD",
    "directory": "Practice directory", "DizzyDesc": "Description", "DizzOnsetDate": "Date of onset",
    "DizzFreq": "Frequency", "better-thesemeds": "Medications", "better-factors": "Other relieving factors",
    "HLOnsetDate": "Date of onset", "HLdesc": "Description", "hl-betterif-other-descr": "Better if (other)",
    "hl-worseif-other-descr": "Worse if (other)", "TinnitusOnsetDate": "Date of onset", "tinnitus-descr": "Description",
    "t-other-descr": "Notes", "t-symptomsbetterif-other-descr": "Better if (other)",
    "t-symptomsworseif-other-descr": "Worse if (other)", "GAOnsetDate": "Date of onset", "gaitataxia-descr": "Description",
    "ga-other-descr": "Notes", "ga-weak-descr": "Weakness", "ga-sens-descr": "Sensory loss", "ga-bowel-descr": "Bowel",
    "ga-blad-descr": "Bladder", "ga-worseother-descr": "Worse if (other)", "ga-usecanedur-descr": "Cane duration",
    "ga-usewalkerdur-descr": "Walker duration", "ga-usewheelchairdur-descr": "Wheelchair duration",
    "HOnsetDate": "Date of onset", "headache-descr": "Description", "h-scale": "Pain scale /10",
    "h-freq-month": "Per month", "h-freq-wk": "Per week", "h-better-thesemeds": "Medications",
    "h-better-other-descr": "Better if (other)", "h-symptomsworseif-descr": "Worse if", "hist-lastmens-date": "LMP date",
    "family-other-descr": "History / imaging continued", "family-mother-age": "Mother age",
    "family-mother-hist": "Mother history", "family-father-age": "Father age", "family-father-hist": "Father history",
    "social-tobacco-freq": "Packs / day", "d-tobacco": "Tobacco", "d-etoh": "ETOH", "d-drugs": "Drugs", "labs": "Labs",
    "const-bp": "BP", "const-hr": "HR", "const-rr": "RR", "WEIGHT": "Weight", "HEIGHT-ft": "Height ft",
    "HEIGHT-in": "Height in", "BMI": "BMI", "TEMP": "Temperature", "const-pain": "Pain /10", "weber": "Weber",
    "rinne-ab": "Rinne A>B", "rinne-ba": "Rinne B>A", "goggle-dr": "Oculography performed by", "goggle-date": "Date",
    "goggle-treatment-x": "Treatment", "impression-plan-descr": "Impression / plan",
    "appt-time": "Total encounter time (minutes)", "addnl-time": "Separate time 1", "addnl-time-2": "Separate time 2",
    "plan-descr": "Plan notes", "plan-adverse-descr": "Medications discussed", "v-vertigo": "", "v-worse": "",
    "next-appt": "Next appointment in", "nextappt-descr": "Note", "referral1-doc": "Physician",
    "referral1-spec": "Specialty", "referral1-diag": "Diagnosis", "referral2-doc": "Physician",
    "referral2-spec": "Specialty", "referral2-diag": "Diagnosis", "Imaging": "Imaging / order 1",
    "Imaging-2": "Imaging / order 2", "referral-for": "PT referral for", "Imp/MP": "Attending statement",
    "k-relief": "", "k-exac": "", "b-relief": "", "b-exac": "",
}
for i in range(1, 6):
    NAME_LABELS[f"x-study{i}"] = f"Study {i}"
    NAME_LABELS[f"x-study{i}-date"] = "Date"
    NAME_LABELS[f"x-study{i}-result"] = "Result"
for side in ("r", "l"):
    for k, lab in (("bi", "Biceps"), ("tri", "Triceps"), ("br", "Brachioradialis"), ("pat", "Patellar"), ("ach", "Achilles")):
        NAME_LABELS[f"dtr-{side}-{k}"] = f"{'Right' if side == 'r' else 'Left'} {lab}"

# Sub-groups inside a section (the printed sub-headings), by checkbox prefix.
GROUP_OF = {
    "assoc-": "Associated with", "worse-": "Worse with", "course-": "Symptom course", "occur-": "Occurs when",
    "duration-": "Duration", "better-": "Symptoms better if", "adl-": "Affects ADLs",
    "hl-": "Side", "hlcourse-": "Symptom course", "hlbetter-": "Symptoms better if", "hlworse-": "Worse if",
    "hlassoc-": "Attacks associated with", "hldur-": "Duration",
    "t-better-": "Symptoms better if", "t-worse-": "Worse if", "t-adl-": "Affects ADLs", "t-dur-": "Duration",
    "t-course-": "Symptom course", "t-assoc-": "Attacks associated with",
    "ga-better-": "Symptoms better if", "ga-worse-": "Worse if", "ga-course-": "Symptom course",
    "ga-assoc-": "Associated with", "ga-falls-": "Falls",
    "h-worse-": "Symptoms worse if", "h-adl-": "Affects ADLs", "h-course-": "Symptom course / character",
    "h-dur-": "Duration", "h-assoc-": "Associated with", "h-better-": "Symptoms better if",
    "dixhall-right-": "Dix-Hallpike right", "dixhall-left-": "Dix-Hallpike left",
    "supine-center-": "Supine, head center", "supine-right-": "Supine, head right", "supine-left-": "Supine, head left",
    "gaze-right-": "Gaze right", "gaze-left-": "Gaze left", "imp-": "Study impression", "next-appt-": "Unit",
    "neuro-": "Visit type", "hpi-": "Sex", "family-mother-": "Mother", "family-father-": "Father",
}
# Sets where one choice excludes the others (drawn as a segmented control).
EXCLUSIVE = [
    ("neuro-consult", "neuro-eval", "neuro-progress", "neuro-urgent"), ("hpi-male", "hpi-female"),
    ("duration-seconds", "duration-minutes", "duration-hours", "duration-days"),
    ("adl-mild", "adl-mod", "adl-severe"), ("course-parox", "course-chronic"),
    ("course-stable", "course-worsening", "course-improving"), ("hl-right", "hl-left", "hl-bilat"),
    ("hlcourse-parox", "hlcourse-chronic"), ("hlcourse-stable", "hlcourse-worsening", "hlcourse-improving", "hlcourse-attacks"),
    ("hldur-seconds", "hldur-minutes", "hldur-hours", "hldur-days"), ("t-pulsatile", "t-nonpulsatile"),
    ("t-right", "t-left", "t-bilat"), ("t-adl-mild", "t-adl-mod", "t-adl-severe"),
    ("t-dur-seconds", "t-dur-minutes", "t-dur-hours", "t-dur-days"), ("t-course-parox", "t-course-chronic"),
    ("t-course-stable", "t-course-worsening", "t-course-improving", "t-course-attacks"),
    ("t-course-freq-hi", "t-course-freq-med", "t-course-freq-low"), ("ga-course-parox", "ga-course-chronic"),
    ("ga-course-stable", "ga-course-worsening", "ga-course-improving", "ga-course-attacks"),
    ("ga-falls-yes", "ga-falls-no"), ("h-adl-mild", "h-adl-mod", "h-adl-sev"), ("h-course-parox", "h-course-chronic"),
    ("h-course-stable", "h-course-worse", "h-course-improve", "h-course-attacks"),
    ("h-dur-seconds", "h-dur-minutes", "h-dur-hours", "h-dur-days"),
    ("family-mother-alive", "family-mother-dec"), ("family-father-alive", "family-father-dec"),
    ("next-appt-days", "next-appt-weeks", "next-appt-months"),
    ("dizziness", "dizziness-denies"), ("hearingloss", "hearingloss-denies"), ("tinnitus", "tinnitus-denies"),
    ("gaitataxia", "gaitataxia-denies"), ("headache", "headache-denies"),
    ("hist-trauma", "hist-trauma-denies"), ("hist-ear", "hist-ear-denies"), ("hist-ototox", "hist-ototox-denies"),
    ("ga-assoc-weakness", "ga-assoc-weakness-denies"), ("ga-assoc-sensory", "ga-assoc-sensory-denies"),
    ("ga-assoc-bowel", "ga-assoc-bowel-denies"), ("ga-assoc-bladder", "ga-assoc-bladder-denies"),
]

# The exam table on page 3: (normal box, abnormal mark, findings text, group, label). The printed
# findings fields are numbered one behind the normal boxes from row 37 on, and row 36 holds the
# DTR grid instead of a findings line.
EXAM_ROWS = [
    ("normal1", "abnormal1", "findings1", "Constitutional", "Appearance is WD / WN / NAD"),
    ("normal2", "abnormal2", "findings2", "Head", "Normocephalic / atraumatic"),
    ("normal3", "abnormal3", "findings3", "Eyes", "Fundi visualized, no papilledema or hemorrhage"),
    ("normal4", "abnormal4", "findings4", "Eyes", "Visual fields intact"),
    ("normal5", "abnormal5", "findings5", "Eyes", "PERRLA"),
    ("normal6", "abnormal6", "findings6", "ENT", "Oropharynx"),
    ("normal7", "abnormal7", "findings7", "ENT", "Tympanic membranes & ear canals w/ microscopy"),
    ("normal8", "abnormal8", "findings8", "Cardiovascular", "Regular rate & rhythm"),
    ("normal9", "abnormal9", "findings9", "Cardiovascular", "No murmurs or carotid bruits"),
    ("normal10", "abnormal10", "findings10", "Lungs", "Clear to auscultation bilaterally"),
    ("normal11", "abnormal11", "findings11", "Extremities", "No clubbing / cyanosis / edema"),
    ("normal12", "abnormal12", "findings12", "Neurologic", "Oriented to person / place / time"),
    ("normal13", "abnormal13", "findings13", "Neurologic", "Intact recent & remote memory"),
    ("normal14", "abnormal14", "findings14", "Neurologic", "Appropriate mood & affect"),
    ("normal15", "abnormal15", "findings15", "Neurologic", "Normal attention span / concentration"),
    (None, "abnormal17-20", "findings17-20", "Neurologic", "Normal language functions"),
    ("normal21", "abnormal21", "findings21", "Neurologic", "Normal fund of knowledge"),
    ("normal22", "abnormal22", "findings22", "Neurologic", "No neglect or inattention"),
    ("normal23", "abnormal23", "findings23", "Cranial nerves", "Cranial nerve II"),
    ("normal24", "abnormal24", "findings24", "Cranial nerves", "Cranial nerves III, IV & VI"),
    ("normal25", "abnormal25", "findings25", "Cranial nerves", "Cranial nerve V"),
    ("normal26", "abnormal26", "findings26", "Cranial nerves", "Cranial nerve VII"),
    ("normal27", "abnormal27", "findings27", "Cranial nerves", "Cranial nerve VIII (Weber / Rinne)"),
    ("normal28", "abnormal28", "findings28", "Cranial nerves", "Cranial nerves IX & X"),
    ("normal29", "abnormal29", "findings29", "Cranial nerves", "Cranial nerve XI"),
    ("normal30", "abnormal30", "findings30", "Cranial nerves", "Cranial nerve XII"),
    ("normal31", "abnormal31", "findings31", "Motor", "5/5 strength x 4 extremities"),
    ("normal32", "abnormal32", "findings32", "Motor", "Normal tone throughout"),
    ("normal33", "abnormal33", "findings33", "Sensory", "Intact touch / position / vibration"),
    ("normal34", "abnormal34", "findings34", "Sensory", "Intact pin / 10 g monofilament / temperature"),
    ("normal36", "abnormal36", None, "Reflexes", "DTRs symmetric in all 4 extremities"),
    ("normal37", "abnormal37", "findings36", "Reflexes", "Toes downgoing bilaterally"),
    ("normal38", "abnormal38", "findings37", "Coordination", "No ataxia on FTN and HTS"),
    ("normal39", "abnormal39", "findings38", "Coordination", "Normal rapid finger movements"),
    ("normal40", "abnormal40", "findings39", "Gait", "Normal based gait"),
]


def section_for(name: str) -> str:
    if name in SECTION_OF:
        return SECTION_OF[name]
    best = ""
    for key, sec in SECTION_OF.items():
        if key.endswith("-") and name.startswith(key) and len(key) > len(best):
            best, found = key, sec
        elif not key.endswith("-") and name.startswith(key) and len(key) > len(best):
            best, found = key, sec
    return found if best else "misc"


def label_for(name: str, ftype: str) -> str:
    if name in NAME_LABELS:
        return NAME_LABELS[name]
    if ftype != "checkbox":
        return name
    for prefix in sorted(GROUP_OF, key=len, reverse=True):
        if name.startswith(prefix):
            key = name[len(prefix):]
            return KEY_LABELS.get(key, key.replace("-", " ").capitalize())
    key = name.split("-", 1)[-1]
    return KEY_LABELS.get(key, key.replace("-", " ").capitalize())


def group_for(name: str) -> str:
    for prefix in sorted(GROUP_OF, key=len, reverse=True):
        if name.startswith(prefix):
            return GROUP_OF[prefix]
    return ""


def build() -> dict:
    doc = pymupdf.open(TEMPLATE)
    fields: dict[str, dict] = {}
    order = []
    exclusive = {n: i for i, grp in enumerate(EXCLUSIVE) for n in grp}
    for pno, page in enumerate(doc, start=1):
        for w in page.widgets():
            name = w.field_name or ""
            kind = w.field_type_string
            if kind == "Button" or not name:
                continue
            if name in fields:                       # Name / DOB / Date repeat on every page
                fields[name]["pages"].append(pno)
                continue
            r = w.rect
            if kind == "CheckBox":
                ftype, default = "checkbox", w.field_value in ("Yes", "On", True)
            elif kind in ("ComboBox", "ListBox"):
                ftype, default = "choice", (w.field_value or "").strip()
            else:
                ftype, default = ("textarea" if r.height > 16 else "text"), (w.field_value or "")
            entry = {
                "type": ftype, "pages": [pno], "rect": [round(r.x0), round(r.y0), round(r.x1), round(r.y1)],
                "section": section_for(name), "label": label_for(name, ftype), "default": default,
            }
            if ftype == "checkbox":
                entry["group"] = group_for(name)
                if name in exclusive:
                    entry["exclusive"] = exclusive[name]
            if ftype == "choice":
                entry["options"] = [o for o in w.choice_values if o.strip()]
            fields[name] = entry
            order.append(name)
    fields["Name"]["default"] = ""                 # the blank form says TEST
    fields["HPI-age"]["default"] = ""
    for name in ("addnl-time", "addnl-time-2", "Imaging", "Imaging-2", "rinne-ba", "appt-time"):
        fields[name]["default"] = ""               # the form pre-selects a statement; a note starts with none
    sections = [{"id": sid, "title": title, "page": page,
                 "fields": [n for n in order if fields[n]["section"] == sid]} for sid, title, page in SECTIONS]
    rows = [{"normal": n, "abnormal": a, "findings": f, "group": g, "label": lab} for n, a, f, g, lab in EXAM_ROWS]
    return {"template": os.path.basename(TEMPLATE), "pages": len(doc), "page_size": [612, 792],
            "sections": sections, "exam_rows": rows, "fields": fields}


if __name__ == "__main__":
    schema = build()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=1)
    by_type = {}
    for v in schema["fields"].values():
        by_type[v["type"]] = by_type.get(v["type"], 0) + 1
    misc = [n for n, v in schema["fields"].items() if v["section"] == "misc"]
    print(f"{len(schema['fields'])} fields {by_type}; unsectioned: {misc}")
