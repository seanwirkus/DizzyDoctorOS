import unittest

from recordfinder.charting import note, scope_import


SCOPE_NOTE = """DIZZYDOCTOR SCOPE TRANSFER V1

VISIT TYPE
Progress / Follow-Up

CHIEF COMPLAINT
Dizziness

HISTORY OF PRESENT ILLNESS
Patient reports episodic room-spinning dizziness when rolling over in bed, lasting seconds. Denies hearing loss.

DIZZINESS / VERTIGO
Reports vertigo worse with lying supine, lasting seconds.

HEARING
Patient denies hearing loss.

TINNITUS
Patient reports left non-pulsatile tinnitus.

GAIT / FALLS
No falls.

MEDICATIONS
Meclizine 25 mg PO PRN dizziness (taking)

ALLERGIES
Penicillin - rash

VITALS
BP 128/76. Heart rate 72. Pain 0/10.

NEUROLOGIC EXAM
Mild sway on Romberg.

TESTS / STUDIES
MRI Brain | 09/20/2026 | No acute intracranial abnormality

ASSESSMENT
1) Positional vertigo.

PLAN
Continue vestibular exercises. Return with worsening symptoms.

ORDERS
MRI Brain without contrast 70551
PT: vestibular rehabilitation

REFERRALS
Jane Smith, PT | Physical therapy | Dizziness and imbalance

FOLLOW-UP
Follow up in 3 months.

TIME
Total encounter time 35 minutes.

NEEDS REVIEW
- Tinnitus onset was not clear.
"""


class ScopeParsingTests(unittest.TestCase):
    def test_parses_versioned_sections(self):
        doc = scope_import.parse(SCOPE_NOTE)
        self.assertEqual(doc.sections["visit_type"], "Progress / Follow-Up")
        self.assertIn("room-spinning", doc.sections["hpi"])
        self.assertIn("MRI Brain", doc.sections["studies"])

    def test_rejects_unstructured_text(self):
        with self.assertRaisesRegex(ValueError, "does not look"):
            scope_import.parse("Patient feels dizzy today and will return in a month.")

    def test_import_is_proposals_not_note_mutation(self):
        values = note.blank()
        before = dict(values)
        out = scope_import.import_note(SCOPE_NOTE, values)
        self.assertEqual(values, before)
        self.assertEqual(out["kind"], "scope")
        props = {p["id"]: p for p in out["proposals"]}
        self.assertEqual(props["scope-visit"]["changes"]["neuro-progress"], True)
        self.assertEqual(props["scope-visit"]["changes"]["visit-kind"], "followup")        # the clinic's visit kind, with its source
        self.assertEqual(props["scope-visit"]["changes"]["visit-source"], "scope")
        self.assertEqual(props["scope-visit"]["lines"], ["Follow-up"])
        self.assertEqual(props["scope-cc"]["changes"]["ChiefComplaint"], "Dizziness")
        self.assertEqual(props["scope-hpi"]["changes"]["ChiefComplaint-descr"].split(".")[0],
                         "Patient reports episodic room-spinning dizziness when rolling over in bed, lasting seconds")
        self.assertFalse(props["scope-hpi"]["requires_confirmation"])   # the story goes into the note's story box as written
        self.assertIn("scope-boxes", props)
        box_changes = props["scope-boxes"]["changes"]
        self.assertTrue(box_changes["t-nonpulsatile"])
        self.assertNotIn("t-pulsatile", box_changes)
        self.assertIn("scope-meds", props)
        self.assertTrue(props["scope-meds"]["requires_confirmation"])
        self.assertTrue(props["scope-allergies"]["requires_confirmation"])
        self.assertTrue(props["scope-studies"]["requires_confirmation"])
        self.assertEqual(props["scope-studies"]["changes"]["x-study1-date"], "09/20/2026")
        self.assertEqual(props["scope-orders"]["changes"]["Imaging"], "MRI Brain without contrast 70551")
        self.assertEqual(props["scope-orders"]["changes"]["referral-for"], "vestibular rehabilitation")
        self.assertEqual(props["scope-referrals"]["changes"]["referral1-spec"], "Physical therapy")
        self.assertEqual(props["scope-time"]["changes"]["appt-time"], "35")
        self.assertNotIn("appt-time", props["scope-structured"]["changes"])
        self.assertTrue(any("Tinnitus onset" in x for x in out["unclear"]))

    def test_ambiguous_structured_lines_are_not_guessed(self):
        text = """DIZZYDOCTOR SCOPE TRANSFER V1
CHIEF COMPLAINT: Dizziness
HISTORY OF PRESENT ILLNESS: Dizzy for a week.
TESTS / STUDIES
MRI was okay sometime last year
REFERRALS
Send to therapy
ORDERS
Some kind of brain MRI
ATTESTATION
Attending was around.
"""
        out = scope_import.import_note(text, note.blank())
        props = {p["id"] for p in out["proposals"]}
        self.assertNotIn("scope-studies", props)
        self.assertNotIn("scope-referrals", props)
        self.assertNotIn("scope-orders", props)
        self.assertNotIn("scope-attestation", props)
        self.assertGreaterEqual(len(out["unclear"]), 4)

    def test_a_visit_type_in_the_clinics_words_keeps_its_kind(self):
        text = SCOPE_NOTE.replace("Progress / Follow-Up", "Telehealth follow-up")
        props = {p["id"]: p for p in scope_import.import_note(text, note.blank())["proposals"]}
        self.assertEqual(props["scope-visit"]["changes"]["visit-kind"], "telehealth")
        self.assertTrue(props["scope-visit"]["changes"]["neuro-progress"])      # prints as a progress note
        out = scope_import.import_note(SCOPE_NOTE.replace("Progress / Follow-Up", "House call"), note.blank())
        self.assertNotIn("scope-visit", {p["id"] for p in out["proposals"]})
        self.assertTrue(any("Visit type was not recognized" in x for x in out["unclear"]))


if __name__ == "__main__":
    unittest.main()
