"""
CranialVIII Web Interface & Conflict Resolution Server
Lightweight, blazing-fast local FastAPI server running strictly on 127.0.0.1.
Zero external CDN dependencies, fully air-gapped HIPAA design.
"""

from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from config.settings import settings
from core.db.connection import get_raw_connection
from services.fax_slayer.conflict_queue import fetch_pending_conflicts, resolve_conflict
from services.clinical_ai.offline_llm import offline_ai
from services.clinical_ai.vestibular.bppv import analyze_bppv
from services.clinical_ai.vestibular.hints import evaluate_hints_exam
from services.clinical_ai.vestibular.differential import differentiate_vestibular_disorder

app = FastAPI(title="CranialVIII Clinical Platform", version="0.1.0")

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    """Main operational dashboard: stats, deduplication savings, and system health."""
    conn = get_raw_connection()
    cur = conn.cursor()
    
    # Ingest Stats
    cur.execute("""
        SELECT 
            COUNT(*) as total_docs,
            COALESCE(SUM(file_size_bytes), 0) as total_bytes,
            COUNT(*) FILTER (WHERE is_duplicate = TRUE) as dup_count,
            COUNT(*) FILTER (WHERE confidence_tier = 'high') as high_conf,
            COUNT(*) FILTER (WHERE confidence_tier = 'conflict') as conflict_count,
            COUNT(*) FILTER (WHERE extracted_at IS NOT NULL) as loaded_count
        FROM documents;
    """)
    row = cur.fetchone()
    total_docs, total_bytes, dup_count, high_conf, conflict_count, loaded_count = row if row else (0,0,0,0,0,0)
    
    # Calculate clerical savings
    # Baseline: Average clerical staff downloads, renames, and files 1 doc in 2.5 minutes ($25/hr = $1.04 per file)
    dollars_saved = round(high_conf * 1.04, 2)
    hours_saved = round((high_conf * 2.5) / 60, 1)
    
    # Pending conflict review count
    cur.execute("SELECT COUNT(*) FROM conflict_queue WHERE status = 'pending';")
    pending_conflicts = cur.fetchone()[0]
    
    cur.close()
    conn.close()

    gb_scanned = round(total_bytes / (1024 ** 3), 2)
    load_pct = round((loaded_count / total_docs * 100), 1) if total_docs > 0 else 0

    return templates.TemplateResponse("index.html", {
        "request": request,
        "total_docs": total_docs,
        "gb_scanned": gb_scanned,
        "dup_count": dup_count,
        "high_conf": high_conf,
        "loaded_count": loaded_count,
        "load_pct": load_pct,
        "pending_conflicts": pending_conflicts,
        "dollars_saved": dollars_saved,
        "hours_saved": hours_saved,
        "ai_model": settings.OFFLINE_AI_MODEL
    })


@app.get("/worklist", response_class=HTMLResponse)
async def worklist_view(request: Request, role: Optional[str] = None):
    """Daily clinical worklist view: STAT, High, and Routine tasks."""
    from services.workflow.fixtures import load_synthetic_clinical_worklist
    wm = load_synthetic_clinical_worklist()
    items = wm.get_by_role(role)
    counts = wm.summary_counts()
    return templates.TemplateResponse("worklist.html", {
        "request": request,
        "items": items,
        "counts": counts,
        "current_role": role or "ALL"
    })


@app.get("/conflicts", response_class=HTMLResponse)
async def conflicts_view(request: Request):
    """Side-by-side human review queue for the 2.3% discrepancies."""
    conflicts = fetch_pending_conflicts(limit=50)
    return templates.TemplateResponse("conflicts.html", {
        "request": request,
        "conflicts": conflicts
    })


class ConflictResolvePayload(BaseModel):
    conflict_id: int
    resolution: str
    reviewed_by: str
    manual_last_name: Optional[str] = None
    manual_first_name: Optional[str] = None
    notes: Optional[str] = ""


@app.post("/api/conflicts/resolve")
async def resolve_conflict_api(payload: ConflictResolvePayload):
    success = resolve_conflict(
        conflict_id=payload.conflict_id,
        resolution=payload.resolution,
        reviewed_by=payload.reviewed_by,
        manual_last_name=payload.manual_last_name,
        manual_first_name=payload.manual_first_name,
        notes=payload.notes or ""
    )
    return {"success": success}


@app.get("/assistant", response_class=HTMLResponse)
async def clinical_assistant_view(request: Request):
    """Interactive offline clinical assistant for otoneurology & neurology."""
    return templates.TemplateResponse("assistant.html", {"request": request})


class ClinicalQueryPayload(BaseModel):
    prompt: str
    clinical_domain: str = "general"


@app.post("/api/assistant/ask")
async def ask_assistant_api(payload: ClinicalQueryPayload):
    messages = [
        {"role": "system", "content": "You are CranialVIII, an expert offline clinical otoneurologist and neurologist. Provide precise, evidence-based clinical reasoning."},
        {"role": "user", "content": payload.prompt}
    ]
    response = await offline_ai.chat_completion(messages=messages)
    return {"response": response}
