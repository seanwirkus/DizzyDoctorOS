"""
PaperEpley - Clinical Fax & Document Auto-Renamer
Repositioning disorganized faxes and scans into standard clinical taxonomy:
Format: [LastName]_[FirstName]_[DOB]_[DocType]_[DOS].pdf
"""

import os
import re
import shutil
from pathlib import Path
from datetime import date
from typing import Dict, Optional, Tuple


def clean_name_token(token: Optional[str]) -> str:
    """Sanitizes names for standard clinical file paths."""
    if not token:
        return "UNKNOWN"
    # Remove apostrophes, hyphens, non-alphanumerics
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", token.strip())
    return cleaned.capitalize() if cleaned else "UNKNOWN"


def generate_standard_filename(
    last_name: Optional[str],
    first_name: Optional[str],
    dob: Optional[date],
    doc_type: Optional[str],
    dos: Optional[date],
    original_extension: str = ".pdf"
) -> str:
    """
    Generates standardized clinical filename:
    Example: 'Smith_John_19800512_ProgressNote_20240315.pdf'
    """
    last = clean_name_token(last_name)
    first = clean_name_token(first_name)
    dob_str = dob.strftime("%Y%m%d") if isinstance(dob, (date,)) else "NODOB"
    dtype = clean_name_token(doc_type or "MedicalRecord")
    dos_str = dos.strftime("%Y%m%d") if isinstance(dos, (date,)) else "NODOS"
    
    ext = original_extension if original_extension.startswith(".") else f".{original_extension}"
    return f"{last}_{first}_{dob_str}_{dtype}_{dos_str}{ext}"


def auto_file_document(
    source_filepath: str,
    target_dir: str,
    last_name: Optional[str],
    first_name: Optional[str],
    dob: Optional[date],
    doc_type: Optional[str],
    dos: Optional[date]
) -> Tuple[bool, str, str]:
    """
    Safely renames and moves document to destination directory.
    Partitions by Patient Surname Initial (e.g. /S/Smith/) to prevent 100k file directory slowdowns.
    """
    src = Path(source_filepath)
    if not src.exists():
        return False, "", "Source file does not exist"

    new_filename = generate_standard_filename(
        last_name, first_name, dob, doc_type, dos, src.suffix
    )
    
    # Subpartition by first letter of surname
    surname_letter = clean_name_token(last_name)[0].upper()
    dest_folder = Path(target_dir) / surname_letter / clean_name_token(last_name)
    dest_folder.mkdir(parents=True, exist_ok=True)
    
    dest_path = dest_folder / new_filename
    
    # Handle rare collision
    counter = 1
    while dest_path.exists():
        stem = dest_path.stem
        ext = dest_path.suffix
        dest_path = dest_folder / f"{stem}_v{counter}{ext}"
        counter += 1

    try:
        shutil.move(str(src), str(dest_path))
        return True, str(dest_path), ""
    except Exception as e:
        return False, "", str(e)
