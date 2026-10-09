"""
CranialVIII Resumable High-Scale Document Loader
Processes crawled files, extracts clinical fields, classifies confidence tiers,
manages conflict queues, and persists to PostgreSQL.
"""

import time
from typing import List, Tuple
try:
    from rapidfuzz.distance import Levenshtein
except ImportError:
    class Levenshtein:
        @staticmethod
        def distance(s1: str, s2: str) -> int:
            if len(s1) < len(s2):
                return Levenshtein.distance(s2, s1)
            if len(s2) == 0:
                return len(s1)
            previous_row = list(range(len(s2) + 1))
            for i, c1 in enumerate(s1):
                current_row = [i + 1]
                for j, c2 in enumerate(s2):
                    insertions = previous_row[j + 1] + 1
                    deletions = current_row[j] + 1
                    substitutions = previous_row[j] + (c1 != c2)
                    current_row.append(min(insertions, deletions, substitutions))
                previous_row = current_row
            return previous_row[-1]
from core.db.connection import get_raw_connection
from pipeline.extract import extract_document, sanitize_text


def score_confidence(filename: str, hdr_last: str) -> Tuple[str, float, int]:
    """
    Measures Levenshtein distance between filename surname and extracted header surname:
    - distance 0: High confidence (93.5% baseline)
    - distance <= 2: Near-miss (e.g. Smyth vs Smith, human audit)
    - distance 3-4: Moderate discrepancy
    - distance >= 5: Definite conflict (diff patient, multi-doc fax)
    """
    if not hdr_last:
        return "low", 0.40, 99
    
    # Extract candidate surname from filename (e.g., Smith_John_1980_ProgressNote.pdf)
    fn_parts = filename.replace("-", "_").split("_")
    fn_surname = fn_parts[0].strip() if fn_parts else ""
    
    if not fn_surname:
        return "low", 0.50, 99
    
    dist = Levenshtein.distance(fn_surname.lower(), hdr_last.lower())
    
    if dist == 0:
        return "high", 0.98, 0
    elif dist <= 2:
        return "conflict", 0.85, dist  # Near-miss kept in review deliberately
    elif dist <= 4:
        return "conflict", 0.65, dist
    else:
        return "conflict", 0.30, dist


def run_loader(batch_size: int = 500, limit: int = 0):
    """
    Resumable bulk loader.
    Pulls documents with extracted_at IS NULL, runs extraction and scoring,
    and updates documents + doc_fields.
    """
    conn = get_raw_connection()
    conn.autocommit = False
    cur = conn.cursor()
    
    print("Querying pending documents...")
    limit_clause = f"LIMIT {limit}" if limit > 0 else ""
    cur.execute(f"""
        SELECT id, filepath, filename 
        FROM documents 
        WHERE extracted_at IS NULL AND is_duplicate = FALSE
        ORDER BY id ASC
        {limit_clause};
    """)
    rows = cur.fetchall()
    total = len(rows)
    print(f"Found {total:,} pending documents to process.")
    
    processed = 0
    start_time = time.time()
    
    doc_updates = []
    field_inserts = []
    conflict_inserts = []
    
    for doc_id, fpath, fname in rows:
        data = extract_document(fpath)
        if "error" in data:
            doc_updates.append((
                "low", 0.0, None, None, None, None, None, None, doc_id
            ))
            continue
            
        tier, score, dist = score_confidence(fname, data.get("hdr_last"))
        
        doc_updates.append((
            tier,
            score,
            dist,
            data.get("hdr_last"),
            data.get("patient_last_name"),
            data.get("patient_first_name"),
            data.get("patient_dob"),
            data.get("date_of_service"),
            doc_id
        ))
        
        # If conflict, queue for human review
        if tier == "conflict":
            fn_parts = fname.replace("-", "_").split("_")
            fn_surname = fn_parts[0].strip() if fn_parts else ""
            conflict_inserts.append((
                doc_id,
                fn_surname,
                data.get("hdr_last"),
                dist
            ))
            
        # Collect structured fields
        for f in data.get("structured_fields", []):
            field_inserts.append((
                doc_id,
                f["key"],
                f["raw_value"][:256] if f["raw_value"] else "",  # Safe bound for btree
                f["parsed_value"][:256] if f["parsed_value"] else "",
                f["category"]
            ))
            
        processed += 1
        
        if len(doc_updates) >= batch_size:
            # Execute batch update
            cur.executemany("""
                UPDATE documents SET
                    confidence_tier = %s,
                    confidence_score = %s,
                    levenshtein_distance = %s,
                    hdr_last = %s,
                    patient_last_name = %s,
                    patient_first_name = %s,
                    patient_dob = %s,
                    date_of_service = %s,
                    extracted_at = NOW()
                WHERE id = %s;
            """, doc_updates)
            
            if field_inserts:
                cur.executemany("""
                    INSERT INTO doc_fields (
                        document_id, field_key, field_raw_value, 
                        field_parsed_value, field_category
                    ) VALUES (%s, %s, %s, %s, %s);
                """, field_inserts)
                
            if conflict_inserts:
                cur.executemany("""
                    INSERT INTO conflict_queue (
                        document_id, filename_extracted_name, form_extracted_name, levenshtein_dist
                    ) VALUES (%s, %s, %s, %s);
                """, conflict_inserts)
                
            conn.commit()
            doc_updates.clear()
            field_inserts.clear()
            conflict_inserts.clear()
            
            rate = processed / (time.time() - start_time)
            print(f"Loaded {processed:,}/{total:,} docs ({rate:.1f} docs/sec)")
            
    # Final flush
    if doc_updates:
        cur.executemany("""
            UPDATE documents SET
                confidence_tier = %s,
                confidence_score = %s,
                levenshtein_distance = %s,
                hdr_last = %s,
                patient_last_name = %s,
                patient_first_name = %s,
                patient_dob = %s,
                date_of_service = %s,
                extracted_at = NOW()
            WHERE id = %s;
        """, doc_updates)
        if field_inserts:
            cur.executemany("""
                INSERT INTO doc_fields (
                    document_id, field_key, field_raw_value, 
                    field_parsed_value, field_category
                ) VALUES (%s, %s, %s, %s, %s);
            """, field_inserts)
        if conflict_inserts:
            cur.executemany("""
                INSERT INTO conflict_queue (
                    document_id, filename_extracted_name, form_extracted_name, levenshtein_dist
                ) VALUES (%s, %s, %s, %s);
            """, conflict_inserts)
        conn.commit()
        
    cur.close()
    conn.close()
    print(f"Load complete. Total processed: {processed:,}")
