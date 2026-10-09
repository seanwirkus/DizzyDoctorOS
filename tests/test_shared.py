"""Clinic-wide install: publishing the main PC's data, loading it on another PC, and spotting a newer build."""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

from recordfinder import shared
from recordfinder.index import Store
from recordfinder.version import VERSION, parse


def _fill(db_path, files, items=(), meta=()):
    s = Store(db_path)
    with s._lock:
        s.db.executemany("INSERT INTO files VALUES(?,?,?,?,?)", [(p, os.path.basename(p), "V:\\", 1, 1.0) for p in files])
        s.db.executemany("INSERT INTO items(key, first_seen, data) VALUES(?,?,?)", [(k, "2026-09-30", "{}") for k in items])
        s.db.executemany("INSERT OR REPLACE INTO meta VALUES(?,?)", list(meta))
        s.db.commit()
    s.close()


def _rows(db_path, sql):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


class SharedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.main = os.path.join(self.tmp.name, "main", "index.db")
        self.client = os.path.join(self.tmp.name, "client", "index.db")
        self.share = os.path.join(self.tmp.name, "share")
        os.makedirs(self.share)
        _fill(self.main, ["V:\\a.pdf", "V:\\b.pdf"], items=["main's request"],
              meta=[("indexed_at", "2026-09-30T12:00:00"), ("ai_spend:2026-09-30", "1.50"), ("sb_stop", "0")])
        self.cfg = {"share_folder": self.share}

    def tearDown(self):
        self.tmp.cleanup()

    def test_roles(self):
        self.assertTrue(shared.is_client(self.cfg))
        self.assertFalse(shared.is_main(self.cfg))
        self.assertTrue(shared.is_main(dict(self.cfg, publish_data=True)))
        self.assertFalse(shared.is_client({}))

    def test_publish_leaves_out_this_pcs_own_work_and_keeps_two(self):
        info = shared.publish(self.main, self.share)
        pub = os.path.join(self.share, "data", info["file"])
        self.assertTrue(os.path.isfile(pub))
        self.assertEqual(shared.latest(self.share)["file"], info["file"])
        self.assertEqual(_rows(pub, "SELECT COUNT(*) FROM files")[0][0], 2)
        self.assertEqual(_rows(pub, "SELECT COUNT(*) FROM items")[0][0], 0)          # billing requests stay on their PC
        self.assertEqual(sorted(k for (k,) in _rows(pub, "SELECT k FROM meta")), ["indexed_at"])
        self.assertEqual(info["counts"]["files"], 2)
        for _ in range(2):
            time.sleep(0.01)
            last = shared.publish(self.main, self.share)
        left = sorted(f for f in os.listdir(os.path.join(self.share, "data")) if f.startswith("index-"))
        self.assertEqual(len(left), 2)
        self.assertEqual(left[-1], last["file"])
        self.assertEqual(shared.latest(self.share)["file"], last["file"])

    def test_fingerprint_ignores_local_settings(self):
        con = sqlite3.connect(self.main)
        fp = shared.fingerprint(con)
        con.execute("INSERT OR REPLACE INTO meta VALUES('ai_spend:2026-10-01', '3.00')")
        con.execute("INSERT OR REPLACE INTO meta VALUES('shared_published', '{}')")
        self.assertEqual(shared.fingerprint(con), fp)
        con.execute("INSERT OR REPLACE INTO meta VALUES('outlook_synced_at', '2026-09-30T13:00:00')")
        self.assertNotEqual(shared.fingerprint(con), fp)
        con.close()

    def test_pull_loads_the_data_and_keeps_local_work(self):
        _fill(self.client, ["V:\\stale.pdf"], items=["my request"], meta=[("ai_spend:2026-09-30", "0.25")])
        info = shared.publish(self.main, self.share)
        msg = shared.pull(self.cfg, self.client)
        self.assertTrue(msg.startswith("Clinic data from"), msg)
        self.assertEqual(sorted(p for (p,) in _rows(self.client, "SELECT path FROM files")), ["V:\\a.pdf", "V:\\b.pdf"])
        self.assertEqual(_rows(self.client, "SELECT key FROM items"), [("my request",)])
        self.assertEqual(_rows(self.client, "SELECT v FROM meta WHERE k = 'ai_spend:2026-09-30'"), [("0.25",)])
        self.assertEqual(shared.marker(self.client)["stamp"], info["stamp"])
        self.assertFalse([f for f in os.listdir(os.path.dirname(self.client)) if ".download" in f or f.endswith(".prev")])
        again = shared.pull(self.cfg, self.client)                       # already has it: nothing copied
        self.assertTrue(again.startswith("Clinic data from"), again)
        Store(self.client).close()                                       # the app opens the loaded index fine

    def test_first_pull_on_a_new_pc(self):
        shared.publish(self.main, self.share)
        seen = []
        shared.pull(self.cfg, self.client, progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(_rows(self.client, "SELECT COUNT(*) FROM files")[0][0], 2)
        self.assertTrue(seen and seen[-1][0] == seen[-1][1])

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows file locking")
    def test_pull_waits_while_the_index_is_open(self):
        _fill(self.client, ["V:\\stale.pdf"])
        shared.publish(self.main, self.share)
        con = sqlite3.connect(self.client)
        con.execute("SELECT COUNT(*) FROM files").fetchall()
        try:
            msg = shared.pull(self.cfg, self.client)
        finally:
            con.close()
        self.assertIn("another window", msg)
        self.assertEqual(_rows(self.client, "SELECT path FROM files"), [("V:\\stale.pdf",)])   # untouched
        self.assertEqual(len([f for f in os.listdir(os.path.dirname(self.client)) if f.endswith(".download")]), 1)
        self.assertTrue(shared.pull(self.cfg, self.client).startswith("Clinic data from"))     # the copy is reused
        self.assertEqual(_rows(self.client, "SELECT COUNT(*) FROM files")[0][0], 2)

    def test_prefetch_copies_newer_data_ahead(self):
        shared.publish(self.main, self.share)
        shared.pull(self.cfg, self.client)
        self.assertIsNone(shared.prefetch(self.cfg, self.client))
        time.sleep(0.01)
        newer = shared.publish(self.main, self.share)
        got = shared.prefetch(self.cfg, self.client)
        self.assertEqual(got["stamp"], newer["stamp"])
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(self.client), newer["file"] + ".download")))

    @unittest.skipUnless(sys.platform.startswith("win"), "the server publishes the Windows .exe only")
    def test_newer_build(self):
        local = os.path.join(self.tmp.name, "pc", "DizzyDoctorOS.exe")
        os.makedirs(os.path.dirname(local))
        with open(local, "wb") as f:
            f.write(b"old")
        with open(os.path.join(self.share, "DizzyDoctorOS.exe"), "wb") as f:
            f.write(b"new build")
        sha = hashlib.sha256(b"new build").hexdigest()
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", local):
            with open(os.path.join(self.share, "version.json"), "w") as f:
                json.dump({"version": VERSION, "sha256": sha}, f)
            self.assertIsNone(shared.newer_build(self.cfg))                   # same version: nothing to do
            with open(os.path.join(self.share, "version.json"), "w") as f:
                json.dump({"version": "99.0.0", "sha256": sha}, f)
            info = shared.newer_build(self.cfg)
            self.assertEqual(info["version"], "99.0.0")
            script = shared.stage_update(info)
            try:
                with open(local + ".new", "rb") as f:
                    self.assertEqual(f.read(), b"new build")
                with open(script) as f:
                    self.assertIn(f'move /y "{local}.new" "{local}"', f.read())
            finally:
                os.remove(script)
        self.assertIsNone(shared.newer_build(self.cfg))                       # running from source: never

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows process list")
    def test_others_running(self):
        self.assertEqual(shared.others_running(os.path.join(self.tmp.name, "NotRunning.exe")), 0)
        self.assertEqual(shared.others_running(sys.executable), shared.others_running(sys.executable))  # doesn't raise

    def test_updater_script_never_loops(self):
        local = os.path.join(self.tmp.name, "pc", "DizzyDoctorOS.exe")
        os.makedirs(os.path.dirname(local))
        with open(os.path.join(self.share, "DizzyDoctorOS.exe"), "wb") as f:
            f.write(b"x")
        with mock.patch.object(sys, "executable", local):
            script = shared.stage_update({"path": os.path.join(self.share, "DizzyDoctorOS.exe")})
        try:
            with open(script) as f:
                text = f.read()
            self.assertIn(f'start "" "{local}" --no-update', text)       # gives up without another update attempt
        finally:
            os.remove(script)

    def test_fresh_env_starts_a_separate_program(self):
        env = shared.fresh_env()
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertNotEqual(os.environ.get("PYINSTALLER_RESET_ENVIRONMENT"), "1")     # this process untouched

    @unittest.skipUnless(sys.platform.startswith("win"), "a Mac app runs the mapper from its own bundle")
    def test_mapper_runs_from_its_own_copy(self):
        from recordfinder import config
        exe = os.path.join(self.tmp.name, "pc", "DizzyDoctorOS.exe")
        os.makedirs(os.path.dirname(exe))
        with open(exe, "wb") as f:
            f.write(b"v1")
        with mock.patch.object(sys, "executable", exe), mock.patch.object(config, "DATA_DIR", os.path.join(self.tmp.name, "data")):
            first = shared.mapper_exe()
            self.assertNotEqual(first, exe)
            with open(first, "rb") as f:
                self.assertEqual(f.read(), b"v1")
            before = os.path.getmtime(first)
            self.assertEqual(shared.mapper_exe(), first)                    # unchanged program: reused as is
            self.assertEqual(os.path.getmtime(first), before)
            time.sleep(0.05)
            with open(exe, "wb") as f:
                f.write(b"v2 new")                                          # after an update: copied again
            with open(shared.mapper_exe(), "rb") as f:
                self.assertEqual(f.read(), b"v2 new")

    def test_version_order(self):
        self.assertGreater(parse("1.6.0"), parse("1.5.10"))
        self.assertGreater(parse("1.10.0"), parse("1.9.9"))
        self.assertLess(parse("bad"), parse("0.0.1"))


if __name__ == "__main__":
    unittest.main()
