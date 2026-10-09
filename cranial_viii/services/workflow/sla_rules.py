"""
Clinical SLA Rules & Overdue Detection Engine
Enforces regulatory compliance deadlines:
- Unsigned Clinical Progress Notes (> 48h / > 72h CMS compliance)
- Inbound Referral Triage SLA (> 24h)
- Diagnostic Test Results Turnaround (> 7 days for VNG / Audiograms)
- Medical Records Release of Information (ROI) Deadlines (> 15 days HIPAA limit)
"""

from datetime import datetime, timedelta, date
from typing import Dict, List, Optional


class SLARules:
    MAX_FAX_INTAKE_HOURS = 24
    MAX_UNSIGNED_NOTE_HOURS = 48
    CRITICAL_UNSIGNED_HOURS = 72
    MAX_PENDING_DIAGNOSTICS_DAYS = 7
    MAX_ROI_FULFILLMENT_DAYS = 15


def check_unsigned_note_status(
    date_of_service: date,
    is_signed: bool,
    now: Optional[datetime] = None
) -> Dict:
    """
    Checks if a provider's clinical progress note is overdue for signing.
    CMS and commercial billing guidelines mandate signature within 48h.
    """
    if is_signed:
        return {"status": "compliant", "severity": "none", "hours_elapsed": 0}

    ref_now = now or datetime.now()
    dos_dt = datetime.combine(date_of_service, datetime.min.time())
    elapsed = ref_now - dos_dt
    hours = elapsed.total_seconds() / 3600.0

    if hours >= SLARules.CRITICAL_UNSIGNED_HOURS:
        return {
            "status": "critical_overdue",
            "severity": "high",
            "hours_elapsed": round(hours, 1),
            "message": f"CRITICAL: Note is unsigned for {round(hours, 1)}h (exceeds {SLARules.CRITICAL_UNSIGNED_HOURS}h ceiling). Billing hold."
        }
    elif hours >= SLARules.MAX_UNSIGNED_NOTE_HOURS:
        return {
            "status": "overdue",
            "severity": "medium",
            "hours_elapsed": round(hours, 1),
            "message": f"WARNING: Note is unsigned for {round(hours, 1)}h (exceeds {SLARules.MAX_UNSIGNED_NOTE_HOURS}h SLA)."
        }
    else:
        return {
            "status": "pending_signature",
            "severity": "low",
            "hours_elapsed": round(hours, 1),
            "message": f"Within 48h signature window ({round(hours, 1)}h elapsed)."
        }


def check_inbound_fax_sla(
    received_at: datetime,
    is_triaged: bool,
    now: Optional[datetime] = None
) -> Dict:
    """Checks if inbound referral fax has been triaged within 24h SLA."""
    if is_triaged:
        return {"status": "compliant", "severity": "none"}

    ref_now = now or datetime.now()
    hours = (ref_now - received_at).total_seconds() / 3600.0

    if hours >= SLARules.MAX_FAX_INTAKE_HOURS:
        return {
            "status": "overdue",
            "severity": "high",
            "hours_elapsed": round(hours, 1),
            "message": f"Overdue: Inbound referral untriaged for {round(hours, 1)}h (SLA is 24h)."
        }
    return {
        "status": "within_sla",
        "severity": "none",
        "hours_elapsed": round(hours, 1),
        "message": f"Intake in progress ({round(hours, 1)}h elapsed)."
    }


def check_pending_diagnostics_sla(
    ordered_date: date,
    has_results: bool,
    test_type: str,  # 'VNG', 'vHIT', 'Audiogram', 'Rotary Chair', 'MRI'
    now: Optional[datetime] = None
) -> Dict:
    """Checks if ordered vestibular or audiological tests are delayed."""
    if has_results:
        return {"status": "completed", "severity": "none"}

    ref_now = now or datetime.now()
    ordered_dt = datetime.combine(ordered_date, datetime.min.time())
    days = (ref_now - ordered_dt).days

    if days >= SLARules.MAX_PENDING_DIAGNOSTICS_DAYS:
        return {
            "status": "overdue",
            "severity": "medium",
            "days_elapsed": days,
            "message": f"Delayed Diagnostic: {test_type} ordered {days} days ago without uploaded report."
        }
    return {
        "status": "pending",
        "severity": "none",
        "days_elapsed": days,
        "message": f"{test_type} pending ({days} days since order)."
    }
