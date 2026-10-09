"""
Clinical Workflow Engine: Stage Transitions & State Machine
Defines valid life-cycle states for clinical documents and patient encounters.
Guards against premature filing or compliance breaches.
"""

from enum import Enum
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class DocumentStage(str, Enum):
    INGESTED = "ingested"              # Downloaded from fax/network, raw file staged
    OCR_PROCESSED = "ocr_processed"    # Text layer extracted
    EXTRACTED = "extracted"            # Patient Demographics & Clinical Fields matched
    AUDIT_PENDING = "audit_pending"    # Conflict queue (e.g. Smith vs Smyth, needs human sign-off)
    EHR_STAGED = "ehr_staged"          # Validated and formatted for EHR ingestion
    FILED = "filed"                    # Renamed and stored in clinical taxonomy folder
    ARCHIVED = "archived"              # Long-term HIPAA archive


class EncounterStage(str, Enum):
    INTAKE_PENDING = "intake_pending"              # Inbound fax referral received
    TRIAGED = "triaged"                            # Clinical urgency scored (STAT vs Routine)
    SCHEDULED = "scheduled"                        # Patient appointment booked
    SEEN = "seen"                                  # Provider encounter complete
    PENDING_DIAGNOSTICS = "pending_diagnostics"    # Awaiting VNG, vHIT, Audiogram, or MRI
    PENDING_SIGNATURE = "pending_signature"        # Note written but unsigned by physician
    SIGNED_COMPLETE = "signed_complete"            # Legally signed & closed
    OVERDUE_ALERT = "overdue_alert"                # SLA violated (e.g. unsigned > 48h)


# Allowed transitions
DOC_TRANSITIONS: Dict[DocumentStage, List[DocumentStage]] = {
    DocumentStage.INGESTED: [DocumentStage.OCR_PROCESSED],
    DocumentStage.OCR_PROCESSED: [DocumentStage.EXTRACTED],
    DocumentStage.EXTRACTED: [DocumentStage.AUDIT_PENDING, DocumentStage.EHR_STAGED, DocumentStage.FILED],
    DocumentStage.AUDIT_PENDING: [DocumentStage.EHR_STAGED, DocumentStage.FILED],
    DocumentStage.EHR_STAGED: [DocumentStage.FILED],
    DocumentStage.FILED: [DocumentStage.ARCHIVED],
    DocumentStage.ARCHIVED: []
}


def transition_document(
    current_stage: DocumentStage,
    target_stage: DocumentStage,
    has_unresolved_conflict: bool = False
) -> Tuple[bool, str]:
    """
    Validates stage transition.
    Enforces compliance guard: Documents with unresolved conflicts cannot transition directly to FILED.
    """
    if target_stage not in DOC_TRANSITIONS.get(current_stage, []):
        return False, f"Illegal transition from {current_stage.value} to {target_stage.value}."

    if target_stage in (DocumentStage.FILED, DocumentStage.EHR_STAGED) and has_unresolved_conflict:
        return False, "Cannot file document while unresolved surname/identity conflict is pending in audit queue."

    return True, f"Successfully transitioned to {target_stage.value}."
