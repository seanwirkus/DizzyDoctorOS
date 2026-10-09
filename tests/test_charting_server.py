"""Authenticated local chart server routes."""
import json
import os
import tempfile
import unittest
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from recordfinder.charting.providers import DemoProvider
from recordfinder.charting.server import ChartContext, ChartServer


class ChartServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.provider = DemoProvider(os.path.join(self.tmp.name, "clinic"))
        cfg = {"notes_folders": [self.provider.notes_dir]}
        self.ctx = ChartContext(cfg, self.tmp.name, provider=self.provider)
        self.server = ChartServer(self.ctx).start()
        self.base = f"http://127.0.0.1:{self.server.port}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.opener.open(self.base + "/?" + urlencode({"t": self.ctx.token})).read()

    def tearDown(self):
        self.server.stop()
        self.ctx.users.close()
        self.tmp.cleanup()

    def get(self, path):
        return json.loads(self.opener.open(self.base + path).read())

    def post(self, path, data):
        req = Request(self.base + path, json.dumps(data).encode(), {"Content-Type": "application/json"}, method="POST")
        return json.loads(self.opener.open(req).read())

    def test_login_schedule_users_and_read_only_history(self):
        with self.assertRaises(HTTPError) as denied:
            self.get("/api/status")
        self.assertEqual(denied.exception.code, 401)
        login = self.post("/api/auth/login", {"username": "admin", "password": "change-me-now"})
        self.assertTrue(login["user"]["must_change"])
        self.assertIn("*", login["user"]["permissions"])

        calendar = self.get("/api/schedule?start=2026-10-05&end=2026-10-12")
        self.assertEqual(calendar["start"], "2026-10-05")
        self.assertEqual(calendar["end"], "2026-10-12")
        self.assertTrue(calendar["providers"])
        self.assertTrue(any(r["duplicate"] for r in calendar["rows"]))
        # every booking carries its visit type and colour; the schema carries the set the screens draw from
        self.assertTrue(all(r["kind"] and r["kind_color"] for r in calendar["rows"] if r["patient"]))
        schema = self.get("/api/schema")
        self.assertEqual([t["id"] for t in schema["visit_types"]][:3], ["consult", "eval", "followup"])
        self.assertIn("schedule", schema["visit_sources"])

        state = self.get("/api/users")
        self.assertEqual(state["users"][0]["username"], "admin")
        note_path = self.provider.patient_notes({"id": "1"})[0].path
        opened = self.get("/api/note?" + urlencode({"path": note_path}))
        self.assertTrue(opened["read_only"])

    def test_group_permissions_are_enforced_by_the_server(self):
        self.ctx.users.create_user("front", "Front Desk", "front-desk-123", ["Front Desk"], must_change=False)
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        opener.open(self.base + "/?" + urlencode({"t": self.ctx.token})).read()
        req = Request(self.base + "/api/auth/login", json.dumps({"username": "front", "password": "front-desk-123"}).encode(),
                      {"Content-Type": "application/json"}, method="POST")
        opener.open(req).read()
        schedule = json.loads(opener.open(self.base + "/api/schedule?start=2026-10-05&end=2026-10-06").read())
        self.assertTrue(schedule["rows"])
        with self.assertRaises(HTTPError) as denied:
            opener.open(self.base + "/api/users").read()
        self.assertEqual(denied.exception.code, 403)

    def test_new_from_returns_gray_reference_prep(self):
        self.post("/api/auth/login", {"username": "admin", "password": "change-me-now"})
        note_path = self.provider.patient_notes({"id": "1"})[0].path
        prepped = self.post("/api/new-from", {"path": note_path, "dos": "10/06/2026"})
        self.assertIn("values", prepped)
        self.assertIn("prep", prepped)
        self.assertTrue(prepped["prep"]["followups"])
        self.assertTrue(prepped["prep"]["impressions"])
        # A patient with no prior note gets a blank note and no reference card.
        blank = self.post("/api/new-from", {"dos": "10/06/2026", "Name": "Nobody, New"})
        self.assertIsNone(blank["prep"])

    def test_report_result_and_imaging_orders_are_served(self):
        self.post("/api/auth/login", {"username": "admin", "password": "change-me-now"})
        eeg = next(d for d in self.provider.chart({"id": "4"})["documents"] if d["kind"] == "EEG")
        r = self.get("/api/report?" + urlencode({"path": eeg["path"]}))
        self.assertTrue(r["result"].startswith("Normal awake and drowsy EEG"))
        with self.assertRaises(HTTPError) as missing:
            self.get("/api/report?" + urlencode({"path": os.path.join(self.tmp.name, "nope.pdf")}))
        self.assertEqual(missing.exception.code, 404)
        phrases = self.get("/api/phrases")
        self.assertIn("MRI Brain with and w/o contrast 70553", phrases["IMAGING_ORDERS"])
        self.assertIn("Zeto EEG", phrases["STUDY_NAMES"])
        it = self.post("/api/interpret", {"text": "order an MRI brain with and without", "section": "plan"})
        self.assertEqual(it["imaging"], ["MRI Brain with and w/o contrast 70553"])

    def test_print_results_are_logged_once_per_attempt(self):
        self.post("/api/auth/login", {"username": "admin", "password": "change-me-now"})
        base = {"attempt": "print-1", "name": "Sample, Alice", "dos": "10/06/2026", "document": "progress note"}
        self.post("/api/print-log", {**base, "status": "requested"})
        self.post("/api/print-log", {**base, "status": "printed"})
        rows = self.get("/api/print-log?" + urlencode({"name": "Sample, Alice", "dos": "10/06/2026"}))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "printed")
        self.assertEqual(rows[0]["user"], "Administrator")

    def test_calendar_can_create_day_provider_superbill_batches(self):
        self.post("/api/auth/login", {"username": "admin", "password": "change-me-now"})
        schedule = self.get("/api/schedule?start=2026-10-05&end=2026-10-12")
        patient = next(r for r in schedule["rows"] if r["patient"])
        result = self.post("/api/superbill/batch", {"date": patient["date"], "providers": [patient["cal"]],
                                                    "rows": [{"name": patient["name"], "dob": patient["dob"], "insurance": "Aetna"}]})
        self.assertTrue(result["batches"])
        self.assertGreaterEqual(result["appointments"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertTrue(os.path.isfile(result["batches"][0]["path"]))
        self.assertTrue(all(item["path"].endswith(" SB.pdf") for item in result["batches"][0]["items"]))


if __name__ == "__main__":
    unittest.main()
