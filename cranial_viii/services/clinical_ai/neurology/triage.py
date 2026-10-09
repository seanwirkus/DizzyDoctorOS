"""
Complex Neurology Triage & Clinical Decision Support
Synthesizes clinical narratives, cranial nerve signs, autonomic symptoms, and ataxia.
"""

from typing import Dict, List, Optional
from services.clinical_ai.offline_llm import offline_ai


NEUROLOGY_SYSTEM_PROMPT = """You are CranialVIII, an expert offline clinical neurology and otoneurology consultant.
Analyze the clinical narrative and examination findings with rigorous neuro-anatomical localization.
Focus on:
1. Cranial nerve mapping (specifically CN V, VII, VIII, IX, X, XII).
2. Central vs Peripheral differentiation.
3. Cerebellar localization (vermis vs hemispheres, truncal vs limb ataxia).
4. Red flags requiring emergent imaging (mass effect, posterior fossa stroke, Chiari malformation, hydrocephalus).
Format with concise clinical headings: Anatomical Localization, Differential Diagnoses, Red Flags, Recommended Testing.
"""


async def triage_neurology_consult(
    patient_age: Optional[int],
    chief_complaint: str,
    exam_findings: str,
    medications: Optional[str] = None
) -> str:
    """Invokes local offline LLM to produce a structured neuro-anatomical synthesis."""
    user_prompt = f"""PATIENT CLINICAL SUMMARY:
- Age: {patient_age or 'Unknown'}
- Chief Complaint & History: {chief_complaint}
- Physical & Neurological Exam: {exam_findings}
- Medications: {medications or 'None reported'}

Provide clinical decision support and differential diagnosis with anatomical localization."""

    messages = [
        {"role": "system", "content": NEUROLOGY_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]
    
    return await offline_ai.chat_completion(messages=messages, temperature=0.1)
