"""
CranialVIII In-Database Date Repair Engine
Replays updated clinical date parsing rules across millions of database rows
without requiring any disk re-reads of PDFs.
Eliminates future date anomalies (e.g. mistyped 2028-2035 years).
"""

from datetime import date
from core.db.connection import get_raw_connection
from pipeline.extract import parse_clinical_date


def repair_dates():
    """
    Scans documents with raw_date_of_service or unparsed/out-of-bounds dates,
    re-applies strict clinical date rules, and updates documents in place.
    """
    today = date.today()
    conn = get_raw_connection()
    conn.autocommit = False
    cur = conn.cursor()
    
    print(f"Running --repair-dates with ceiling: {today}...")
    
    # 1. Direct SQL normalization of any impossible future dates
    cur.execute("""
        SELECT COUNT(*) 
        FROM documents 
        WHERE date_of_service > CURRENT_DATE;
    """)
    future_count = cur.fetchone()[0]
    print(f"Found {future_count:,} documents with dates in the future.")
    
    # 2. Reset invalid future dates to NULL or try to repair if 2-digit year was parsed wrong
    cur.execute("""
        UPDATE documents 
        SET date_of_service = NULL 
        WHERE date_of_service > CURRENT_DATE;
    """)
    repaired_future = cur.rowcount
    
    # 3. Pull documents that have raw_date_of_service recorded but NULL or altered parsed dates
    cur.execute("""
        SELECT id, raw_date_of_service 
        FROM documents 
        WHERE raw_date_of_service IS NOT NULL 
          AND (date_of_service IS NULL OR date_of_service > CURRENT_DATE)
        LIMIT 100000;
    """)
    rows = cur.fetchall()
    
    repaired_from_raw = 0
    updates = []
    
    for doc_id, raw_dos in rows:
        fixed_date = parse_clinical_date(raw_dos)
        if fixed_date:
            updates.append((fixed_date, doc_id))
            repaired_from_raw += 1
            
    if updates:
        cur.executemany("""
            UPDATE documents 
            SET date_of_service = %s 
            WHERE id = %s;
        """, updates)
        
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"Date Repair Complete: {repaired_future:,} future anomalies cleared, {repaired_from_raw:,} re-parsed from raw.")
    return {"future_cleared": repaired_future, "reparsed_from_raw": repaired_from_raw}
