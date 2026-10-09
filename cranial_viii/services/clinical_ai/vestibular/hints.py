"""
Clinical Triage: HINTS & HINTS Plus Exam Interpreter
Differentiates Central Posterior Circulation Stroke (PICA/AICA)
from Acute Peripheral Vestibular Neuritis in Acute Vestibular Syndrome (AVS).
More sensitive than early MRI DWI within the first 24-48 hours.
"""

from typing import Dict, List


def evaluate_hints_exam(
    head_impulse: str,      # 'normal' or 'abnormal_saccade'
    nystagmus_pattern: str, # 'unidirectional_horizontal' or 'direction_changing_bidirectional' or 'vertical'
    test_of_skew: str,      # 'none' or 'skew_deviation_vertical_refixation'
    acute_hearing_loss: bool = False  # HINTS Plus component (AICA stroke indicator)
) -> Dict:
    """
    Evaluates the three HINTS exam components:
    - Central Danger Sign 1: Normal Head Impulse Test (HIT) in the presence of spontaneous nystagmus
    - Central Danger Sign 2: Direction-changing nystagmus on eccentric lateral gaze
    - Central Danger Sign 3: Skew deviation (vertical misalignment of eyes on alternating cover test)
    - HINTS Plus: Acute new sensorineural hearing loss (AICA stroke / labyrinthine artery infarction)
    """
    central_flags: List[str] = []
    peripheral_findings: List[str] = []

    # 1. Head Impulse Test (HIT)
    # Counterintuitively: A NORMAL head impulse test in acute vertigo is DANGEROUS (central stroke).
    # An ABNORMAL catch-up saccade confirms peripheral vestibular ocular reflex (VOR) deficit (neuritis).
    if head_impulse.lower() in ("normal", "intact", "no_saccade"):
        central_flags.append("Normal Head Impulse Test in acute continuous vertigo strongly suggests central brainstem/cerebellar stroke.")
    else:
        peripheral_findings.append("Abnormal Head Impulse Test (corrective refixation saccade) consistent with peripheral VOR deficit.")

    # 2. Nystagmus
    # Peripheral nystagmus is unidirectional (fast phase always beats in one direction, Alexander's law).
    # Central nystagmus changes direction when looking left vs right, or is purely vertical/torsional.
    if "direction_changing" in nystagmus_pattern.lower() or "bidirectional" in nystagmus_pattern.lower():
        central_flags.append("Direction-changing gaze-evoked nystagmus is a pathognomonic central sign.")
    elif "vertical" in nystagmus_pattern.lower() or "pure_torsional" in nystagmus_pattern.lower():
        central_flags.append("Pure vertical (downbeat/upbeat) or pure torsional nystagmus indicates central brainstem/cerebellar pathology.")
    else:
        peripheral_findings.append("Unidirectional horizontal-torsional nystagmus consistent with peripheral vestibular deafferentation.")

    # 3. Test of Skew
    # Skew deviation is a vertical ocular misalignment caused by asymmetric otolithic input to brainstem.
    if "skew" in test_of_skew.lower() or "refixation" in test_of_skew.lower():
        central_flags.append("Skew deviation (vertical refixation on cross-cover test) indicates central vestibular pathway disruption.")
    else:
        peripheral_findings.append("No skew deviation detected.")

    # 4. HINTS Plus: Acute Hearing Loss
    if acute_hearing_loss:
        central_flags.append("Acute new-onset unilateral hearing loss in acute vertigo is a hallmark of AICA stroke (internal auditory artery occlusion).")

    is_central = len(central_flags) > 0
    triage_level = "EMERGENCY_CENTRAL_STROKE" if is_central else "PERIPHERAL_ACUTE_VESTIBULAR_SYNDROME"
    
    return {
        "triage_classification": triage_level,
        "is_central_danger": is_central,
        "central_flags": central_flags,
        "peripheral_findings": peripheral_findings,
        "action_plan": (
            "STAT Emergency Neurological Evaluation & Brain MRI with DWI (Diffusion-Weighted Imaging). "
            "High suspicion for cerebellar or brainstem stroke (PICA/AICA)."
        ) if is_central else (
            "Supportive care for acute vestibular neuritis. Vestibular physical therapy referral. "
            "Rule out labyrinthitis if auditory symptoms present. Short-term vestibular suppressants only."
        )
    }
