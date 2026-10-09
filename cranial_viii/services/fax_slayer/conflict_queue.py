"""
PaperEpley Conflict Queue Manager
Handles audit trail and fast resolution of the 2.3% filename/form discrepancies.
"""

from datetime import datetime
from typing import Dict, List, Optional
from core.db.connection import get_raw_connection
from services.fax_slayer.renamer import auto_file_document
from config.settings import settings


def fetch_pending_conflicts(limit: int = 50) -> List[Dict]:
    """Retrieves pending conflicts for human review queue in web UI."""
    conn = get_raw_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            cq.id as conflict_id,
            d.id as doc_id,
            d.filepath,
            d.filename,
            cq.filename_extracted_name,
            cq.form_extracted_name,
            cq.levenshtein_dist,
            d.patient_dob,
            d.date_of_service,
            cq.created_at
        FROM conflict_queue cq
        JOIN documents d ON cq.document_id = d.id
        WHERE cq.status = 'pending'
        ORDER BY cq.levenshtein_dist ASC, cq.created_at DESC
        LIMIT %s;
    """, (limit,))
    
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    results = [dict(zip(columns, row)) for row in rows]
    
    cur.close()
    conn.close()
    return results


def resolve_conflict(
    conflict_id: int,
    resolution: str,  # 'use_form', 'use_filename', 'manual'
    reviewed_by: str,
    manual_last_name: Optional[str] = None,
    manual_first_name: Optional[str] = None,
    notes: str = ""
) -> bool:
    """
    Applies resolution: updates document metadata, moves file if appropriate,
    and logs resolution for HIPAA audit.
    """
    conn = get_raw_connection()
    conn.autocommit = False
    cur = conn.cursor()
    
    cur.execute("""
        SELECT cq.document_id, d.filepath, d.filename, cq.filename_extracted_name, 
               cq.form_extracted_name, d.patient_dob, d.date_of_service
        FROM conflict_queue cq
        JOIN documents d ON cq.document_id = d.id
        WHERE cq.id = %s;
    """, (conflict_id,))
    
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return False
        
    doc_id, filepath, filename, fn_name, form_name, dob, dos = row
    
    chosen_last = None
    chosen_first = "Unknown"
    
    if resolution == "use_form":
        chosen_last = form_name
    elif resolution == "use_filename":
        chosen_last = fn_name
    elif resolution == "manual" and manual_last_name:
        chosen_last = manual_last_name
        chosen_first = manual_first_name or chosen_first
    else:
        chosen_last = form_name or fn_name
        
    # Update document with confirmed name
    cur.execute("""
        UPDATE documents SET
            patient_last_name = %s,
            patient_first_name = %s,
            confidence_tier = 'resolved',
            confidence_score = 1.00
        WHERE id = %s;
    """, (chosen_last, chosen_first, doc_id))
    
    # Mark conflict resolved
    cur.execute("""
        UPDATE conflict_queue SET
            status = %s,
            reviewed_by = %s,
            resolution_notes = %s,
            resolved_at = NOW()
        WHERE id = %s;
    """, (f"resolved_{resolution}", reviewed_by, notes, conflict_id))
    
    # Audit log entry
    cur.execute("""
        INSERT INTO hipaa_audit_log (
            action, document_id, actor_id, details
        ) VALUES (
            'RESOLVE_CONFLICT', %s, %s, %s
        );
    """, (doc_id, reviewed_by, f'{{"resolution": "{resolution}", "chosen_last": "{chosen_last}"}}'))
    
    conn.commit()
    cur.close()
    conn.close()
    return True
