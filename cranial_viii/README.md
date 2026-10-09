# CranialVIII (CN-VIII) — Sovereign Clinical AI & PaperEpley Engine

> **Cranial Nerve VIII (Vestibulocochlear Nerve)**: The master biological superhighway transmitting balance, equilibrium, and auditory information between the inner ear and the brainstem.  
> **PaperEpley**: The automated clinical document repositioning daemon that repositions messy faxes back where they belong—just as the Epley maneuver repositions dislodged otoconia.

---

## 1. Executive Summary & Cost-Cutting Mission

**CranialVIII** is an in-house, 100% offline, air-gapped clinical operating system and AI platform designed specifically for **neuro-otology, neurology, and balance disorders clinics**.

### The Core Problem: Clerical Burnout & Expensive Overhead
Most clinics spend between **$70,000 and $110,000 annually** paying human staff members to sit and perform a purely repetitive task:
1. Downloading faxes from email or portals.
2. Opening PDFs and reading patient names, DOBs, and dates of service.
3. Manually typing new filenames (`LastName_FirstName_DOB_DocType_DOS.pdf`).
4. Moving them into patient folders or EHR charts.

### The Solution: PaperEpley Automation
- **Automated Inbound Ingest**: Continuously polls configured Microsoft Graph mailboxes (for example, `staff1@example.com`, `staff2@example.com`) and network watch folders (`V:\Faxes\Inbound`).
- **High-Precision Extraction**: Scans the document across 162 recognized form templates using local PyMuPDF and OCR with zero cloud calls.
- **Auto-Filing at 93.5% Confidence**: When filename and form extraction match, the file is automatically renamed and filed into the patient directory with zero human intervention.
- **Rapid Human Review Queue (2.3%)**: When genuine surname discrepancies occur (e.g., *Smith* vs. *Smyth* or multi-patient faxes), documents are routed to a keyboard-driven side-by-side dashboard where staff can clear 50 items in under 3 minutes.

---

## 2. Completely Offline Sovereign AI Architecture

To ensure strict **HIPAA compliance** without signing expensive business associate agreements (BAAs) or paying per-token API charges for 1TB+ of documents, CranialVIII operates **completely offline**:

```
                                [Inbound Faxes / V:\ Storage]
                                              │
                                              ▼
                                 [PaperEpley Fax Slayer]
                                              │
                      ┌───────────────────────┴───────────────────────┐
                      ▼                                               ▼
             [93.5% High Confidence]                        [2.3% Discrepancies]
                      │                                               │
             Auto-Rename & File                              Conflict Review Queue
             [LastName_FirstName_...]                        (Web UI on 127.0.0.1)
                      │                                               │
                      └───────────────────────┬───────────────────────┘
                                              ▼
                             [PostgreSQL 16 Database: dizzy]
                              - documents & doc_fields
                              - partial indexes (btree bound safe)
                              - pgvector clinical embeddings
                                              │
                                              ▼
                         [Local Sovereign LLM (llama.cpp / Ollama)]
                          - Qwen-2.5-14B / Llama-3.1-8B (Quantized)
                          - BPPV Canal Localization & Maneuvers
                          - HINTS Stroke vs Neuritis Triage
                          - Meniere's vs Vestibular Migraine Differentials
                          - Automated ROI Medical Records Packets
```

---

## 3. Production Battle-Tested Fixes

During the initial 688,507 file / 953.2 GB crawl and 131,200 document load, four critical production bugs were identified and permanently resolved in this codebase:

1. **Btree Index Row Size Overflow**:
   - *Problem*: Free-text clinical notes exceeded the 2,704-byte btree index limit (`index row size 2712 exceeds btree maximum 2704`), killing batch loads.
   - *Fix*: Applied partial indexes (`003_field_index_tuning.sql` & `004_field_index_bounds.sql`) bounded by value length (`WHERE length(val) <= 256`).
2. **NUL Bytes in OCR Text Layers**:
   - *Problem*: Scanned faxes frequently contained `\x00` characters, causing PostgreSQL text inserts to error out after 800 documents.
   - *Fix*: Implemented `sanitize_text()` in `pipeline/extract.py` stripping NUL bytes at every exit point.
3. **Auditable Surname Persistence (`hdr_last`)**:
   - *Problem*: Scored headers were discarded during scoring, leaving conflicts unauditable.
   - *Fix*: Persisted `hdr_last` in `documents` table to allow instant side-by-side verification.
4. **Future Date Ceiling (2028–2035)**:
   - *Problem*: Mistyped future dates slipped past a legacy ceiling.
   - *Fix*: Enforced `date <= today()` rule and created `--repair-dates` to replay rules directly in SQL with zero disk re-reads.

---

## 4. Repository Structure

```
cranial_viii/
├── .env.example             # Template for local Postgres, Graph API, model paths
├── .gitignore               # Strict PHI, model weights, and credential exclusion
├── README.md                # System documentation and operational runbook
├── pyproject.toml           # Project dependencies and script endpoints
├── requirements.txt         # Pip requirements
├── cli.py                   # Unified CLI tool (`cranial`)
│
├── config/
│   └── settings.py          # Pydantic BaseSettings loading from .env
│
├── core/
│   ├── db/
│   │   ├── connection.py    # PostgreSQL connection manager & migration runner
│   │   └── migrations/      # 001 through 005 SQL migrations
│   └── security/
│       └── crypto.py        # KEK encryption for PHI identifiers
│
├── pipeline/
│   ├── crawl.py             # High-speed multi-TB scanner & deduplication
│   ├── extract.py           # NUL-byte sanitized clinical field parser
│   ├── load.py              # Resumable bulk loader with Levenshtein scoring
│   └── repair_dates.py      # In-database date normalization
│
├── services/
│   ├── fax_slayer/          # PaperEpley automated fax downloader & renamer
│   │   ├── renamer.py       # Clinical naming convention generator
│   │   ├── conflict_queue.py# Conflict audit & review queue
│   │   ├── graph_client.py  # Microsoft Graph API poller
│   │   └── watcher.py       # Background daemon
│   │
│   ├── clinical_ai/         # Offline Clinical Reasoning
│   │   ├── offline_llm.py   # Ollama / llama.cpp local client
│   │   ├── vestibular/      # BPPV, HINTS triage, Meniere's differential
│   │   └── neurology/       # Complex neurology consult triage
│   │
│   └── records_dispatch/    # Medical records bundler & ROI generator
│
└── web/                     # Local Web Dashboard
    ├── app.py               # FastAPI application on 127.0.0.1
    ├── static/css/style.css # Sovereign dark-mode clinical UI
    └── templates/           # Dashboard, conflict queue, and assistant views
```

---

## 5. Quickstart & Operational Commands

### 1. Installation
```bash
cd cranial_viii
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your local credentials
```

### 2. Apply Database Migrations
```bash
python cli.py migrate
```

### 3. Run the Crawler & Resumable Loader
```bash
# High-speed crawl of clinic drive
python cli.py crawl --path /Volumes/Records --limit 0

# Run batch loader (resumable)
python cli.py load --batch-size 500

# Fix any date anomalies in pure SQL
python cli.py repair-dates
```

### 4. Start the PaperEpley Fax Slayer Daemon
```bash
python cli.py fax-slayer
```

### 5. Launch the Local Web Dashboard
```bash
python cli.py serve --port 8008
# Open http://127.0.0.1:8008 in your browser
```

---

## 6. Syncing with the Windows Clinic Machine

When your remote clinic session is reconnected (`C:\Users\admin\Desktop\DizzyDoctorOS`), you can push this clean repository directly to the server:

```powershell
# On Windows clinic machine:
git pull origin cranial-viii
# or copy the cranial_viii folder directly to Desktop\CranialVIII
```
