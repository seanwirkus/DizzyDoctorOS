"""The web shell's files agree with each other: every module the rail can open exists, every static
file index.html references is there, and the modules register under the name the shell loads."""
import os
import re
import unittest

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recordfinder", "charting", "web")
ACTIVITIES = ["today", "patients", "notes", "settings", "users", "snapshot", "review", "note", "testing", "billing", "superbill"]


class ShellStaticTests(unittest.TestCase):
    def test_index_references_existing_files(self):
        html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
        for ref in re.findall(r'(?:href|src)="/static/([^"]+)"', html):
            self.assertTrue(os.path.isfile(os.path.join(WEB, ref)), f"index.html references missing /static/{ref}")

    def test_every_activity_module_exists_and_registers(self):
        for name in ACTIVITIES:
            path = os.path.join(WEB, "modules", f"{name}.js")
            self.assertTrue(os.path.isfile(path), f"modules/{name}.js is missing")
            src = open(path, encoding="utf-8").read()
            self.assertRegex(src, rf"DDOS\.register\(\s*['\"]{name}['\"]", f"modules/{name}.js must call DDOS.register('{name}', ...)")
            self.assertIn("mount", src)

    def test_rail_activities_are_known(self):
        html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
        known = set(ACTIVITIES) | {"chart", "superbills", "mips", "requests"}
        for a in re.findall(r'data-activity="([^"]+)"', html):
            self.assertIn(a, known)

    def test_no_leftover_standalone_note_page(self):
        # the old single-page note writer was folded into modules/note.js
        self.assertFalse(os.path.exists(os.path.join(WEB, "app.js")), "web/app.js should be gone (now modules/note.js)")

    def test_document_is_the_simple_default(self):
        shell = open(os.path.join(WEB, "shell.js"), encoding="utf-8").read()
        note = open(os.path.join(WEB, "modules", "note.js"), encoding="utf-8").read()
        # Explorer-style: Home (Today, Patients, Saved notes) in the left pane, one tab per open patient, one Billing button
        self.assertIn('const HOME = [["today", "Calendar", "calendar"], ["patients", "Patients", "users"], ["notes", "Saved notes", "file"]]', shell)
        html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
        self.assertIn('id="tabstrip"', html)
        self.assertIn('id="billingBtn"', html)
        self.assertNotIn("Superbills", html)                       # the office tabs live in the office app
        # the note is the PDF's four pages in one scroll, listed down the left by page
        self.assertIn('["patient", "Patient", 1]', note)
        self.assertIn('["plan", "Plan", 4]', note)
        self.assertIn('function goSection(', note)
        self.assertNotIn("Claude", note)                            # the note is drafted by rules, not by a language model
        self.assertIn('>File note</button>', note)
        # New-visit prep: last-visit reference stays gray/read-only with tick-off follow-ups
        self.assertIn('prep-ref', note + open(os.path.join(WEB, "app.css"), encoding="utf-8").read())
        self.assertIn('prep-visit', note)
        self.assertIn('Click to expand', note)
        self.assertIn('Pull into plan', note)
        # "Pull all" takes this visit's follow-ups; the ones about the next visit are kept apart
        self.assertIn('Pull all into plan', note)
        self.assertIn('function nextVisitItem(', note)
        self.assertIn('"more prep-next"', note)

    def test_imaging_orders_and_drive_reports_land_where_the_provider_already_is(self):
        note = open(os.path.join(WEB, "modules", "note.js"), encoding="utf-8").read()
        testing = open(os.path.join(WEB, "modules", "testing.js"), encoding="utf-8").read()
        snapshot = open(os.path.join(WEB, "modules", "snapshot.js"), encoding="utf-8").read()
        # the PDF's two order lines are searchable, editable combo boxes; a third order goes to the plan notes
        self.assertIn('textField("Imaging", { span: 12, list: "dl-imaging-orders"', note)
        self.assertIn('textField("Imaging-2", { span: 12, list: "dl-imaging-orders"', note)
        self.assertNotIn('selectField("Imaging"', note)
        self.assertIn("S.phrases.IMAGING_ORDERS", note)
        self.assertIn("- Imaging ordered: ", note)
        # dictation's orders and reviewed studies are placed, with the overflow kept
        self.assertIn("for (const name of it.imaging || [])", note)
        self.assertIn("for (const s of it.studies || [])", note)
        self.assertIn('"family-other-descr"', note)
        # Zeto EEG reports show with the other drive reports and are prepped into the study rows
        self.assertIn('"Zeto EEG"', note)
        self.assertIn("async function prefillStudies(", note)
        self.assertIn("/api/report?path=", note)
        self.assertIn('return ["EEG", ""]', testing)
        self.assertIn("Reports on file", snapshot)
        self.assertIn("snap-report", snapshot)

    def test_new_reports_are_flagged_and_pulled_with_one_click(self):
        note = open(os.path.join(WEB, "modules", "note.js"), encoding="utf-8").read()
        testing = open(os.path.join(WEB, "modules", "testing.js"), encoding="utf-8").read()
        css = open(os.path.join(WEB, "app.css"), encoding="utf-8").read() + open(os.path.join(WEB, "modules", "meds.css"), encoding="utf-8").read()
        # the prep card lists reports filed since the last visit, with where each landed or one click to put it there
        self.assertIn("function newSinceLastVisit(", note)
        self.assertIn("function lastVisitDate(", note)
        self.assertIn('"New since last visit"', note)
        self.assertIn("`In study row ${r}`", note)
        self.assertIn("async function addStudy(", note)                 # one click: row + the report's impression
        self.assertIn("added with the report's impression", note)
        self.assertIn("addStudy: (d) => addStudy(d)", note)              # Results / testing can do the same
        # Results / testing: Zeto EEG told apart, a kind filter, the new ones flagged and first
        self.assertIn('"Zeto EEG" : "EEG"', testing)
        self.assertIn("vt-report-chips", testing)
        self.assertIn("NoteWriter.addStudy(d)", testing)
        self.assertIn("New since last visit", testing)
        for cls in (".prep-fresh-row", ".on-file-row.is-new", ".on-file-new", ".vt-report-chips"):
            self.assertIn(cls, css)

    def test_maps_and_saved_notes_have_compact_detail_views(self):
        bodymap = open(os.path.join(WEB, "modules", "bodymap.js"), encoding="utf-8").read()
        snapshot = open(os.path.join(WEB, "modules", "snapshot.js"), encoding="utf-8").read()
        notes = open(os.path.join(WEB, "modules", "notes.js"), encoding="utf-8").read()
        self.assertIn('"myotome"', bodymap)
        self.assertIn("MYOTOME_ACTIONS", bodymap)
        self.assertIn("shoulder abduction", bodymap)
        self.assertIn('"Myotomes"', snapshot)
        self.assertIn('h("details", { class: "card notes-patient"', notes)
        self.assertIn("notes-patient-summary", notes)
        self.assertIn("openPatients", notes)

    def test_visit_types_are_one_set_with_one_colour_everywhere(self):
        note = open(os.path.join(WEB, "modules", "note.js"), encoding="utf-8").read()
        today = open(os.path.join(WEB, "modules", "today.js"), encoding="utf-8").read()
        shell = open(os.path.join(WEB, "shell.js"), encoding="utf-8").read()
        css = "".join(open(os.path.join(WEB, p), encoding="utf-8").read() for p in ("shell.css", "modules/today.css", "modules/meds.css"))
        # the types come from the server (visits.py) through the schema; nothing is hard-coded in the screens
        self.assertIn("S.schema.visit_types", note)
        self.assertNotIn('"#0071e3", "box"', note)
        # the calendar shows the kind in its colour and hands it to the note when a patient is opened
        self.assertIn("visit-kind", today)
        self.assertIn("visit_kind: r.kind", today)
        self.assertIn("kind_color", today)
        # the note keeps the kind and where it came from, shows it in the header and on Build, and lets it be overridden
        self.assertIn("function setVisitKind(", note)
        self.assertIn("function visitPicker(", note)
        self.assertIn("function applyScheduleKind(", note)
        self.assertIn('id="sbVisit"', note)
        self.assertIn('card("Visit type"', note)
        self.assertIn('"override"', note)
        # the tab strip carries the colour
        self.assertIn("tab-kind", shell)
        self.assertIn('"visit" in info', shell)
        for cls in (".visit-kind", ".tab-kind", ".visit-badge", ".chip.kind", ".kind-legend"):
            self.assertIn(cls, css, cls)

    def test_build_warnings_are_settled_once_and_kept_on_the_note(self):
        note = open(os.path.join(WEB, "modules", "note.js"), encoding="utf-8").read()
        css = open(os.path.join(WEB, "modules", "meds.css"), encoding="utf-8").read()
        self.assertIn('S.values["build-resolved"]', note)           # resolutions live in the note's data, not in a Set
        self.assertIn("function unclearList(", note)
        self.assertIn("function warnActions(", note)
        self.assertIn('"Dismiss all"', note)
        self.assertIn("Add to plan notes", note)
        self.assertIn('data-view=\'build\'', note.replace('"', "'"))   # the Build tab wears the open count
        self.assertIn(".warn-group", css)
        # the Scope HPI goes into the story so Build drafts the HPI from it, instead of a card the builder writes over
        self.assertIn('pr.id === "scope-hpi"', note)
        self.assertIn("build-parts", note)                         # the HPI's paragraphs, one tick each
        # Build is the note's home: first tab, demographics beside it, and the complaints it names decide the boxes below
        self.assertIn('const VIEWS = [["build", "Build"], ["patient", "Demographics"], ["note", "Full note"]', note)
        self.assertIn("function hpiCard(", note)
        self.assertIn("function complaintBlocks(", note)
        self.assertIn("Complaints from the HPI", note)
        self.assertIn(".hpi-topic.present", css)

    def test_no_emoji_in_the_screens(self):
        import re
        emoji = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\uFF0B]")
        for root, _dirs, files in os.walk(WEB):
            for fn in files:
                if fn.endswith((".js", ".html", ".css")):
                    with open(os.path.join(root, fn), encoding="utf-8") as f:
                        self.assertIsNone(emoji.search(f.read()), fn)


if __name__ == "__main__":
    unittest.main()
