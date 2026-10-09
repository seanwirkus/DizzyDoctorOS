import os
import tempfile
import unittest
from datetime import date

from recordfinder.charting import note
from recordfinder.charting.providers import DemoProvider, prep_reference, snapshot_from_note


class SnapshotTests(unittest.TestCase):
    def test_snapshot_reads_the_latest_note(self):
        v = note.blank()
        v.update({"Name": "Sample, Alice", "DOB": "02/14/1958", "Date": "08/12/2026", "HPI-age": "68", "hpi-female": True, "nkda": True,
                  "neuro-progress": True, "neuro-consult": False, "hist-hypt": True, "hist-migraine": True, "ga-falls-no": True,
                  "meds-descr": "--------PO Medications--------\rAmlodipine 5mg QD\r**I, TY, independently reviewed the medication list 08/12/26**\rNurtec 75mg PRN",
                  "impression-plan-descr": "1) Dizziness and lack of coordination - Monitor clinically.\r2) Headaches with migrainous features - Nurtec PRN.",
                  "x-study1": "CDP", "x-study1-date": "03/02/26", "x-study1-result": "Composite 54.", "next-appt": "3-4", "next-appt-months": True,
                  "imp-mp": True, "Imp/MP": note.FIELDS["Imp/MP"]["options"][1]})
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "SampleAlice 08 12 26.pdf")
            note.fill(v, p)
            s = snapshot_from_note(p)
        self.assertEqual(s["allergies"], "NKDA")
        self.assertEqual([i["dx"] for i in s["impressions"]], ["Dizziness and lack of coordination", "Headaches with migrainous features"])
        self.assertEqual(s["meds"], ["Amlodipine 5mg QD", "Nurtec 75mg PRN"])
        self.assertEqual(s["pmh"], ["Hypertension", "Migraine headaches"])
        self.assertEqual(s["falls"], "no")
        self.assertEqual(s["studies"][0]["name"], "CDP")
        self.assertEqual(s["next"], "3-4 months")
        self.assertEqual(s["attending"], "Monali Patel")
        self.assertEqual(s["sex"], "F")


class DemoProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = DemoProvider(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_today_chart_snapshot(self):
        self.assertEqual([r["last"] for r in self.p.search_patients("samp")], ["Sample"])
        self.assertEqual(len(self.p.search_patients("")), 5)
        rows = self.p.today(date.today())
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["patient"] for r in rows))
        chart = self.p.chart({"last": "Sample", "first": "Alice"})
        self.assertEqual(chart["patient"]["acct"], "123456")
        self.assertGreaterEqual(len(chart["visits"]), 3)
        self.assertTrue(any(d["group"] == "Progress notes" for d in chart["documents"]))
        snap = self.p.snapshot({"id": "1"})
        self.assertEqual(snap["name"], "Sample, Alice")
        self.assertTrue(snap["impressions"])
        notes = self.p.patient_notes({"last": "Tester"})
        self.assertEqual(len(notes), 2)
        self.assertTrue(all(os.path.isfile(n.path) for n in notes))

    def test_range_schedule_has_provider_colors_and_marks_cross_schedule_copies(self):
        start = date(2026, 10, 5)
        schedule = self.p.schedule(start, date(2026, 10, 12))
        self.assertEqual(schedule["start"], "2026-10-05")
        self.assertTrue(all(p["color"].startswith("#") for p in schedule["providers"]))
        duplicate = [r for r in schedule["rows"] if r["duplicate"]]
        self.assertEqual(len(duplicate), 2)
        self.assertTrue(all(r["duplicate_count"] == 2 for r in duplicate))

    def test_zeto_eeg_report_is_an_eeg_on_the_chart_with_its_impression(self):
        from recordfinder import people
        from recordfinder.charting.providers import report_result
        chart = self.p.chart({"last": "Fake", "first": "Dana"})
        eeg = [d for d in chart["documents"] if d["kind"] == "EEG"]
        self.assertEqual(len(eeg), 1)
        self.assertEqual(eeg[0]["group"], "Neuro testing")                 # beside the EMG, VNG and chair reports
        self.assertIn("Zeto Reports", eeg[0]["folder"])
        self.assertEqual(people.kind_of(eeg[0]["path"]), "EEG")              # the drive's folder name decides the kind
        self.assertEqual(people.kind_of(r"V:\Zeto Reports\Fake, Dana Zeto EEG 09-02-26.pdf"), "EEG")
        self.assertEqual(report_result(eeg[0]["path"]), "Normal awake and drowsy EEG. No epileptiform discharges or focal slowing.")
        emg = next(d for d in self.p.chart({"last": "Example", "first": "Carl"})["documents"] if d["kind"] == "EMG/NCV")
        self.assertTrue(report_result(emg["path"]).startswith("Length-dependent sensorimotor axonal polyneuropathy"))
        self.assertEqual(report_result(os.path.join(self.tmp.name, "missing.pdf")), "")

    def test_prep_reference_flags_followups_without_copying_the_visit(self):
        v = note.blank()
        v.update({"Name": "Sample, Alice", "Date": "08/12/2026", "ChiefComplaint": "Dizziness",
                  "impression-plan-descr": "1) Dizziness and lack of coordination - Monitor clinically. RTC with worsening symptoms.",
                  "x-study1": "VNG", "x-study1-date": "08/01/26", "x-study1-result": "Ordered, pending",
                  "next-appt": "3-4", "next-appt-months": True, "nextappt-descr": "or sooner PRN",
                  "referral1-doc": "Audiology", "referral1-spec": "Audiology"})
        prep = prep_reference(v, "")
        self.assertEqual(prep["chief_complaint"], "Dizziness")
        self.assertTrue(any("Dizziness" in f["text"] for f in prep["followups"]))
        self.assertTrue(any(f["source"] == "Study" for f in prep["followups"]))
        self.assertTrue(any(f["source"] == "Referral" for f in prep["followups"]))
        self.assertTrue(any(f["source"] == "Follow-up" for f in prep["followups"]))


if __name__ == "__main__":
    unittest.main()
