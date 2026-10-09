"""
Differential Diagnosis Engine: Vestibular Migraine vs. Menière's Disease vs. PPPD
Applies consensus diagnostic criteria from the Bárány Society and International Headache Society (ICHD-3).
"""

from typing import Dict, List, Optional


def differentiate_vestibular_disorder(
    episode_duration: str,          # 'seconds', 'minutes_to_hours', 'hours_to_days', 'constant_months'
    has_headache_history: bool,
    has_migraine_features: bool,    # photophobia, phonophobia, visual aura
    has_aural_fullness: bool,
    has_fluctuating_hearing_loss: bool,
    has_tinnitus: bool,
    audiogram_low_freq_loss: bool,
    chronic_unsteadiness_gt_3mo: bool = False,
    worse_with_upright_posture: bool = False
) -> Dict:
    """
    Evaluates clinical constellation against consensus diagnostic criteria:
    - Menière's Disease (Definite vs Probable)
    - Vestibular Migraine (Definite vs Probable)
    - PPPD (Persistent Postural-Perceptual Dizziness)
    """
    scores = {
        "vestibular_migraine": 0,
        "menieres_disease": 0,
        "pppd": 0
    }
    evidence: Dict[str, List[str]] = {
        "vestibular_migraine": [],
        "menieres_disease": [],
        "pppd": []
    }

    # Vestibular Migraine criteria:
    # 5+ episodes lasting 5 minutes to 72 hours, history of migraine, >=50% episodes with migraine features
    if has_headache_history:
        scores["vestibular_migraine"] += 2
        evidence["vestibular_migraine"].append("Positive personal or family history of migraine.")
    if has_migraine_features:
        scores["vestibular_migraine"] += 3
        evidence["vestibular_migraine"].append("Migrainous features present (photophobia, phonophobia, or visual aura during episodes).")
    if episode_duration in ("minutes_to_hours", "hours_to_days"):
        scores["vestibular_migraine"] += 2

    # Menière's Disease criteria:
    # 2+ spontaneous episodes lasting 20 min to 12 hours, audiometrically documented low-to-mid frequency SNHL, fluctuating aural symptoms
    if audiogram_low_freq_loss:
        scores["menieres_disease"] += 4
        evidence["menieres_disease"].append("Pathognomonic low-to-mid frequency sensorineural hearing loss on audiogram.")
    if has_fluctuating_hearing_loss:
        scores["menieres_disease"] += 2
        evidence["menieres_disease"].append("Fluctuating hearing loss in affected ear.")
    if has_aural_fullness and has_tinnitus:
        scores["menieres_disease"] += 2
        evidence["menieres_disease"].append("Classic episodic aural fullness and roaring tinnitus.")
    if episode_duration == "minutes_to_hours":
        scores["menieres_disease"] += 2
        evidence["menieres_disease"].append("Episode duration (20 minutes to 12 hours) matches classic endolymphatic hydrops.")

    # PPPD (Persistent Postural-Perceptual Dizziness):
    # Persistent non-spinning dizziness/unsteadiness on most days for >3 months, worse upright/moving/visual stimuli
    if chronic_unsteadiness_gt_3mo:
        scores["pppd"] += 4
        evidence["pppd"].append("Chronic unsteadiness present for >= 3 months on most days.")
    if worse_with_upright_posture:
        scores["pppd"] += 3
        evidence["pppd"].append("Symptoms exacerbated by upright posture, active/passive motion, and complex visual environments.")

    # Determine leading classification
    leading = max(scores, key=scores.get)
    if scores[leading] == 0:
        diagnosis = "Undifferentiated Vestibular Disorder"
        confidence = "Low"
    else:
        diagnosis = leading.replace("_", " ").title()
        confidence = "High" if scores[leading] >= 6 else "Moderate"

    return {
        "primary_differential": diagnosis,
        "confidence": confidence,
        "scores": scores,
        "supporting_evidence": evidence[leading] if leading in evidence else [],
        "hybrid_note": (
            "Note: Vestibular Migraine and Menière's Disease co-occur in up to 30% of patients. "
            "Consider dual therapy if both cochlear loss and migrainous headaches persist."
        ) if scores["vestibular_migraine"] >= 4 and scores["menieres_disease"] >= 4 else ""
    }
