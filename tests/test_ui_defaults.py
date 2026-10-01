import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

import sweptpc  # noqa: E402


class UiDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with mock.patch.object(sweptpc.SweptPC, "_start_update_check", lambda self: None):
            self.window = sweptpc.SweptPC()
        self.addCleanup(self.window.close)

    def test_recycle_bin_is_opt_in_and_everything_else_starts_ticked(self):
        self.assertNotIn("recycle_bin", self.window.selected_keys)
        self.assertFalse(self.window.cards["recycle_bin"].checkbox.isChecked())
        others = set(sweptpc.CLEANUP_TARGETS) - {"recycle_bin"}
        self.assertEqual(others, self.window.selected_keys)
        for key in others:
            self.assertTrue(self.window.cards[key].checkbox.isChecked(), key)

    def test_ticking_recycle_bin_selects_it(self):
        self.window.cards["recycle_bin"].checkbox.setChecked(True)
        self.assertIn("recycle_bin", self.window.selected_keys)

    def test_footer_reads_free_and_portable_without_html_entity(self):
        footers = [w for w in self.window.findChildren(QLabel) if "Portable" in w.text()]
        self.assertEqual(1, len(footers))
        footer = footers[0]
        self.assertIn("Free & Portable", footer.text())
        self.assertNotIn("&amp;", footer.text())
        self.assertNotEqual(Qt.TextFormat.RichText, footer.textFormat())


if __name__ == "__main__":
    unittest.main()
