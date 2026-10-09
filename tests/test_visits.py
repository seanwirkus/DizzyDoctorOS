"""One set of visit types across the schedule, the note, Build and coding (recordfinder.charting.visits)."""
import unittest
from datetime import date

from recordfinder.charting import coding, compose, note, visits
from recordfinder.charting.providers import DemoProvider, visit_kind


class VisitTypeTests(unittest.TestCase):
    def test_the_set_is_the_clinics_and_every_colour_is_distinct(self):
        ids = [t["id"] for t in visits.VISIT_TYPES]
        for wanted in ("consult", "eval", "followup", "procedure", "results", "telehealth", "urgent", "other"):
            self.assertIn(wanted, ids)
        colours = [t["color"] for t in visits.VISIT_TYPES]
        self.assertEqual(len(colours), len(set(colours)))
        for t in visits.VISIT_TYPES:
            self.assertRegex(t["color"], r"^#[0-9a-f]{6}$")
            self.assertTrue(t["box"] in visits.BOXES or t["box"] == "")
            self.assertIn(t["family"], ("new", "established", ""))

    def test_the_schedules_words_become_a_kind(self):
        self.assertEqual(visits.classify("New Patient"), "consult")
        self.assertEqual(visits.classify("New consult"), "consult")
        self.assertEqual(visits.classify("Follow-Up"), "followup")
        self.assertEqual(visits.classify("Follow-up · Epley chair"), "procedure")
        self.assertEqual(visits.classify("Follow-up · VNG results"), "results")
        self.assertEqual(visits.classify("Follow-up · CDP"), "results")
        self.assertEqual(visits.classify("Telehealth f/u"), "telehealth")
        self.assertEqual(visits.classify("Urgent add-on"), "urgent")
        self.assertEqual(visits.classify("Neurologic evaluation"), "eval")
        self.assertEqual(visits.classify("", "VNG_VEMP"), "results")     # a testing calendar is a results visit
        self.assertEqual(visits.classify(""), "")                        # nothing said: the note's boxes decide
        self.assertEqual(visits.classify("ABN Form"), "")

    def test_a_kind_ticks_its_form_box_and_reads_back(self):
        v = note.blank()
        v.update(visits.changes_for("procedure", "schedule"))
        self.assertTrue(v["neuro-progress"])
        self.assertFalse(v["neuro-consult"])
        self.assertEqual((v["visit-kind"], v["visit-source"]), ("procedure", "schedule"))
        self.assertEqual(visits.kind_of(v), ("procedure", "schedule"))
        self.assertEqual(visits.family(v), "established")
        # a filed note knows only its boxes: the kind comes back from them
        self.assertEqual(visits.kind_of({"neuro-consult": True}), ("consult", "note"))
        self.assertEqual(visits.kind_of({"neuro-progress": True, "visit-kind": "procedure", "visit-source": "override"}), ("procedure", "override"))
        # a kind whose box was unticked by hand is no longer the kind
        self.assertEqual(visits.kind_of({"neuro-eval": True, "visit-kind": "procedure"}), ("eval", "note"))
        self.assertEqual(visits.kind_of({}), ("", ""))
        with self.assertRaises(ValueError):
            visits.changes_for("house call")
        self.assertEqual(visits.changes_for("other"), {"visit-kind": "other", "visit-source": "override"})

    def test_an_unknown_source_is_an_override(self):
        self.assertEqual(visits.changes_for("followup", "guess")["visit-source"], "override")

    def test_schedule_rows_carry_the_kind_and_its_colour(self):
        row = visit_kind({"patient": True, "visit": "Follow-up · Epley chair", "cal": "TY"})
        self.assertEqual((row["kind"], row["kind_label"], row["kind_color"]), ("procedure", "Procedure", visits.BY_ID["procedure"]["color"]))
        block = visit_kind({"patient": False, "visit": "New Patient", "cal": "TY"})
        self.assertEqual(block["kind"], "")
        demo = DemoProvider().schedule(date(2026, 10, 5), date(2026, 10, 12))
        kinds = {r["name"]: r["kind"] for r in demo["rows"]}
        self.assertEqual(kinds["Tester, Bob"], "procedure")
        self.assertEqual(kinds["Example, Carl"], "consult")
        self.assertEqual(kinds["Fake, Dana"], "results")
        self.assertEqual(demo["visit_types"], visits.VISIT_TYPES)
        self.assertTrue(all(r["kind"] for r in DemoProvider().today(date.today())))

    def test_coding_follows_the_kind(self):
        v = note.blank()
        v.update({"appt-time": "40", "impression-plan-descr": "1) Dizziness - Monitor."})
        v.update(visits.changes_for("telehealth", "schedule"))
        c = coding.code_note(v)
        self.assertEqual((c.visit, c.kind, c.em.code), ("established", "telehealth", "99215"))
        self.assertTrue(any("modifier 95" in w for w in c.warnings))
        v.update(visits.changes_for("procedure", "override"))
        c = coding.code_note(v)
        self.assertTrue(any("Scheduled as a procedure visit" in w for w in c.warnings))
        v["time-proc1"], v["addnl-time"] = True, "Additionally, I spent up to 15 minutes performing IR/oculography."
        c = coding.code_note(v)
        self.assertFalse(any("Scheduled as a procedure visit" in w for w in c.warnings))
        v.update(visits.changes_for("consult", "override"))
        c = coding.code_note(v)
        self.assertEqual((c.visit, c.em.code), ("new", "99203"))
        self.assertEqual(c.as_dict()["kind"], "consult")

    def test_the_hpi_opening_follows_the_kind_chosen_over_a_carried_opening(self):
        prev = note.blank()
        prev.update({"Name": "Example, Carl", "DOB": "11/20/1949", "Date": "08/11/2026", "hpi-male": True, "ChiefComplaint": "Imbalance",
                     "ChiefComplaint-descr": "Mr. Example is a 76 y/o RHD male presenting to the clinic today for a consultation on imbalance."})
        v = note.carry_forward(prev, date(2026, 10, 8))
        self.assertIn("a follow-up on imbalance. Last seen on 08/11/2026.", v["ChiefComplaint-descr"])
        v.update(visits.changes_for("consult", "schedule"))          # booked as a new consult after all
        hpi = {p.id: p for p in compose.compose_note(v, last_seen="08/11/2026").proposals}["hpi"].text
        self.assertTrue(hpi.startswith("Mr. Example is a 76 y/o RHD male presenting to the clinic today for a consultation on imbalance."), hpi)
        self.assertNotIn("Last seen on", hpi)
        v.update(visits.changes_for("results", "schedule"))          # a results visit is a follow-up on the form
        v["ChiefComplaint-descr"] = hpi
        hpi = {p.id: p for p in compose.compose_note(v, last_seen="08/11/2026").proposals}["hpi"].text
        self.assertTrue(hpi.startswith("Mr. Example is a 76 y/o RHD male presenting to the clinic today for a follow-up on imbalance. Last seen on 08/11/2026."), hpi)


if __name__ == "__main__":
    unittest.main()
