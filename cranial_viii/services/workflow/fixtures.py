"""
Synthetic Clinical Fixtures for Offline Testing & Verification
100% synthetic, zero-PHI test fixtures mirroring real otoneurology & neurology clinic workflows.
"""

from datetime import datetime, date, timedelta
from services.workflow.worklist import WorklistItem, WorklistManager
from services.workflow.sla_rules import check_unsigned_note_status, check_inbound_fax_sla, check_pending_diagnostics_sla


def load_synthetic_clinical_worklist() -> WorklistManager:
    """Populates a realistic daily clinic worklist using zero-PHI synthetic fixtures."""
    wm = WorklistManager()
    today = date.today()
    now = datetime.now()

    # 1. STAT Emergency: Inbound referral with HINTS stroke danger sign
    wm.add_item(WorklistItem(
        id="WL-101",
        priority="STAT",
        role="PHYSICIAN",
        category="CLINICAL_TRIAGE",
        title="STAT: Acute Vestibular Syndrome with Central Flags",
        description="Inbound ER consult notes bidirectional gaze-evoked nystagmus with intact head impulse. Rule out PICA cerebellar stroke.",
        patient_identifier="SYNTH-PT-8821 (Case #101)",
        created_at=now.strftime("%Y-%m-%d %H:%M"),
        due_in="Immediate (1 hour)"
    ))

    # 2. HIGH: Unsigned Progress Note from 3 days ago (> 72h billing block)
    three_days_ago = today - timedelta(days=3)
    note_status = check_unsigned_note_status(three_days_ago, is_signed=False, now=now)
    wm.add_item(WorklistItem(
        id="WL-102",
        priority="HIGH",
        role="PHYSICIAN",
        category="UNSIGNED_NOTE",
        title="Overdue Progress Note Signature (DOS: " + three_days_ago.strftime("%m/%d/%Y") + ")",
        description=note_status["message"],
        patient_identifier="SYNTH-PT-4029 (Case #102)",
        created_at=(now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M"),
        due_in="Overdue by 24h"
    ))

    # 3. HIGH: Overdue Inbound Referral Fax (> 24h intake SLA)
    yesterday_fax = now - timedelta(hours=28)
    fax_status = check_inbound_fax_sla(yesterday_fax, is_triaged=False, now=now)
    wm.add_item(WorklistItem(
        id="WL-103",
        priority="HIGH",
        role="MA_CLERK",
        category="OVERDUE_FAX",
        title="Inbound Referral Untriaged (> 24h SLA)",
        description=fax_status["message"],
        patient_identifier="SYNTH-PT-1105 (Case #103)",
        created_at=yesterday_fax.strftime("%Y-%m-%d %H:%M"),
        due_in="Overdue by 4h"
    ))

    # 4. ROUTINE: Delayed VNG / Vestibular Diagnostic Report (10 days since order)
    ten_days_ago = today - timedelta(days=10)
    diag_status = check_pending_diagnostics_sla(ten_days_ago, has_results=False, test_type="VNG Test", now=now)
    wm.add_item(WorklistItem(
        id="WL-104",
        priority="ROUTINE",
        role="AUDIOLOGIST",
        category="DELAYED_VNG",
        title="Awaiting VNG Caloric & Oculomotor Traces",
        description=diag_status["message"],
        patient_identifier="SYNTH-PT-9402 (Case #104)",
        created_at=(now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
        due_in="2 days ago"
    ))

    # 5. ROUTINE: PaperEpley Conflict Queue (Smith vs Smyth, near-miss distance 1)
    wm.add_item(WorklistItem(
        id="WL-105",
        priority="ROUTINE",
        role="MA_CLERK",
        category="CONFLICT_QUEUE",
        title="Discrepancy Review: Filename 'Smith' vs Form 'Smyth'",
        description="Levenshtein distance = 1. Kept in review to prevent incorrect chart merges.",
        patient_identifier="SYNTH-PT-3301 (Case #105)",
        created_at=(now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"),
        due_in="End of day"
    ))

    return wm
