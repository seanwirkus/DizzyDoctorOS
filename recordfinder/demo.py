"""Made-up demo clinic for testing (no real patients): a fake drive, a Missing Folder and a Teams chat.

Every name, number and date here is invented. Safe to send to Claude before the BAA is signed.
"""
from __future__ import annotations

import os

CHAT = """Pls provide M/R for Pt: Alice B Sample... by Demo Billing
Demo Billing
Tuesday 8:17 AM
Pls provide M/R for Pt: Alice B Sample Acct#:123456. Aetna is downcoding and underpaying from 99205 to 99204 for DOS 9/3/2026

1 Like reaction.
PLEASE PROVIDE M/R TO APPEAL FOR ACCOUNT 23... by Demo Billing
Demo Billing
Monday 9:34 AM
PLEASE PROVIDE M/R TO APPEAL FOR ACCOUNT 234567 PT BOB C TESTER DOS 09/01/2026. AETNA IS DOWN CODING FROM 99215 TO 99213.
  ACCOUNT 345678 PT Carl D Example DOS 09... by Demo Billing
Demo Billing
Wednesday 10:35 AM
 ACCOUNT 345678 PT Carl D Example DOS 09/08/2026 MISSING AUTH FOR 92550 AND 92548
NOT ON SCMG PORTAL
account 456789 pt Dana E Fake DOS 09/17/... by Demo Billing
Demo Billing
Wednesday 1:43 PM
account 456789 pt Dana E Fake DOS 09/17/2026 TY  pt wrote that he has new insurance under Medicare..... NO INS CARDS WERE PROVIDED
"""

NOTE_TESTER = """DEMO CLINIC - PROGRESS NOTE  (MADE-UP DEMO DATA)
Patient: Bob C Tester    Acct: 234567    DOB: 01/01/1960    DOS: 09/01/2026
Provider: Demo Physician, MD    Visit type: Established patient, follow-up

History: Recurrent positional vertigo for 3 weeks, worse rolling to the right, with nausea and two near-falls at home.
Also new intermittent tinnitus in the left ear. On meclizine without benefit. Reviewed outside MRI brain (normal).
Exam: Right Dix-Hallpike positive with upbeating torsional nystagmus. Gait wide-based. Head impulse test normal.
Assessment: 1) Right posterior canal BPPV. 2) Fall risk. 3) Left tinnitus, new - audiology referral.
Plan: Epley maneuver performed in clinic. Home exercises taught. Stop meclizine. Fall-precaution counselling.
Order audiogram and VNG. Return in 4 weeks. Discussed risk of injury from falls; high complexity decision making.
Total time on date of service: 45 minutes, including review of outside records and coordination with audiology.
"""

EOB_TESTER = """AETNA (DEMO) - EXPLANATION OF BENEFITS   (MADE-UP DEMO DATA)
Member: BOB C TESTER    Member ID: DEMO000234    Claim #: DEMO-CLM-0001    Patient acct: 234567
Provider: DEMO CLINIC

DOS         CPT (billed)  CPT (processed)  Billed    Allowed   Paid     Pt resp  Codes
09/01/2026  99215         99213            $310.00   $118.40   $94.72   $23.68   CO-150, N390

CO-150: Payer deems the information submitted does not support this level of service.
N390: This service/report cannot be billed separately. (demo remark)
Appeal within 180 days of this notice.
"""

AUTH_EXAMPLE = """SCMG (DEMO) - AUTHORIZATION STATUS   (MADE-UP DEMO DATA)
Member: CARL D EXAMPLE    Acct: 345678
Requested: 92550 Tympanometry & reflex, 92548 CDP/SOT     Status: NOT FOUND on portal as of 09/20/2026
"""

FILES = {
    "Patients/Tester, Bob 234567/Progress Note 09-01-2026.pdf": NOTE_TESTER,
    "Patients/Tester, Bob 234567/Aetna EOB 09-01-2026.pdf": EOB_TESTER,
    "Patients/Tester, Bob 234567/Insurance card.txt": "AETNA (DEMO) member card - BOB C TESTER - DEMO000234 (made-up)",
    "Patients/Example, Carl 345678/SCMG auth status 09-20-2026.pdf": AUTH_EXAMPLE,
    "Patients/Sample, Alice 123456/Progress Note 09-03-2026.txt": "Made-up demo note for Alice B Sample, DOS 09/03/2026. New patient, 60 minutes.",
}


def _write_pdf(path: str, text: str) -> bool:
    try:
        import pymupdf
    except ImportError:
        return False
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(40, 40, 570, 800), text, fontsize=9, fontname="cour")
    doc.save(path)
    doc.close()
    return True


def create(root: str) -> dict:
    """Write the demo clinic under root. Returns the settings to use for it."""
    for rel, text in FILES.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if path.endswith(".pdf") and _write_pdf(path, text):
            continue
        with open(os.path.splitext(path)[0] + (".txt" if path.endswith(".pdf") else os.path.splitext(path)[1]), "w") as f:
            f.write(text)
    missing = os.path.join(root, "Missing Folder")
    os.makedirs(missing, exist_ok=True)
    with open(os.path.join(root, "teams_chat_demo.txt"), "w") as f:
        f.write(CHAT)
    return {"search_folders": [os.path.join(root, "Patients")], "missing_folder": missing,
            "staff_names": ["Demo Front Desk"], "signature": "*DEMO"}
