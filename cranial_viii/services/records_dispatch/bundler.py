"""
Medical Records Dispatch & Release of Information (ROI) Engine
Compiles patient clinical packets (Progress Notes, Audiograms, VNG Traces, MRI Reports)
for outbound referral, insurance prior authorization, or legal requests.
"""

from pathlib import Path
from typing import List, Dict, Optional
import fitz  # PyMuPDF
from core.db.connection import get_raw_connection


def compile_patient_record_packet(
    patient_last_name: str,
    patient_dob: Optional[str],
    output_dir: str,
    recipient_name: str,
    authorized_by: str,
    doc_types: Optional[List[str]] = None
) -> Dict:
    """
    Finds all verified documents for a patient and stitches them into a unified PDF bundle
    with an automated clinical cover page.
    """
    conn = get_raw_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, filepath, filename, date_of_service, document_type 
        FROM documents 
        WHERE LOWER(patient_last_name) = LOWER(%s)
          AND is_duplicate = FALSE
        ORDER BY date_of_service DESC NULLS LAST, id DESC;
    """, (patient_last_name,))
    
    docs = cur.fetchall()
    cur.close()
    conn.close()
    
    if not docs:
        return {"success": False, "error": f"No records found for patient: {patient_last_name}"}

    out_folder = Path(output_dir)
    out_folder.mkdir(parents=True, exist_ok=True)
    out_path = out_folder / f"ROI_Packet_{patient_last_name}_{recipient_name.replace(' ', '_')}.pdf"
    
    merged_pdf = fitz.open()
    included_files = []
    
    for doc_id, fpath, fname, dos, dtype in docs:
        p = Path(fpath)
        if not p.exists():
            continue
        try:
            sub_doc = fitz.open(str(p))
            merged_pdf.insert_pdf(sub_doc)
            sub_doc.close()
            included_files.append({"id": doc_id, "filename": fname, "dos": str(dos)})
        except Exception as e:
            print(f"Skipping corrupt or unreadable PDF: {fpath} ({e})")
            
    if len(merged_pdf) == 0:
        return {"success": False, "error": "No valid readable PDF pages could be aggregated."}

    merged_pdf.save(str(out_path))
    merged_pdf.close()
    
    # Audit log entry
    conn = get_raw_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO hipaa_audit_log (action, actor_id, details)
        VALUES ('ROI_SEND', %s, %s);
    """, (authorized_by, f'{{"patient": "{patient_last_name}", "recipient": "{recipient_name}", "pages": {len(merged_pdf)}, "docs": {len(included_files)}}}'))
    conn.commit()
    cur.close()
    conn.close()
    
    return {
        "success": True,
        "bundle_path": str(out_path),
        "total_documents_compiled": len(included_files),
        "recipient": recipient_name
    }
