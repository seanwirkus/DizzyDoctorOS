"""
Otoneurology Clinical Intelligence: BPPV Diagnostic & Treatment Engine
Implements Barany Society consensus criteria for Benign Paroxysmal Positional Vertigo:
- Semicircular canal localization (Posterior, Horizontal, Anterior)
- Pathophysiologic mechanism (Canalithiasis vs Cupulolithiasis)
- Canal repositioning maneuver selection (Epley, Semont, Lempert 360, Gufoni, Yacovino)
"""

from typing import Dict, Optional


def analyze_bppv(
    dix_hallpike_right: Optional[str] = None,
    dix_hallpike_left: Optional[str] = None,
    supine_roll_right: Optional[str] = None,
    supine_roll_left: Optional[str] = None,
    nystagmus_latency_sec: Optional[float] = None,
    nystagmus_duration_sec: Optional[float] = None,
    nystagmus_type: Optional[str] = None  # e.g., "upbeating_torsional", "geotropic", "apogeotropic", "downbeating"
) -> Dict:
    """
    Evaluates vestibular provocative testing to determine exact canal, mechanism, and maneuver.
    """
    canal = "Undetermined"
    ear = "Undetermined"
    mechanism = "Canalithiasis"
    recommended_maneuvers = []
    contraindications = []
    clinical_rationale = []

    # Mechanism differentiation: duration < 60s is typical canalithiasis; persistent >= 60s is cupulolithiasis
    if nystagmus_duration_sec and nystagmus_duration_sec >= 60:
        mechanism = "Cupulolithiasis"
        clinical_rationale.append("Duration >= 60 seconds indicates otoconia adhered to the cupula (Cupulolithiasis).")
    elif nystagmus_latency_sec and nystagmus_latency_sec > 0:
        mechanism = "Canalithiasis"
        clinical_rationale.append(f"Latency of {nystagmus_latency_sec}s with fatigue indicates free-floating otoconia (Canalithiasis).")

    # Posterior Canal BPPV (approx 85-90% of cases)
    # Dix-Hallpike produces upbeating and torsional nystagmus toward the affected dependent ear
    if dix_hallpike_right and "positive" in dix_hallpike_right.lower() and dix_hallpike_left != "positive":
        canal = "Posterior Canal"
        ear = "Right"
        if mechanism == "Canalithiasis":
            recommended_maneuvers = ["Right Epley Maneuver", "Right Semont Maneuver"]
            clinical_rationale.append("Right Dix-Hallpike positive with classic upbeating/torsional nystagmus.")
        else:
            recommended_maneuvers = ["Right Semont Plus Maneuver", "Canal Jamming"]
            clinical_rationale.append("Right posterior cupulolithiasis requires Semont plus with rapid acceleration.")

    elif dix_hallpike_left and "positive" in dix_hallpike_left.lower() and dix_hallpike_right != "positive":
        canal = "Posterior Canal"
        ear = "Left"
        if mechanism == "Canalithiasis":
            recommended_maneuvers = ["Left Epley Maneuver", "Left Semont Maneuver"]
            clinical_rationale.append("Left Dix-Hallpike positive with classic upbeating/torsional nystagmus.")
        else:
            recommended_maneuvers = ["Left Semont Plus Maneuver"]
            clinical_rationale.append("Left posterior cupulolithiasis.")

    # Horizontal (Lateral) Canal BPPV (approx 5-10% of cases)
    # Supine Roll Test shows horizontal nystagmus:
    # - Geotropic (towards ground): Canalithiasis. Affected ear has stronger nystagmus.
    # - Apogeotropic (away from ground): Cupulolithiasis. Affected ear has weaker nystagmus.
    elif supine_roll_right or supine_roll_left:
        canal = "Horizontal (Lateral) Canal"
        if nystagmus_type and "apogeotropic" in nystagmus_type.lower():
            mechanism = "Cupulolithiasis"
            recommended_maneuvers = ["Gufoni Maneuver (for apogeotropic)", "Head Shake 30s", "Cupulolith Repositioning"]
            clinical_rationale.append("Horizontal apogeotropic nystagmus indicates otoconia on the utricular or canal side of cupula.")
        else:
            mechanism = "Canalithiasis"
            recommended_maneuvers = ["Lempert 360 BBQ Roll", "Gufoni Maneuver (for geotropic)", "Vannucchi Prolonged Forced Position"]
            clinical_rationale.append("Horizontal geotropic nystagmus indicates horizontal canalithiasis.")

    # Anterior (Superior) Canal BPPV (rare, < 2%)
    elif nystagmus_type and "downbeat" in nystagmus_type.lower():
        canal = "Anterior (Superior) Canal"
        recommended_maneuvers = ["Yacovino Maneuver", "Reverse Epley"]
        clinical_rationale.append("Downbeating nystagmus on Dix-Hallpike without visual fixation. Note: Rule out central cerebellar/brainstem etiology if non-fatiguing.")

    return {
        "affected_canal": canal,
        "affected_ear": ear,
        "mechanism": mechanism,
        "recommended_maneuvers": recommended_maneuvers,
        "contraindications": ["Severe cervical spine stenosis/radiculopathy", "Carotid artery dissection risk", "Severe rheumatoid arthritis"],
        "clinical_rationale": clinical_rationale,
        "red_flags": [
            "Downbeating nystagmus with no latency and no fatigue (Central Cerebellar Lesion / Chiari)",
            "Direction-changing nystagmus without positional change",
            "Accompanying cranial nerve palsies or dysarthria"
        ]
    }
