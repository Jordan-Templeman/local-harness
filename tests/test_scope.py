import tempfile
import unittest
from pathlib import Path

from harness.tools import ToolError, Workspace


def make_project(root):
    for path in ["lib/invoice.rb", "lib/deep/x.rb", "lib/deep/y.rb", "lib/other/z.rb", "spec/a_spec.rb", "README.md"]:
        target = Path(root) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {path}\n")


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_project(self.tmp.name)
        self.ws = Workspace(self.tmp.name, "coder", reviewer=None, ui=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unrestricted_by_default(self):
        self.assertFalse(self.ws.restricted)
        self.assertIn("README.md", self.ws.read_file("README.md"))

    def test_checking_a_folder_restricts_to_it(self):
        self.ws.toggle_scope("lib")
        self.assertEqual(self.ws.scopes, ["lib"])
        self.assertIn("invoice", self.ws.read_file("lib/invoice.rb"))
        with self.assertRaises(ToolError):
            self.ws.read_file("spec/a_spec.rb")

    def test_unchecking_last_folder_leaves_no_access(self):
        self.ws.toggle_scope("lib")
        self.ws.toggle_scope("lib")
        self.assertEqual(self.ws.scopes, [])
        with self.assertRaises(ToolError):
            self.ws.read_file("lib/invoice.rb")
        with self.assertRaises(ToolError):
            self.ws.search("invoice")

    def test_unchecking_a_subfolder_keeps_its_siblings_and_loose_files(self):
        self.ws.toggle_scope("lib")
        self.ws.toggle_scope("lib/deep")
        self.assertEqual(self.ws.scopes, ["lib/invoice.rb", "lib/other"])
        with self.assertRaises(ToolError):
            self.ws.read_file("lib/deep/x.rb")
        self.assertIn("invoice", self.ws.read_file("lib/invoice.rb"))

    def test_unchecking_a_nested_file_splits_each_level(self):
        self.ws.toggle_scope("lib")
        self.ws.toggle_scope("lib/deep/x.rb")
        self.assertEqual(self.ws.scopes, ["lib/deep/y.rb", "lib/invoice.rb", "lib/other"])

    def test_checking_a_parent_absorbs_children(self):
        self.ws.toggle_scope("lib/deep")
        self.ws.toggle_scope("lib/other")
        self.ws.toggle_scope("lib")
        self.assertEqual(self.ws.scopes, ["lib"])

    def test_clear_scope_restores_whole_project(self):
        self.ws.toggle_scope("lib")
        self.ws.clear_scope()
        self.assertFalse(self.ws.restricted)
        self.assertIn("spec", self.ws.read_file("spec/a_spec.rb"))

    def test_search_only_covers_scoped_folders(self):
        self.ws.toggle_scope("spec")
        result = self.ws.search("#")
        self.assertIn("spec/a_spec.rb", result)
        self.assertNotIn("lib/", result)

    def test_paths_outside_the_project_are_refused(self):
        with self.assertRaises(ToolError):
            self.ws.read_file("../outside.txt")


if __name__ == "__main__":
    unittest.main()
