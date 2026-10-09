import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from recordfinder import source_update


def git(path, *args):
    return subprocess.run(["git", "-C", path, *args], check=True, capture_output=True, text=True)


class SourceUpdateTests(unittest.TestCase):
    def test_dirty_checkout_is_never_changed(self):
        with tempfile.TemporaryDirectory() as root:
            git(root, "init", "-q")
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test")
            path = os.path.join(root, "app.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("one")
            git(root, "add", "app.txt")
            git(root, "commit", "-qm", "one")
            with open(path, "w", encoding="utf-8") as f:
                f.write("local")
            message, changed = source_update.update(root)
            self.assertFalse(changed)
            self.assertIn("local code changes", message)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "local")

    def test_installed_copy_does_not_run_git(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            message, changed = source_update.update("missing")
        self.assertFalse(changed)
        self.assertIn("installed build", message)


if __name__ == "__main__":
    unittest.main()
