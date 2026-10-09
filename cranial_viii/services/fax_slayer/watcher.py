"""
PaperEpley Fax Slayer Background Watcher Daemon
Continuously monitors inbound fax folders and Graph API mailboxes.
Extracts clinical data, computes Levenshtein distance, auto-files 93.5% high-confidence docs,
and routes conflicts to the review queue.
Eliminates human clerical overhead ($70k-$100k/yr in labor).
"""

import time
import asyncio
from pathlib import Path
from typing import Optional
from config.settings import settings
from pipeline.extract import extract_document
from pipeline.load import score_confidence
from services.fax_slayer.renamer import auto_file_document
from services.fax_slayer.graph_client import GraphClient
from core.db.connection import get_raw_connection


def process_single_fax(filepath: Path) -> dict:
    """Processes a newly arrived PDF fax through extraction, scoring, and auto-renaming."""
    print(f"PaperEpley processing: {filepath.name}")
    data = extract_document(str(filepath))
    
    if "error" in data:
        print(f"Extraction error on {filepath.name}: {data['error']}")
        return {"status": "error", "error": data["error"]}
        
    hdr_last = data.get("hdr_last")
    tier, score, dist = score_confidence(filepath.name, hdr_last)
    
    conn = get_raw_connection()
    conn.autocommit = False
    cur = conn.cursor()
    
    # Check if doc already in database
    cur.execute("SELECT id FROM documents WHERE filepath = %s;", (str(filepath),))
    existing = cur.fetchone()
    
    if existing:
        doc_id = existing[0]
    else:
        cur.execute("""
            INSERT INTO documents (
                filepath, filename, extension, file_size_bytes,
                file_hash_sha256, confidence_tier, confidence_score,
                levenshtein_distance, hdr_last, patient_last_name,
                patient_first_name, patient_dob, date_of_service,
                last_modified_at, extracted_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW()
            ) RETURNING id;
        """, (
            str(filepath), filepath.name, filepath.suffix, filepath.stat().st_size,
            "stream_ingest", tier, score, dist, hdr_last,
            data.get("patient_last_name"), data.get("patient_first_name"),
            data.get("patient_dob"), data.get("date_of_service")
        ))
        doc_id = cur.fetchone()[0]

    # Insert structured clinical fields
    for f in data.get("structured_fields", []):
        cur.execute("""
            INSERT INTO doc_fields (
                document_id, field_key, field_raw_value, field_parsed_value, field_category
            ) VALUES (%s, %s, %s, %s, %s);
        """, (doc_id, f["key"], f["raw_value"][:256], f["parsed_value"][:256], f["category"]))

    # Route document
    action_taken = "pending"
    if tier == "high":
        # 93.5% confident: Auto-rename and file into patient archive
        success, new_path, err = auto_file_document(
            str(filepath),
            settings.PAPER_EPLEY_PROCESSED_DIR,
            data.get("patient_last_name") or hdr_last,
            data.get("patient_first_name"),
            data.get("patient_dob"),
            "ProgressNote",
            data.get("date_of_service")
        )
        if success:
            action_taken = "auto_filed"
            cur.execute("UPDATE documents SET filepath = %s, filename = %s WHERE id = %s;",
                        (new_path, Path(new_path).name, doc_id))
    else:
        # Conflict / Near-miss: Queue for rapid human verification
        fn_surname = filepath.name.replace("-", "_").split("_")[0]
        cur.execute("""
            INSERT INTO conflict_queue (
                document_id, filename_extracted_name, form_extracted_name, levenshtein_dist
            ) VALUES (%s, %s, %s, %s);
        """, (doc_id, fn_surname, hdr_last, dist))
        action_taken = "queued_for_review"

    conn.commit()
    cur.close()
    conn.close()
    
    return {
        "doc_id": doc_id,
        "tier": tier,
        "score": score,
        "dist": dist,
        "action": action_taken
    }


async def run_watcher_daemon():
    """Main event loop for PaperEpley Fax Slayer daemon."""
    inbound_dir = Path(settings.PAPER_EPLEY_WATCH_DIR)
    inbound_dir.mkdir(parents=True, exist_ok=True)
    
    graph = GraphClient()
    print(f"PaperEpley Fax Slayer active. Monitoring: {inbound_dir}")
    
    while True:
        try:
            # 1. Fetch any new e-faxes from Microsoft Graph
            if settings.GRAPH_TENANT_ID:
                new_faxes = await graph.fetch_inbound_faxes(str(inbound_dir))
                if new_faxes > 0:
                    print(f"Downloaded {new_faxes} incoming faxes from Microsoft Graph.")

            # 2. Check local watch folder
            for item in inbound_dir.glob("*.pdf"):
                if item.is_file():
                    res = process_single_fax(item)
                    print(f"Result for {item.name}: {res}")
                    
        except Exception as e:
            print(f"Watcher loop exception: {e}")
            
        await asyncio.sleep(settings.PAPER_EPLEY_POLL_INTERVAL_SEC)
