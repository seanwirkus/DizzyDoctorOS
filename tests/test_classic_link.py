import os
import tempfile
import time
import unittest

from recordfinder import classic_link


class OfficeWindowLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_nothing_open_means_start_one(self):
        self.assertFalse(classic_link.is_open(self.dir))
        self.assertFalse(classic_link.ask(self.dir, "billing"))
        self.assertIsNone(classic_link.take_request(self.dir))

    def test_an_open_window_gets_the_tab(self):
        classic_link.beat(self.dir)
        self.assertTrue(classic_link.ask(self.dir, "superbills"))
        self.assertEqual(classic_link.take_request(self.dir)["tab"], "superbills")
        self.assertIsNone(classic_link.take_request(self.dir))        # used up

    def test_a_window_still_starting_is_asked_not_started_twice(self):
        classic_link.starting(self.dir)
        self.assertTrue(classic_link.ask(self.dir, "mips"))
        classic_link.beat(self.dir)                                    # it came up: the starting mark goes
        self.assertFalse(os.path.exists(os.path.join(self.dir, classic_link.STARTING)))
        self.assertEqual(classic_link.take_request(self.dir)["tab"], "mips")

    def test_a_closed_or_stale_window_is_not_open(self):
        classic_link.beat(self.dir)
        classic_link.closed(self.dir)
        self.assertFalse(classic_link.is_open(self.dir))
        classic_link.beat(self.dir)
        old = time.time() - classic_link.FRESH - 5
        os.utime(os.path.join(self.dir, classic_link.ALIVE), (old, old))
        self.assertFalse(classic_link.is_open(self.dir))


if __name__ == "__main__":
    unittest.main()
