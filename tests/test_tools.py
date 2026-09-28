import tempfile
import unittest
from pathlib import Path

from harness.agent import REVIEW_REQUEST
from harness.tools import ToolError, Workspace


class SuggestionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for path in [
            "app/models/order_sync.rb",
            "app/models/concerns/order_sync_retries.rb",
            "app/models/concerns/order_sync_states.rb",
            "spec/models/order_sync_spec.rb",
        ]:
            target = Path(self.tmp.name) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# file\n")
        self.ws = Workspace(self.tmp.name, "coder", reviewer=None, ui=None)

    def tearDown(self):
        self.tmp.cleanup()

    def error_for(self, path):
        with self.assertRaises(ToolError) as caught:
            self.ws.read_file(path)
        return str(caught.exception)

    def test_wrong_folder_suggests_the_real_path(self):
        message = self.error_for("app/models/order_sync_retries.rb")
        self.assertIn("Did you mean: app/models/concerns/order_sync_retries.rb?", message)

    def test_bare_file_name_suggests_the_real_path(self):
        self.assertIn("app/models/order_sync.rb", self.error_for("order_sync.rb"))

    def test_typo_suggests_close_match(self):
        self.assertIn("app/models/order_sync.rb", self.error_for("app/models/order_snyc.rb"))

    def test_unrelated_name_points_to_search(self):
        self.assertIn("Use list_files or search", self.error_for("nothing_like_it.py"))

    def test_suggestions_respect_scope(self):
        self.ws.toggle_scope("spec")
        with self.assertRaises(ToolError) as caught:
            self.ws.read_file("spec/order_sync_retries.rb")
        self.assertNotIn("concerns", str(caught.exception))

    def test_replace_in_missing_file_suggests_and_mentions_write_file(self):
        with self.assertRaises(ToolError) as caught:
            self.ws.replace_in_file("order_sync.rb", "a", "b")
        self.assertIn("app/models/order_sync.rb", str(caught.exception))
        self.assertIn("write_file", str(caught.exception))


class SearchByNameTest(SuggestionTest):
    def test_search_finds_files_by_name(self):
        result = self.ws.search("order_sync.rb")
        self.assertIn("Files whose path matches:\napp/models/order_sync.rb", result)

    def test_search_by_name_respects_path(self):
        result = self.ws.search("order_sync", path="spec")
        self.assertIn("spec/models/order_sync_spec.rb", result)
        self.assertNotIn("app/models", result)


class ReviewRequestTest(unittest.TestCase):
    def test_detects_review_requests(self):
        for text in ["Review order_sync.rb", "can you audit the billing code", "find bugs in invoice.rb"]:
            self.assertTrue(REVIEW_REQUEST.search(text), text)

    def test_ignores_other_requests(self):
        for text in ["Add a scope for failed runs", "what does preview_url do", "explain reviewer.rb"]:
            self.assertFalse(REVIEW_REQUEST.search(text), text)


if __name__ == "__main__":
    unittest.main()
