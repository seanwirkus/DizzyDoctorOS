"""Local users, groups and permissions (no patient data)."""
import os
import sqlite3
import tempfile
import unittest

from recordfinder.charting.users import UserStore


class UserStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "users.db")
        self.store = UserStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_bootstrap_admin_is_hashed_and_has_full_access(self):
        user = self.store.authenticate("admin", "change-me-now")
        self.assertIsNotNone(user)
        self.assertIn("Administrators", user["groups"])
        self.assertTrue(self.store.allowed(user, "users.manage"))
        self.assertTrue(user["must_change"])
        self.assertIsNone(self.store.authenticate("admin", "wrong password"))
        con = sqlite3.connect(self.path)
        stored = con.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
        con.close()
        self.assertNotEqual(stored, b"change-me-now")
        self.assertNotIn(b"change-me-now", stored)

    def test_create_user_and_assign_group_permissions(self):
        user = self.store.create_user("jdoe", "Jordan Doe", "temporary-123", ["Front Desk"])
        self.assertTrue(self.store.allowed(user, "schedule.view"))
        self.assertFalse(self.store.allowed(user, "notes.create"))
        changed = self.store.update_user(user["id"], groups=["Providers"], active=True)
        self.assertTrue(self.store.allowed(changed, "notes.file"))
        self.assertIsNotNone(self.store.authenticate("JDOE", "temporary-123"))

    def test_custom_group(self):
        group = self.store.create_group("Auditors", "Read only", ["patients.view", "notes.view"])
        self.assertEqual(group["permissions"], ["notes.view", "patients.view"])
        user = self.store.create_user("audit", "Chart Auditor", "audit-pass-123", ["Auditors"])
        self.assertTrue(self.store.allowed(user, "notes.view"))
        self.assertFalse(self.store.allowed(user, "notes.file"))


if __name__ == "__main__":
    unittest.main()
