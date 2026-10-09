"""
CranialVIII Unified CLI Command Suite
The Sovereign Clinical AI, PaperEpley Fax Slayer, and Clinical Workflow Engine.
Built with zero required external dependencies (argparse + optional rich/typer).
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import date, timedelta

# Ensure package root in sys.path
pkg_root = Path(__file__).parent
if str(pkg_root) not in sys.path:
    sys.path.insert(0, str(pkg_root))


def cmd_migrate(args):
    """Applies all PostgreSQL schema migrations in sequence (001-005)."""
    from core.db.connection import run_migrations
    print("=== Applying CranialVIII Database Migrations ===")
    run_migrations()
    print("✓ All migrations successfully applied.")


def cmd_crawl(args):
    """Executes high-performance crawler, tracks duplicates, and stages documents."""
    from pipeline.crawl import crawl_directory
    print(f"=== Starting CranialVIII Corpus Crawl (limit: {args.limit}) ===")
    res = crawl_directory(source_root=args.path, limit=args.limit)
    print(f"✓ Crawl Complete: {res}")


def cmd_load(args):
    """Runs resumable document extractor, clinical scoring, and field indexing."""
    from pipeline.load import run_loader
    print(f"=== Running Resumable Document Loader (batch size: {args.batch_size}) ===")
    run_loader(batch_size=args.batch_size, limit=args.limit)


def cmd_repair_dates(args):
    """Replays date ceiling rules in-database across entire corpus without PDF re-reads."""
    from pipeline.repair_dates import repair_dates as run_repair
    print("=== Replaying Clinical Date Ceiling Rules in PostgreSQL ===")
    res = run_repair()
    print(f"✓ Date Repair Complete: {res}")


def cmd_fax_slayer(args):
    """Runs PaperEpley Fax Slayer background daemon (auto-download, auto-rename, conflict queue)."""
    import asyncio
    from services.fax_slayer.watcher import run_watcher_daemon
    print("=== Starting PaperEpley Fax Slayer Daemon ===")
    print("Saving $70k-$100k/year by automating document intake, OCR, and renaming...")
    asyncio.run(run_watcher_daemon())


def cmd_worklist(args):
    """Displays prioritized daily clinical worklist (STAT, High, Routine)."""
    from services.workflow.fixtures import load_synthetic_clinical_worklist
    wm = load_synthetic_clinical_worklist()
    items = wm.get_by_role(args.role)
    counts = wm.summary_counts()

    print("\n" + "="*80)
    print(f"  CRANIALVIII CLINICAL WORKLIST  —  [{counts['stat']} STAT | {counts['high']} HIGH | {counts['routine']} ROUTINE]")
    print("="*80)
    
    for it in items:
        badge = f"[{it['priority']}]"
        print(f"\n{badge:<9} {it['id']} | {it['role']:<12} | Category: {it['category']}")
        print(f"         Title: {it['title']}")
        print(f"         Patient: {it['patient_identifier']}  |  Due: {it['due_in']}")
        print(f"         Details: {it['description']}")
    print("\n" + "="*80 + "\n")


def cmd_check_overdue(args):
    """Scans for overdue unsigned notes and delayed diagnostic reports."""
    from services.workflow.sla_rules import check_unsigned_note_status, check_pending_diagnostics_sla
    print("\n=== Compliance & SLA Audit ===")
    note_status = check_unsigned_note_status(date.today() - timedelta(days=3), is_signed=False)
    print(f"• Unsigned Note Alert: {note_status['message']}")
    vng_status = check_pending_diagnostics_sla(date.today() - timedelta(days=10), has_results=False, test_type="VNG")
    print(f"• Diagnostic Test SLA: {vng_status['message']}\n")


def cmd_test_bppv(args):
    """Demonstrates clinical otoneurology BPPV canal localization & maneuver selection."""
    from services.clinical_ai.vestibular.bppv import analyze_bppv
    print("=== Testing CranialVIII BPPV Diagnostic & Treatment Engine ===")
    res = analyze_bppv(
        dix_hallpike_right="positive",
        nystagmus_latency_sec=3.0,
        nystagmus_duration_sec=20.0,
        nystagmus_type="upbeating_torsional"
    )
    print("Affected Canal:", res["affected_canal"])
    print("Affected Ear:  ", res["affected_ear"])
    print("Mechanism:     ", res["mechanism"])
    print("Maneuvers:     ", res["recommended_maneuvers"])
    print("Rationale:     ", res["clinical_rationale"])


def cmd_test_hints(args):
    """Demonstrates emergency HINTS exam triage (central stroke vs vestibular neuritis)."""
    from services.clinical_ai.vestibular.hints import evaluate_hints_exam
    print("=== Testing CranialVIII HINTS Triage Engine ===")
    res = evaluate_hints_exam(
        head_impulse="normal",
        nystagmus_pattern="direction_changing_bidirectional",
        test_of_skew="skew_deviation_vertical_refixation"
    )
    print("Classification: ", res["triage_classification"])
    print("Is Central Danger:", res["is_central_danger"])
    print("Central Flags:  ", res["central_flags"])
    print("Action Plan:    ", res["action_plan"])


def cmd_serve(args):
    """Starts local web server on 127.0.0.1."""
    import uvicorn
    print(f"Launching CranialVIII Local Interface on http://{args.host}:{args.port}")
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=False)


def main():
    parser = argparse.ArgumentParser(
        description="CranialVIII: Sovereign In-House Clinical AI & PaperEpley Automation Engine."
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # migrate
    subparsers.add_parser("migrate", help="Apply PostgreSQL database migrations")

    # crawl
    p_crawl = subparsers.add_parser("crawl", help="Crawl directory and track deduplication")
    p_crawl.add_argument("--path", type=str, default=None, help="Root folder to crawl")
    p_crawl.add_argument("--limit", type=int, default=0, help="Max files to scan")

    # load
    p_load = subparsers.add_parser("load", help="Run batch loader into Postgres")
    p_load.add_argument("--batch-size", type=int, default=500, help="Batch size")
    p_load.add_argument("--limit", type=int, default=0, help="Max documents")

    # repair-dates
    subparsers.add_parser("repair-dates", help="Replay date ceiling rules in-database")

    # fax-slayer
    subparsers.add_parser("fax-slayer", help="Run PaperEpley fax auto-renaming daemon")

    # worklist
    p_wl = subparsers.add_parser("worklist", help="Display prioritized daily clinical worklist")
    p_wl.add_argument("--role", type=str, default=None, choices=["PHYSICIAN", "AUDIOLOGIST", "MA_CLERK", "BILLING"])

    # check-overdue
    subparsers.add_parser("check-overdue", help="Check unsigned notes and delayed diagnostic SLAs")

    # test-bppv
    subparsers.add_parser("test-bppv", help="Run BPPV diagnostic test")

    # test-hints
    subparsers.add_parser("test-hints", help="Run HINTS stroke triage test")

    # serve
    p_serve = subparsers.add_parser("serve", help="Launch local web UI")
    p_serve.add_argument("--host", type=str, default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8008)

    args = parser.parse_args()

    commands = {
        "migrate": cmd_migrate,
        "crawl": cmd_crawl,
        "load": cmd_load,
        "repair-dates": cmd_repair_dates,
        "fax-slayer": cmd_fax_slayer,
        "worklist": cmd_worklist,
        "check-overdue": cmd_check_overdue,
        "test-bppv": cmd_test_bppv,
        "test-hints": cmd_test_hints,
        "serve": cmd_serve,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
