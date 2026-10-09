"""Claude integration tests with a fake API client - no network, made-up data only."""
import json
import os
import re
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

from recordfinder import ai
from recordfinder.parser import parse_requests
from tests.test_parser import CHAT, TODAY


class FakeClient:
    """Stands in for anthropic.Anthropic(); answers every message with one canned request."""

    def __init__(self, stop_reason="end_turn"):
        self.calls = []
        self.stop_reason = stop_reason
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=self.messages)

    def _create(self, **kw):
        self.calls.append(kw)
        first = kw["messages"][0]["content"]
        ids = re.findall(r'<message id="([0-9a-f]+)"', first) if isinstance(first, str) else []
        body = {"messages": [{"id": i, "requests": [{
            "kind": "Medical records", "patient_first": "Alice", "patient_last": "Sample", "account": "0123456",
            "dob": "", "dos": ["09/03/2026"], "codes": ["99205"], "payer": "Aetna",
            "summary": "Records needed for Aetna downcoding appeal.", "missing_info": ["No claim number"],
            "suggested_reply": "M/R for Alice Sample uploaded to the Missing Folder. *FD",
        }] if n == 0 else []} for n, i in enumerate(ids)]}
        text = json.dumps(body) if kw.get("output_config", {}).get("format") else "Dear Aetna, ..."
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])


class ClaudeReaderTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.client = FakeClient()
        self.reader = ai.ClaudeReader("test", self.cache.get, self.cache.__setitem__, ["Front Desk"], "*FD",
                                      client=self.client, playbook="")

    def test_request_shape(self):
        parse_requests(CHAT, today=TODAY, ai=self.reader.extract)
        kw = self.client.calls[0]
        self.assertEqual(kw["model"], ai.FAST_MODEL)                  # chats are read by the cheaper model
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("fallbacks", kw)                               # nothing a HIPAA organization rejects
        self.assertNotIn("betas", kw)
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Front Desk", kw["messages"][0]["content"])

    def test_claude_fields_reach_requests(self):
        reqs = parse_requests(CHAT, today=TODAY, ai=self.reader.extract)
        r = next(r for r in reqs if r.ai_kind)
        self.assertEqual(r.kind, "Medical records")
        self.assertEqual(r.patient.display_name, "Alice Sample")
        self.assertEqual(r.patient.account, "0123456")
        self.assertEqual(r.patient.dos[0].value, date(2026, 9, 3))
        self.assertEqual(r.payers, ["Aetna"])
        self.assertEqual(r.missing, ["No claim number"])
        self.assertTrue(r.suggested_reply.startswith("M/R for Alice Sample"))
        # messages Claude said hold no request produce no rows (chit-chat, our own replies)
        self.assertEqual(len([r for r in reqs if r.ai_kind]), 1)

    def test_repaste_uses_cache_not_api(self):
        parse_requests(CHAT, today=TODAY, ai=self.reader.extract)
        n = len(self.client.calls)
        parse_requests(CHAT, today=TODAY, ai=self.reader.extract)
        self.assertEqual(len(self.client.calls), n)

    def test_refusal_raises(self):
        reader = ai.ClaudeReader("test", client=FakeClient(stop_reason="refusal"), playbook="")
        with self.assertRaises(ai.AIError):
            parse_requests(CHAT, today=TODAY, ai=reader.extract)

    def test_draft(self):
        text = self.reader.draft("Appeal letter", {"text": "Aetna downcoded 99205", "name": "Alice Sample"}, "Visit note text")
        self.assertEqual(text, "Dear Aetna, ...")
        prompt = self.client.calls[-1]["messages"][0]["content"]
        self.assertIn("Visit note text", prompt)
        self.assertNotIn("format", self.client.calls[-1]["output_config"])

    def test_playbook_goes_in_cached_system_prompt(self):
        reader = ai.ClaudeReader("test", client=self.client, playbook="SCMG needs auth for 92550.")
        reader.draft("Explain", {"text": "x"})
        system = self.client.calls[-1]["system"]
        self.assertIn("SCMG needs auth", system[-1]["text"])
        self.assertEqual(system[-1]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("cache_control", system[0])

    def test_ask_conversation_and_attachments(self):
        with tempfile.TemporaryDirectory() as d:
            pdf, txt, png = (os.path.join(d, n) for n in ("EOB.pdf", "note.txt", "card.png"))
            for path, data in ((pdf, b"%PDF-1.4 fake"), (txt, b"Made-up note text"), (png, b"\x89PNG fake")):
                with open(path, "wb") as f:
                    f.write(data)
            item = {"sender": "Billing", "when": "Mon", "text": "Aetna downcoded 99215", "name": "Bob C Tester", "account": "234567"}
            answer, history = self.reader.ask([], "Why was this denied?", item, [pdf, txt, png])
            self.assertEqual(answer, "Dear Aetna, ...")
            first = history[0]["content"]
            self.assertIn("Aetna downcoded 99215", first[0]["text"])            # case details on the first turn
            self.assertEqual(first[1]["type"], "document")
            self.assertEqual(first[1]["source"]["media_type"], "application/pdf")
            self.assertIn("Made-up note text", first[2]["text"])
            self.assertEqual(first[3]["type"], "image")
            self.assertEqual(first[-1]["text"], "Why was this denied?")
            self.assertEqual(history[1]["role"], "assistant")                    # full reply kept, thinking included
            self.assertEqual(history[1]["content"][0].type, "thinking")
            _, history2 = self.reader.ask(history, "Write the appeal.", item)
            second = history2[2]["content"]
            self.assertEqual(len(second), 1)                                     # no repeated case/attachments
            self.assertEqual(len(self.client.calls[-1]["messages"]), 3)

    def test_unsupported_attachment(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.exe")
            with open(p, "wb") as f:
                f.write(b"MZ")
            with self.assertRaises(ai.AIError):
                ai.attachment_block(p)


class DemoTests(unittest.TestCase):
    def test_demo_clinic(self):
        from recordfinder import demo
        with tempfile.TemporaryDirectory() as d:
            settings = demo.create(d)
            self.assertTrue(os.path.isdir(settings["missing_folder"]))
            files = [f for _, _, fs in os.walk(settings["search_folders"][0]) for f in fs]
            self.assertTrue(any("EOB" in f for f in files))
            reqs = parse_requests(demo.CHAT, today=TODAY)
            self.assertEqual(sorted(r.patient.account for r in reqs if r.patient),
                             ["123456", "234567", "345678", "456789"])


class AppModuleTests(unittest.TestCase):
    def test_app_module_loads(self):
        try:
            import tkinter  # noqa: F401
        except ImportError:
            self.skipTest("tkinter not available")
        import recordfinder.app  # catches syntax errors in the UI code, which the other tests don't import


if __name__ == "__main__":
    unittest.main()
