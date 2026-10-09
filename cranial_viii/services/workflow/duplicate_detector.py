"""
Duplicate Referral & Fax Detection Engine
Solves the clinical problem of referring clinics repeatedly sending duplicate faxes.
Saves clerical staff from re-reading and re-triaging duplicate 20-page packets.
"""

from typing import Dict, List, Optional, Tuple
from datetime import date
from core.db.connection import get_raw_connection


def check_for_duplicate_referral(
    patient_last: Optional[str],
    patient_dob: Optional[date],
    date_of_service: Optional[date],
    doc_type: Optional[str],
    file_hash: str
) -> Tuple[bool, str, Optional[int]]:
    """
    Two-stage duplicate detection:
    1. Exact SHA-256 hash match (byte-for-byte identical file re-sent).
    2. Semantic Clinical Tuple match: Same Patient Surname + DOB + Date of Service.
    """
    conn = get_raw_connection()
    if conn is None:
        return False, "Database not connected", None

    cur = conn.cursor()

    # Stage 1: Exact byte hash
    cur.execute("""
        SELECT id, filename, filepath 
        FROM documents 
        WHERE file_hash_sha256 = %s 
        LIMIT 1;
    """, (file_hash,))
    exact_match = cur.fetchone()
    if exact_match:
        cur.close()
        conn.close()
        return True, f"Exact duplicate of previously ingested document #{exact_match[0]} ({exact_match[1]}).", exact_match[0]

    # Stage 2: Clinical Tuple match
    if patient_last and date_of_service:
        cur.execute("""
            SELECT id, filename, date_of_service 
            FROM documents 
            WHERE LOWER(patient_last_name) = LOWER(%s)
              AND date_of_service = %s
              AND is_duplicate = FALSE
            LIMIT 1;
        """, (patient_last, date_of_service))
        semantic_match = cur.fetchone()
        if semantic_match:
            cur.close()
            conn.close()
            return True, f"Suspected duplicate encounter: Match found for {patient_last} with DOS {date_of_service} (Doc #{semantic_match[0]}).", semantic_match[0]

    cur.close()
    conn.close()
    return False, "No duplicates detected.", None
