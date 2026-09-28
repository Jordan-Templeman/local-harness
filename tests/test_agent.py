import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import agent as agent_module
from harness.agent import REPEATED_CALL, REPEATED_FAILURE, REVIEW_NO_EDITS, REVIEW_REDO, Agent
from harness.tools import Workspace


class SilentUI:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class Approver:
    def edit(self, path, before, after, diff):
        return True, ""

    def command(self, command):
        return False, ""


def tool_call(name, **args):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}], "stats": {}}


def answer(text):
    return {"content": text, "tool_calls": [], "stats": {}}


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "app/models/run.rb"
        path.parent.mkdir(parents=True)
        path.write_text("class Run\nend\n")
        workspace = Workspace(self.tmp.name, "coder", reviewer=Approver(), ui=SilentUI())
        self.agent = Agent(workspace, "model")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, text, responses):
        with mock.patch.object(agent_module.llm, "chat", side_effect=responses):
            self.agent.ask(text)
        return [m["content"] for m in self.agent.messages if m["role"] == "tool"]

    def test_repeated_read_is_short_circuited(self):
        results = self.run_script("what is Run?", [
            tool_call("read_file", path="app/models/run.rb"),
            tool_call("read_file", path="app/models/run.rb"),
            answer("It is an empty class."),
        ])
        self.assertIn("class Run", results[0])
        self.assertEqual(results[1], REPEATED_CALL)

    def test_reading_again_after_an_edit_is_allowed(self):
        results = self.run_script("rename it", [
            tool_call("read_file", path="app/models/run.rb"),
            tool_call("replace_in_file", path="app/models/run.rb", old_text="class Run", new_text="class Job"),
            tool_call("read_file", path="app/models/run.rb"),
            answer("Renamed."),
        ])
        self.assertIn("class Job", results[2])

    def test_repeating_a_failed_edit_is_refused(self):
        bad_edit = tool_call("replace_in_file", path="app/models/run.rb", old_text="missing", new_text="x")
        results = self.run_script("change it", [bad_edit, tool_call("read_file", path="app/models/run.rb"), bad_edit, answer("Gave up.")])
        self.assertTrue(results[0].startswith("Error:"))
        self.assertEqual(results[2], REPEATED_FAILURE)

    def test_edits_are_blocked_during_a_review(self):
        results = self.run_script("review run.rb", [
            tool_call("replace_in_file", path="app/models/run.rb", old_text="class Run", new_text="class Job"),
            answer("app/models/run.rb:1 `class Run` is empty."),
        ])
        self.assertEqual(results[0], REVIEW_NO_EDITS)
        self.assertIn("class Run", (Path(self.tmp.name) / "app/models/run.rb").read_text())

    def test_review_without_line_references_is_sent_back_once(self):
        self.run_script("review run.rb", [
            answer("Looks great overall. Grade: A."),
            answer("Still fine."),
        ])
        user_messages = [m["content"] for m in self.agent.messages if m["role"] == "user"]
        self.assertEqual(user_messages.count(REVIEW_REDO), 1)

    def test_review_with_line_references_is_accepted(self):
        self.run_script("review run.rb", [
            answer("app/models/run.rb:1 `class Run` has no behaviour; add the methods callers expect."),
        ])
        user_messages = [m["content"] for m in self.agent.messages if m["role"] == "user"]
        self.assertNotIn(REVIEW_REDO, user_messages)


if __name__ == "__main__":
    unittest.main()
