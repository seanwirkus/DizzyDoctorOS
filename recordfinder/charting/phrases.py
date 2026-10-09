"""The practice's own note language, learned from how its progress notes are actually written.

Every entry here is wording that recurs across many different patients' notes (so it is the
clinic's template language, not anyone's history). The chart screen offers these as one-tap
phrases and the dictation mapper recognises them; nothing here identifies a patient.
"""
from __future__ import annotations

# HPI (ChiefComplaint-descr): the opening template is printed on the blank form itself.
HPI_OPENING = "{title} {last} is a {age} y/o {hand}HD {sex} presenting to the clinic today for {reason}. PMHx significant for {pmhx}"
HPI_OPENING_FOLLOWUP = "{title} {last} is a {age} y/o {hand}HD {sex} presenting to the clinic today for a follow-up on {reason}. Last seen on {last_seen}."
HPI_CLOSERS = [
    "No other focal motor, sensory, or CN deficits reported at this time.",
    "Patient denies any other focal motor/sensory/cranial nerve complaints at this time.",
]
HPI_INTERIM = [
    "Last seen on {last_seen}.",
    "Pt was last seen in clinic on {last_seen}.",
    "Denies any falls over the interim.",
    "Pt reports no falls in the interim.",
    "Denies any falls in the interim.",
    "Patient denies any falls over the interim.",
    "Denies any recent falls.",
]
HPI_NEGATIVES = [
    "Denies associated fluctuating hearing loss, tinnitus or aural fullness.",
    "Denies associated hearing loss/fluctuations, aural fullness, or tinnitus with dizziness.",
    "Denies chronic baseline disequilibrium.",
    "Denies any chest pain, heart palpitations, or shortness of breath.",
    "Denies any BLE weakness, hypo/paresthesias, or b/b incontinence.",
    "Denies any associated N/V.",
    "Patient denies completing MRI Brain, audiogram, VNG or VEMP testing.",
    "PMHx nonsignificant per patient report.",
]
HPI_COMPANION = [
    "Patient presents to clinic with his wife who aids in history.",
    "Patient presents with her husband today.",
    "Patient presents to clinic with {companion} who aids in history.",
]
MED_RECONCILIATION = [
    "**I, {initials}, independently performed medication reconciliation**",
    "***I, {initials}, independently reviewed the medication list with patient, {date}***",
    "Medication list was reviewed with patient today and reflected in chart note.",
    "--PA {initials} reviewed and updated medication list with patient today.",
]
MEDS_HEADER = "--------PO Medications--------"

# Gait section notes (ga-other-descr) and descriptions
GAIT_DESCR = ["2/2 dizziness", "2/2 above dizziness", "2/2 above imbalance", "no current complaints"]
GAIT_FALLS = [
    "Pt reports no falls in the interim.",
    "No hx of falls",
    "No falls 2/2 above dizziness.",
    "No falls reported with above events.",
    "No falls reported over the interim.",
    "No recent falls reported.",
]
DIZZ_FREQ = ["paroxysmal", "chronic", "episodic", "daily", "no current complaints", "more akin to imbalance",
             "Paroxysmal w/ positional triggers", "Imbalance w/ gait", "Resolved"]
ONSET_WORDS = ["Longstanding", "longstanding hx", "see below", "2/2 dizziness"]

# Exam findings: the normal text used when a row is marked normal, and the practice's standard
# wording for common findings.
EXAM_NORMALS = {
    "findings3": ["No papilledema", "(-) papilledema", "(-) papilledema. No TTP or nodularity over b/l temporal ridges."],
    "findings4": ["VF intact", "Visual fields intact to all 4 quadrants; no vertical or horizontal diplopia"],
    "findings5": ["4mm OD, 3mm OS, both reactive to light and accommodation"],
    "findings6": ["Scalloping of the tongue", "Narrow POP", "Scalloping of the tongue, narrow POP"],
    "findings7": ["Ceruminosis AU", "Ceruminosis AD", "Ceruminosis AS"],
    "findings8": ["RRR"],
    "findings9": ["No MRG or carotid bruits appreciated", "No carotid bruits", "No MRG"],
    "findings12": ["A&O x 3", "A&O x 3; MoCA ({date}) {score}/30"],
    "findings14": ["Pleasant", "Very pleasant", "Lucid and appropriate on exam", "Appropriate mood & affect, stable"],
    "findings24": [
        "Smooth pursuits and saccades intact",
        "EOM's intact. Smooth saccades and pursuits",
        "Smooth pursuits and saccades intact. Cursory Dix Hallpike/supine positional testing",
        "Left gaze evoked nystagmus", "Right gaze evoked nystagmus", "mild left gaze evoked nystagmus",
        "no gaze evoked nystagmus",
    ],
    "findings25": [
        "revealed atypical upbeat nystagmus and patient is symptomatic",
        "Cursory Dix Hallpike and static positioning tests for benign paroxysmal positional",
        "pt was symptomatic",
    ],
    "findings26": ["vertigo were suggestive of atypical nystagmus and symptom provoking."],
    "findings27": [
        "No deficits to finger friction rub and 128hz tuning fork AU",
        "Mild deficits to finger friction rub and 128hz tuning fork AU",
        "Decreased hearing to finger friction rub and 128Hz tuning fork AS",
        "Decreased hearing to finger friction rub and 128Hz tuning fork AD",
        "Hearing WNL AU.", "Hearing aids AU",
    ],
    "findings31": ["5/5 strength x 4 extremities", "5/5 BUE/BLE", "5/5 BUE, 5/5 BLE", "5/5 throughout"],
    "findings32": ["normal tone"],
    "findings33": ["Sensations grossly intact to light touch and temperatures",
                   "Intact touch / position / vibration throughout", "Light touch intact BUE/BLE",
                   "Decrease sensation of BUE to touch/temp/vibration", "Decreased sensation of BLE to touch/temp/vibration"],
    "findings36": ["(-) Hoffman's Sign", "(-) hoffman's", "(-) Hoffman's BUE", "No dysdiadochokinesia"],
    "findings37": ["No dysdiadochokinesia", "no dysmetria or dysdiadochokinesia", "No dysmetria",
                   "0/4 finger flicks, 0/4 finger taps"],
    "findings38": ["0/4 finger taps, 0/4 finger flicks, 0/4 finger counts", "0/4 finger flicks, 0/4 finger taps",
                   "No dysdiadochokinesia"],
    "findings39": ["Normal gait", "Mild sway on Romberg", "(+) Mild sway on Romberg", "Sway on Romberg",
                   "Moderate sway on Romberg", "Normal gait. Mild sway on Romberg"],
    "findings1": ["(-) Cursory Dix Hallpike and supine positional testing for atypical nystagmus", "mask like facies"],
    "findings23": ["X - Cursory Dix-Hallpike and supine positional testing revealed atypical nystagmus,"],
}
# Which of the wording above the note builder writes on a row marked normal, and which describes an
# abnormal finding (dictating one of those marks its row abnormal). Every entry is one of the above.
EXAM_NORMAL_TEXT = {
    "findings3": "No papilledema", "findings4": "VF intact", "findings8": "RRR",
    "findings9": "No MRG or carotid bruits appreciated", "findings12": "A&O x 3", "findings14": "Pleasant",
    "findings24": "Smooth pursuits and saccades intact",
    "findings27": "No deficits to finger friction rub and 128hz tuning fork AU",
    "findings31": "5/5 strength x 4 extremities", "findings32": "normal tone",
    "findings33": "Sensations grossly intact to light touch and temperatures", "findings36": "(-) Hoffman's Sign",
    "findings37": "no dysmetria or dysdiadochokinesia", "findings38": "0/4 finger taps, 0/4 finger flicks, 0/4 finger counts",
    "findings39": "Normal gait",
}
EXAM_ABNORMAL = {
    "findings1": ["mask like facies"],
    "findings6": ["Scalloping of the tongue", "Narrow POP", "Scalloping of the tongue, narrow POP"],
    "findings7": ["Ceruminosis AU", "Ceruminosis AD", "Ceruminosis AS"],
    "findings24": ["Left gaze evoked nystagmus", "Right gaze evoked nystagmus", "mild left gaze evoked nystagmus"],
    "findings27": ["Mild deficits to finger friction rub and 128hz tuning fork AU",
                   "Decreased hearing to finger friction rub and 128Hz tuning fork AS",
                   "Decreased hearing to finger friction rub and 128Hz tuning fork AD", "Hearing aids AU"],
    "findings33": ["Decrease sensation of BUE to touch/temp/vibration", "Decreased sensation of BLE to touch/temp/vibration"],
    "findings39": ["Mild sway on Romberg", "(+) Mild sway on Romberg", "Sway on Romberg", "Moderate sway on Romberg",
                   "Normal gait. Mild sway on Romberg"],
}
# Exam wording people say differently than the note writes it.
EXAM_SPOKEN = {"regular rate and rhythm": "RRR", "alert and oriented x 3": "A&O x 3", "no murmurs rubs or gallops": "No MRG"}
DTR_VALUES = ["0", "1-", "1", "1+", "2-", "2", "2+", "3", "3+", "4"]
GOGGLE_TREATMENT = [
    "Normal study. No treatment indicated.",
    "No evidence of sustained resting or evoked nystagmus, no treatment indicated.",
    "Normal study, no treatment indicated.",
    "Right posterior canal BPPV: Epley maneuver performed to the right; patient tolerated well.",
    "Left posterior canal BPPV: Epley maneuver performed to the left; patient tolerated well.",
]
# Positional testing as the impression writes it.
POSITIONAL_NEGATIVE = "IR Oculography ({date}) was negative."
POSITIONAL_ATYPICAL = ("Cursory DixHallpike test for benign paroxysmal positional vertigo and static positioning tests "
                       "for BPPV were suggestive of atypical {direction}nystagmus.")

# Impression / plan
IMPRESSION_SECONDARY = "Secondary to #{n}."
IMPRESSION_SEE_PLAN = ["Plan as in #{n} above.", "Further plan as in #{n}.", "See and plan as in #{n}.", "Plan as in above."]
IMPRESSION_SENTENCES = [
    "Monitor clinically.",
    "Follow clinically.",
    "Keep balance log.",
    "Continue balance log.",
    "Keep symptoms log.",
    "Continue symptoms log.",
    "Keep hearing log.",
    "Keep sxs log and RTC should sxs worsen.",
    "Patient instructed to keep balance log and RTC if symptoms worsen.",
    "RTC with worsening symptoms.",
    "RTC w/ worsening sxs.",
    "RTC if symptoms worsen.",
    "RTC with acute episode.",
    "RTC with acute vertigo.",
    "Strict fall precautions.",
    "Reinforced strict fall precautions.",
    "Reinforced fall precautions.",
    "Discussed fall precautions.",
    "No treatment indicated.",
    "Consider Zofran should symptoms worsen.",
    "No complaints of lower extremity weakness, paresthesias or bowel/bladder incontinence.",
    "No hx/exam finding to suggest CCS w/wo myelopathy.",
    "There are no history or exam findings that suggest cervical stenosis w/wo myelopathy.",
    "There was no hx or exam finding to further suggest VBI.",
    "There is no hx or exam finding to further suggest vertebral basilar insufficiency (VBI).",
    "No hx/exam finding to further suggest VBI.",
    "Metabolic w/u ({date}) noted on pg 2.",
    "IR Oculography ({date}) was negative.",
    "Cursory DixHallpike test for benign paroxysmal positional vertigo and static positioning tests for BPPV were suggestive of atypical upbeat nystagmus and sx provoking.",
    "PE as on page 3.",
    "Denies associated fluctuating hearing loss, tinnitus or aural fullness.",
]
# Numbered impressions seen most often, with the ICD-10 the practice bills for them (verify
# against the superbill; a coder should confirm before relying on these).
DIAGNOSES = [
    ("Dizziness and lack of coordination", "R42"),
    ("Gait and balance difficulties", "R26.89"),
    ("Gait difficulties", "R26.9"),
    ("Complex BPPV", "H81.10"),
    ("Right posterior canal BPPV", "H81.11"),
    ("Left posterior canal BPPV", "H81.12"),
    ("Right lateral canal BPPV", "H81.11"),
    ("Left lateral canal BPPV", "H81.12"),
    ("Right anterior canal BPPV", "H81.11"),
    ("Left anterior canal BPPV", "H81.12"),
    ("Right cupulolithiasis", "H81.11"),
    ("Left cupulolithiasis", "H81.12"),
    ("Probable BPPV", "H81.10"),
    ("Peripheral vestibulopathy AD", "H81.91"),
    ("Peripheral vestibulopathy AS", "H81.92"),
    ("Atypical peripheral vestibulopathy", "H81.90"),
    ("Headaches", "R51.9"),
    ("Headaches with migrainous features", "G43.909"),
    ("Migraines", "G43.909"),
    ("Chronic migraines", "G43.709"),
    ("Migraine variant", "G43.809"),
    ("Nausea and vomiting", "R11.2"),
    ("Nausea w/o vomiting", "R11.0"),
    ("Hearing loss", "H91.90"),
    ("Hearing loss AU", "H91.93"),
    ("Hearing loss AD", "H91.91"),
    ("Hearing loss AS", "H91.92"),
    ("SNHL AU", "H90.3"),
    ("SNHL AD", "H90.41"),
    ("SNHL AS", "H90.42"),
    ("Tinnitus AU", "H93.13"),
    ("Tinnitus AD", "H93.11"),
    ("Tinnitus AS", "H93.12"),
    ("Cognitive difficulties", "R41.89"),
    ("Memory difficulties", "R41.3"),
    ("Mild cognitive impairment", "G31.84"),
    ("Peripheral neuropathy", "G62.9"),
    ("Diabetic peripheral neuropathy", "E11.42"),
    ("Post-concussive syndrome", "F07.81"),
    ("Cervicalgia", "M54.2"),
    ("Cervicalgia / myofascial pain", "M54.2"),
    ("Occipital neuralgia", "M54.81"),
    ("Lumbago", "M54.50"),
    ("Mild OSA", "G47.33"),
    ("Moderate OSA", "G47.33"),
    ("Severe OSA", "G47.33"),
    ("Sleep disturbances", "G47.9"),
    ("Insomnia", "G47.00"),
    ("Parkinson's disease", "G20.A1"),
    ("Parkinsonism", "G20.C"),
    ("Essential tremor", "G25.0"),
    ("Tremor", "R25.1"),
    ("Orthostatic hypotension", "I95.1"),
    ("Seizures", "R56.9"),
    ("Episodes of unclear etiology", "R55"),
    ("Visual disturbances", "H53.9"),
    ("Cerumen impaction AU", "H61.23"),
    ("Restless leg syndrome", "G25.81"),
    ("Chronic fatigue", "R53.82"),
    ("Anxiety", "F41.9"),
    ("TIA", "G45.9"),
    ("B/L CTS", "G56.03"),
    ("Cervical radiculopathy", "M54.12"),
    ("Cervical stenosis", "M48.02"),
    ("Lumbar stenosis", "M48.062"),
    ("Dysphagia", "R13.10"),
    ("Depression", "F32.A"),
    ("Diplopia OU", "H53.2"),
    ("Alzheimer's disease", "G30.9"),
]

# Past history boxes as the HPI opening lists them ("PMHx significant for HTN, DM and dyslipidemia.").
PMHX_WORDS = {"hist-hypt": "HTN", "hist-diab": "DM", "hist-dyslip": "dyslipidemia", "hist-cad": "CAD",
              "hist-arrh": "cardiac arrhythmia", "hist-stroke": "stroke", "hist-migraine": "migraines",
              "hist-cervical": "cervical disease", "hist-trauma": "head trauma w/ LOC", "hist-ear": "severe ear infections",
              "hist-ototox": "ototoxic drug exposure"}
PMHX_NONE = "PMHx nonsignificant per patient report."
# Plan boxes as the numbered impressions write them: (first visit, follow-up).
PLAN_SENTENCES = {
    "plan-balancelog": ("Keep balance log.", "Continue balance log."),
    "plan-halog": ("Keep HA log.", "Continue HA log."),
    "plan-fall": ("Strict fall precautions.", "Reinforced strict fall precautions."),
    "plan-continue": ("Continue home vis/vest exercises.", "Continue home vis/vest exercises."),
    "plan-horizontal": ("Keep head greater than earth horizontal.", "Keep head greater than earth horizontal."),
    "plan-sleep": ("Sleep w/ head elevated 45 degrees.", "Sleep w/ head elevated 45 degrees."),
    "plan-driving": ("Instructed not to drive when experiencing episodes of vertigo or disequilibrium.",
                     "Instructed not to drive when experiencing episodes of vertigo or disequilibrium."),
    "plan-meds": ("Take medications as prescribed.", "Take medications as prescribed."),
    "plan-prophy": ("Advised two forms of birth control on these medications.",
                    "Advised two forms of birth control on these medications."),
    "plan-return": ("RTC with worsening symptoms.", "RTC with worsening symptoms."),
}
PLAN_ADVERSE = "Discussed indications/contraindications/adverse effects of {meds}."
CCS_NEGATIVE = "No hx/exam finding to suggest CCS w/wo myelopathy."

# Staff plan lines (plan-descr)
PLAN_STAFF = [
    "STAFF: Refer to Dr. {doctor}",
    "STAFF: Fax today's note to Dr. {doctor}",
    "Continue with Dr. {doctor}",
    "RTC should sxs worsen",
    "IR Oculography+tx today",
]
NEXT_APPT = ["3-4", "4-6", "4", "3", "2-3", "6-8", "6", "4-5", "1", "8", "2", "12"]
NEXT_APPT_NOTE = "or sooner PRN"
REFERRAL_SPECIALTIES = ["Cardiology", "Audiology", "Neurology", "ENT", "Pain management", "Neurosurgery",
                        "Ophthalmology", "Physical therapy", "Sleep medicine", "Rheumatology"]
PT_REFERRAL = ["Visual/vestibular PT. Dx: peripheral vestibulopathy. 2x/week for 6 weeks.",
               "Vestibular PT. Dx: gait and balance difficulties. 2x/week for 6-8 weeks."]
# The PDF has two order boxes, but the web app presents them as editable combo boxes.  Keep the
# form's original choices and add the orders the clinic needs to be able to find quickly; a custom
# order can still be typed, and a third order is kept in the plan continuation rather than lost.
IMAGING_ORDERS = [
    "CT Brain with and without contrast 70470", "CT Brain without contrast 70450",
    "CT Petrous Bone 3-D reconstruction 70480", "CTA Head", "CTA Head and Neck",
    "CT Cervical Spine without contrast", "CT Thoracic Spine without contrast", "CT Lumbar Spine without contrast",
    "MRA Brain Circle of Willis w/o contrast 70544", "MRA Neck with contrast 70544", "MRA Neck without contrast",
    "MRA Neck with and without contrast", "MRI Brain FULL with IAC and Contrast",
    "MRI Brain IAC –Screening Protocol 70551", "MRI Brain IAC with and without contrast",
    "MRI Brain with and w/o contrast 70553", "MRI Brain without contrast 70551", "MRI Orbits with and without contrast",
    "MRI Pituitary with and without contrast", "MRI C Spine without contrast 72141",
    "MRI C Spine with and without contrast", "MRI T Spine without contrast 72146",
    "MRI T Spine with and without contrast", "MRI L Spine without contrast 72148",
    "MRI L Spine with and without contrast", "Carotid duplex ultrasound", "DaTscan", "PET Brain",
    "X-ray Cervical Spine", "X-ray Lumbar Spine", "No Imaging Required.",
]
STUDY_NAMES = ["CDP", "VNG", "VNG/VEMP", "VEMP", "Audiogram", "CAE", "MRI Brain w/o @ IHS", "MRI Brain IAC w/o @ IHS",
               "MRI Brain w/wo @ IHS", "MRI Brain w/o @ Scripps", "MRI Brain w/wo @ Scripps", "CT Head w/o @ Sharp",
               "CT Head w/o @ Scripps", "MRI C-spine w/o @ IHS", "MRI L-Spine w/o @ IHS", "EMG/NCV BUE", "EMG/NCV BLE",
               "EEG", "Zeto EEG", "CTA Head", "CTA Head/Neck", "MRA Head", "MRA Neck", "MRI IAC", "MRI Orbits",
               "MRI Pituitary", "Carotid ultrasound", "DaT Scan @ IHS", "PET Brain", "Echo @ Scripps",
               "HST @ Breathe Diagnostics"]
STUDY_RESULTS = ["No acute intracranial abnormality.", "No acute intracranial findings.", "No acute abnormality.",
                 "Normal", "Unremarkable.", "Normal composite score of {score}.",
                 "Vestibular integration deficit, w/ composite score {score}.",
                 "Hearing thresholds WNL. WRS 100% AU.", "No visible inner ear abnormality or retrocochlear pathology.",
                 "Mild chronic microangiopathic changes in central white matter."]
REVIEWED_BY = ["Rev/interp by {pa}/IMP", "Rev/interp by {pa}/MP", "-rev/interpreted {pa}", "-rev/interpreted {pa}/IMP"]
LABS_LINES = ["CBC w/ diff - WNL", "CMP - WNL except", "Lipid panel - WNL", "Hemoglobin A1C: {value}", "TSH {value} WNL",
              "Vit B12 {value} WNL", "Vit D {value}", "Lyme titer - Negative", "({date}) @ Sharp", "({date}) @ Scripps",
              "({date}) @ Labcorp", "({date}) @ Quest", "({date}) per PCP"]

# Encounter time (page 4). The form's own choices for the separate-time line, and the G2211 wording.
TIME_CHOICES = [
    "15 minutes of separate time IR/oculography and/or Epley Chair testing",
    "15 minutes of separate time for Injection Procedures",
    "8 minutes of separate time on removal of keratinous debris/cerumen from the external auditory canals",
    "31 minutes of separate time spent interrogating, programming, and educating patient regarding the Inspire device",
    "31 minutes of separate time spent interrogating, monitoring, and educating patient regarding the Inspire device",
    "31 minutes of separate time spent administering, evaluating, and discussing results of MoCA testing.",
]
LONGITUDINAL = "Longitudinal services were provided to patient in clinic today"
ATTESTATIONS = [
    " I, Ian Purcell, M.D., Ph.D, was present and established the care plan for this patient at this encounter",
    " I, Monali Patel, M.D., was present and established the care plan for this patient at this encounter",
]
SPLIT_SHARED = [
    "I, Ian Purcell, MD, PhD, was present, conducted a PE, and established the care plan for this pt at this encounter for {minutes}mins w/o PA {pa}",
    "***IMP was independently in the room for an additional {minutes} minutes away from {pa} discussing case with patient and performing exam***",
    "***MP was independently in the room for an additional {minutes} minutes away from {pa} discussing case with patient and performing exam***",
    "Dr. Purcell spent {minutes} minutes independently completing PE and discussing case with patient today.",
]
ENCOUNTER_TIMES = ["30", "40", "60"]
