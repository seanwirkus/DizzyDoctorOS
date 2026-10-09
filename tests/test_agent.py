"""The agent loop against the made-up demo clinic, with a scripted Claude (no network)."""
import os
import tempfile
import unittest
from types import SimpleNamespace

from recordfinder import ai, demo
from recordfinder.agent import TOOLS, Agent
from recordfinder.index import Store

try:
    import pymupdf  # noqa: F401
except ImportError:  # the demo writes .txt instead of PDFs without it
    pymupdf = None


def tool(id_, name, **inp):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)


class ScriptedClient:
    """Plays back Claude turns; each turn can look at the tool results sent so far."""

    def __init__(self, turns):
        self.turns, self.calls = turns, []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=self.messages)

    def _create(self, **kw):
        self.calls.append(kw)
        blocks = self.turns[len(self.calls) - 1](kw["messages"])
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking", thinking="")] + blocks)


def last_results(messages):
    return {r["tool_use_id"]: r["content"] for r in messages[-1]["content"]}


@unittest.skipIf(pymupdf is None, "pymupdf not installed")
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.join(self.tmp.name, "clinic")
        self.cfg = demo.create(root)
        self.store = Store(os.path.join(self.tmp.name, "index.db"))
        self.store.rebuild_index(self.cfg["search_folders"], [])
        self.item = {"key": "k1", "sender": "Demo Billing", "when": "Mon", "kind": "Medical records",
                     "text": "PLEASE PROVIDE M/R TO APPEAL FOR ACCOUNT 234567 PT BOB C TESTER DOS 09/01/2026.",
                     "name": "Bob C Tester", "first": "Bob", "last": "Tester", "account": "234567", "dos_display": "09/01/2026"}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_full_run(self):
        seen = {}

        def t1(_m):
            return [SimpleNamespace(type="text", text="Looking for the note and EOB."),
                    tool("a", "search_files", last_name="Tester", first_name="Bob", account="234567",
                         dates=["09/01/2026"], keywords=["note", "eob"]),
                    tool("b", "patient_schedule", last_name="Tester", first_name="Bob", dob="", account="234567")]

        def t2(m):
            res = last_results(m)
            seen["search"] = res["a"]
            note_id = next(x["file_id"] for x in __import__("json").loads(res["a"]) if "Progress Note" in x["name"])
            seen["note_id"] = note_id
            return [tool("c", "read_document", file_id=note_id, pages="")]

        def t3(m):
            seen["read"] = last_results(m)["c"]
            return [tool("d", "copy_to_missing_folder", file_ids=[seen["note_id"]], reason="Note for the Aetna appeal"),
                    tool("e", "propose_playbook_rule", section="PAYER RULES", rule="Aetna demo rule: attach the note to E/M appeals.",
                         reason="Seen in this request")]

        def t4(m):
            seen["copy"] = last_results(m)
            return [tool("f", "finish", summary="Note supports 99215; copied for the appeal.",
                         findings=["Progress Note 09-01-2026.pdf p1: 45 minutes, high complexity"],
                         reply_to_billing="M/R for Bob C Tester DOS 09/01/2026 in the Missing Folder. *DEMO",
                         letter="Dear Aetna, ...", status="Done")]

        client = ScriptedClient([t1, t2, t3, t4])
        reader = ai.ClaudeReader("x", client=client, playbook="")
        asked, events = [], []
        pb = os.path.join(self.tmp.name, "playbook.md")
        old = ai.PLAYBOOK_PATH
        ai.PLAYBOOK_PATH = pb
        try:
            res = Agent(reader, self.store, self.cfg, self.item, lambda k, t: events.append((k, t)),
                        lambda k, t: asked.append(k) or True).run()
            playbook = ai.load_playbook()
        finally:
            ai.PLAYBOOK_PATH = old

        self.assertEqual(client.calls[0]["tools"], TOOLS)
        self.assertEqual(client.calls[0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Progress Note 09-01-2026.pdf", seen["search"])
        read_text = " ".join(b.get("text", "") for b in seen["read"])
        self.assertIn("45 minutes", read_text)                          # Claude got the document's text
        self.assertEqual(asked, ["copy", "rule"])                       # both actions needed approval
        self.assertEqual(res.copied, ["Progress Note 09-01-2026.pdf"])
        self.assertTrue(os.path.exists(os.path.join(self.cfg["missing_folder"], "Progress Note 09-01-2026.pdf")))
        self.assertIn("Aetna demo rule", playbook)
        self.assertEqual(res.status, "Done")
        self.assertTrue(res.reply_to_billing.startswith("M/R for Bob"))
        self.assertTrue(any(k == "think" for k, _ in events))
        # assistant turns are sent back whole (thinking included)
        self.assertEqual(client.calls[1]["messages"][1]["content"][0].type, "thinking")

    def test_declined_copy_and_unknown_file(self):
        def t1(_m):
            return [tool("a", "read_document", file_id="F99", pages=""),
                    tool("b", "copy_to_missing_folder", file_ids=["F99"], reason="x")]

        def t2(m):
            res = last_results(m)
            self.assertIn("Unknown file id", res["a"])
            self.assertIn("None of those", res["b"])
            return [SimpleNamespace(type="text", text="Couldn't find it.")]

        reader = ai.ClaudeReader("x", client=ScriptedClient([t1, t2]), playbook="")
        res = Agent(reader, self.store, self.cfg, self.item, lambda k, t: None, lambda k, t: False).run()
        self.assertEqual(res.summary, "Couldn't find it.")
        self.assertEqual(res.copied, [])


if __name__ == "__main__":
    unittest.main()
