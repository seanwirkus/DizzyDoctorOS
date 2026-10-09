"""Outlook cache sync against a fake Outlook (made-up bookings): a stopped full read keeps the old copy."""
import os
import tempfile
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from recordfinder import outlook_cache, schedule
from recordfinder.index import Store


class FakeTable:
    def __init__(self, rows, on_block=None):
        self.rows, self.on_block, self.pos = rows, on_block, 0
        self.Columns = SimpleNamespace(RemoveAll=lambda: None, Add=lambda c: None)

    @property
    def EndOfTable(self):
        return self.pos >= len(self.rows)

    def GetArray(self, n):
        block = self.rows[self.pos:self.pos + n]
        self.pos += n
        if self.on_block:
            self.on_block()
        return block


def booking(k, subject):
    d = datetime(2026, 9, 1 + k % 28, 9, 0)
    return (f"new{k}", subject, d, d, "", d)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "index.db"))
        outlook_cache.ensure_schema(self.store)
        old = [(f"old{k}", "EC", "2025-01-02T09:00", "2025-01-02T09:30", f"Old, Patient{k} DOB: 01/02/1950", "", "OLD",
                f"PATIENT{k}", "", "1950-01-02", "", "", "", "2025-01-01T00:00:00") for k in range(3)]
        outlook_cache._flush(self.store, old)
        self.rows = [booking(k, f"Sample, Alice{k} DOB: 01/02/1990") for k in range(1200)]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def count(self):
        return self.store.reader().execute("SELECT COUNT(*) FROM appts").fetchone()[0]

    def run_sync(self, cancel=None, on_block=None):
        folder = SimpleNamespace(GetTable=lambda flt: FakeTable(self.rows, on_block))
        with mock.patch.object(schedule, "_outlook", lambda: object()), \
             mock.patch.object(schedule, "calendar_folders", lambda ol: {"EC": folder}):
            return outlook_cache.sync(self.store, full=True, cancel=cancel)

    def test_stopped_full_read_keeps_old_copy(self):
        stop = threading.Event()
        self.assertEqual(self.run_sync(cancel=stop, on_block=stop.set), -1)  # the app closed mid-read
        self.assertEqual(self.count(), 3)                                    # old copy untouched

    def test_full_read_replaces_copy(self):
        self.assertEqual(self.run_sync(), 1200)
        self.assertEqual(self.count(), 1200)
        tables = {r[0] for r in self.store.reader().execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
        self.assertNotIn("appts_new", tables)
        self.assertTrue({"appts_last", "appts_start", "appts_dob"} <= tables)  # indexes rebuilt on the new table
        self.assertEqual(len(outlook_cache.find_patient(self.store, "Sample", "Alice")), 1200)
        self.assertEqual(outlook_cache.find_patient(self.store, "Old", "Patient1"), [])  # the old copy is gone


if __name__ == "__main__":
    unittest.main()
