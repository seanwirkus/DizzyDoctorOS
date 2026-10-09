"""
CranialVIII High-Precision Clinical Extractor
Extracts structured clinical fields, templates, demographics, and symptoms from PDFs.
Features strict NUL-byte stripping at every exit point to prevent PostgreSQL ingestion crashes.
"""

import re
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


def sanitize_text(text: Optional[str]) -> str:
    """
    CRITICAL FIX: Strips NUL bytes (\x00) and cleans broken surrogate scanner artifacts.
    PostgreSQL text/varchar columns reject strings containing NUL bytes outright.
    """
    if not text:
        return ""
    # Strip null bytes and non-printable control characters (except newline/tab)
    cleaned = text.replace("\x00", "")
    cleaned = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned.strip()


# Regex patterns for clinical documents
DATE_PATTERNS = [
    re.compile(r"\b(0?[1-9]|1[0-2])[\/\-\.](0?[1-9]|[12]\d|3[01])[\/\-\.](19\d\d|20\d\d)\b"),
    re.compile(r"\b(19\d\d|20\d\d)[\/\-\.](0?[1-9]|1[0-2])[\/\-\.](0?[1-9]|[12]\d|3[01])\b"),
]

NAME_HEADER_PATTERNS = [
    re.compile(r"(?:Patient(?:\s+Name)?|Name|Pt):\s*([A-Z][a-zA-Z'\-]+),\s*([A-Z][a-zA-Z'\-]+)", re.IGNORECASE),
    re.compile(r"(?:Patient(?:\s+Name)?|Name|Pt):\s*([A-Z][a-zA-Z'\-]+)\s+([A-Z][a-zA-Z'\-]+)", re.IGNORECASE),
]

DOB_PATTERNS = [
    re.compile(r"(?:DOB|Date of Birth|Birthdate):\s*([0-9\/\-\.]{8,10})", re.IGNORECASE),
]

DOS_PATTERNS = [
    re.compile(r"(?:DOS|Date of Service|Visit Date|Exam Date):\s*([0-9\/\-\.]{8,10})", re.IGNORECASE),
]

# Clinical symptoms and physical exam tests specific to otoneurology & neurology
SYMPTOM_TRIGGERS = {
    "disequilibrium": re.compile(r"\b(disequilibrium|imbalance|unsteadiness)\b", re.IGNORECASE),
    "vertigo": re.compile(r"\b(vertigo|true vertigo|rotational vertigo)\b", re.IGNORECASE),
    "attacks": re.compile(r"\b(attacks|drop attacks|tumarkin|spells)\b", re.IGNORECASE),
    "aural_fullness": re.compile(r"\b(aural fullness|ear fullness|pressure in ear)\b", re.IGNORECASE),
    "tinnitus": re.compile(r"\b(tinnitus|ringing in ear)\b", re.IGNORECASE),
    "hearing_loss": re.compile(r"\b(hearing loss|sensorineural|conductive)\b", re.IGNORECASE),
    "oscillopsia": re.compile(r"\b(oscillopsia|visual bobbing)\b", re.IGNORECASE),
}

EXAM_TRIGGERS = {
    "weber_midline": re.compile(r"\bWeber:\s*midline\b", re.IGNORECASE),
    "weber_ad": re.compile(r"\bWeber:\s*(?:AD|right)\b", re.IGNORECASE),
    "weber_as": re.compile(r"\bWeber:\s*(?:AS|left)\b", re.IGNORECASE),
    "dix_hallpike_pos": re.compile(r"\bDix[\-\s]Hallpike:\s*(?:positive|abnormal|\+)\b", re.IGNORECASE),
    "dix_hallpike_neg": re.compile(r"\bDix[\-\s]Hallpike:\s*(?:negative|normal|\-)\b", re.IGNORECASE),
    "nystagmus": re.compile(r"\b(nystagmus|geotropic|apogeotropic|torsional|horizontal)\b", re.IGNORECASE),
    "head_impulse": re.compile(r"\b(head impulse|vHIT|catch[\-\s]up saccade)\b", re.IGNORECASE),
}


def parse_clinical_date(date_str: str) -> Optional[date]:
    """
    Parses date and enforces clinical validity:
    Date cannot be in the future (rejects mistyped 2028-2035 years).
    """
    if not date_str:
        return None
    
    clean_str = sanitize_text(date_str)
    formats = ["%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m.%d.%Y", "%m/%d/%y"]
    
    today = date.today()
    for fmt in formats:
        try:
            parsed = datetime.strptime(clean_str, fmt).date()
            # If 2-digit year was parsed into future, adjust
            if parsed.year > today.year + 1:
                continue
            # Date of service cannot be in the future
            if parsed > today:
                continue
            # Plausibility floor (clinic records not older than 1920)
            if parsed.year < 1920:
                continue
            return parsed
        except ValueError:
            continue
    return None


def extract_document(pdf_path: str) -> Dict:
    """
    Extracts clinical text layer and fields from PDF.
    Returns sanitized dictionary ready for database insertion.
    """
    path_obj = Path(pdf_path)
    if not path_obj.exists():
        return {}

    full_text = []
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            page_text = page.get_text()
            full_text.append(page_text)
        doc.close()
    except Exception as e:
        return {"error": str(e), "filepath": pdf_path}

    raw_text = "\n".join(full_text)
    clean_body = sanitize_text(raw_text)
    
    first_page_text = full_text[0] if full_text else ""
    first_page_clean = sanitize_text(first_page_text)
    
    # 1. Extract Patient Name
    last_name = None
    first_name = None
    hdr_last = None
    
    for pat in NAME_HEADER_PATTERNS:
        match = pat.search(first_page_clean)
        if match:
            # Check pattern format
            g1, g2 = match.group(1).strip(), match.group(2).strip()
            if "," in match.group(0):
                last_name, first_name = g1, g2
            else:
                first_name, last_name = g1, g2
            hdr_last = last_name
            break

    # 2. Extract DOB
    patient_dob = None
    dob_match = DOB_PATTERNS[0].search(first_page_clean)
    if dob_match:
        patient_dob = parse_clinical_date(dob_match.group(1))

    # 3. Extract Date of Service
    dos = None
    raw_dos = None
    dos_match = DOS_PATTERNS[0].search(first_page_clean)
    if dos_match:
        raw_dos = sanitize_text(dos_match.group(1))
        dos = parse_clinical_date(raw_dos)
        
    # 4. Extract Structured Clinical Findings
    structured_fields = []
    
    # Check symptoms
    for sym_key, regex in SYMPTOM_TRIGGERS.items():
        if regex.search(clean_body):
            structured_fields.append({
                "key": f"assoc-{sym_key}",
                "raw_value": sym_key,
                "parsed_value": "present",
                "category": "symptom"
            })

    # Check exams
    for exam_key, regex in EXAM_TRIGGERS.items():
        m = regex.search(clean_body)
        if m:
            structured_fields.append({
                "key": f"exam-{exam_key}",
                "raw_value": sanitize_text(m.group(0)),
                "parsed_value": exam_key,
                "category": "exam"
            })

    return {
        "filepath": str(path_obj),
        "filename": sanitize_text(path_obj.name),
        "clean_body": clean_body,
        "hdr_last": sanitize_text(hdr_last),
        "patient_last_name": sanitize_text(last_name),
        "patient_first_name": sanitize_text(first_name),
        "patient_dob": patient_dob,
        "date_of_service": dos,
        "raw_date_of_service": sanitize_text(raw_dos),
        "structured_fields": structured_fields
    }
