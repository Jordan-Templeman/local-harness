import json
import re
import threading

from . import llm
from .tools import TOOLS

SYSTEM = """You are a coding assistant working in the user's project at {root}. You talk with the user conversationally and use tools to explore and change their code.

How to work:
- Answer general questions directly. Use tools when you need facts about this project; never guess file contents.
- Explore before editing: use list_files and search to find the relevant code, then read_file the parts you need.
- Search with short, plain text such as "has_many :comments" or "def total". Avoid complex regular expressions. If a search finds nothing, try a shorter or different term before concluding it does not exist.
- When you decide to use a tool, call it right away instead of describing what you will do.
- For small, targeted edits use replace_in_file with text copied exactly from read_file output, without the line numbers.
- For bigger changes to one file use delegate_edit with precise instructions; a specialist coder model writes the change.
- Use write_file for new files.
- After changing code, verify it when you can with run_command (tests, a linter, or running the script) and fix failures.
- The user approves every edit and command. If they reject one, follow their feedback.
- Keep replies short. When a task is done, summarize what changed in a few lines.

{scope}

Project overview:
{overview}"""

ANNOUNCES_ACTION = re.compile(
    r"(\blet me\b|\bi'll\b|\bi will\b|\bi'm going to\b|\bnext,? i\b|\bnow i\b)[^.?!]*[.:]?\s*$",
    re.IGNORECASE,
)

REVIEW_REQUEST = re.compile(r"\b(review|audit|critique|code quality|find (?:bugs|issues|problems))\b", re.IGNORECASE)

REVIEW_GUIDE = """The user is asking for a code review. Do it properly:
1. Find the file the user named (search matches file names) and read it once, in full. Only open another file when a specific finding depends on it.
2. Check, in this order: bugs and wrong behaviour; unhandled edge cases (nil or empty values, retries, concurrency, partial failure); error handling; security (authorization, injection, secrets); database performance (N+1 queries, queries inside loops, missing indexes); risky logic with no tests.
3. Report at most 8 findings, most severe first. For each one give path:line, quote the code, explain the concrete problem (what input or situation breaks, and what goes wrong), and suggest a fix. Missing comments, documentation or style preferences are not findings. Never repeat the same kind of finding for multiple lines.
4. Do not praise the code, give it a grade, or list things that are fine. If you find no real problems after checking everything, say so in one sentence and list what you checked.
5. Do not edit files unless the user asks you to."""

READ_ONLY_TOOLS = {"list_files", "search", "read_file"}

EDIT_TOOLS = {"replace_in_file", "write_file", "delegate_edit"}

FAILURE_PREFIXES = ("Error:", "The user rejected", "The user declined")

REPEATED_FAILURE = (
    "You already made this exact call in this turn and it failed or was rejected. Do not repeat it. "
    "Change your approach, or explain the problem to the user and stop."
)

REVIEW_NO_EDITS = (
    "Edits are disabled during a review because the user asked for a review, not changes. "
    "Finish the review; the user can ask for fixes afterwards."
)

TRIMMED = "[old tool output removed to save space; run the tool again if needed]"

REPEATED_CALL = (
    "You already made this exact call in this turn and its result is above. Do not repeat it. "
    "Use what you have: continue with the next step, or answer the user."
)

LINE_REFERENCE = re.compile(r"[\w/.-]+\.\w+:\d+")

REVIEW_REDO = (
    "That review has no path:line references, so it cannot be checked. If you have not found the file the user "
    "asked about, find it first (search matches file names too). Then read it in full and rewrite the review: "
    "every finding needs path:line, the quoted code, the concrete problem and a fix. Drop generic advice."
)

STACK_MARKERS = {
    "Gemfile": "Ruby (Bundler)",
    "package.json": "JavaScript/Node",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "pom.xml": "Java (Maven)",
    "composer.json": "PHP",
}


def scope_text(workspace):
    if not workspace.restricted:
        return "You have access to the whole project."
    if not workspace.scopes:
        return "The user has not given you access to any files right now. Ask them to grant access to the folders you need."
    return ("You only have access to these folders; tools refuse anything outside them: "
            + ", ".join(workspace.scopes))


def overview(workspace):
    files = workspace.all_files()
    dirs, loose = workspace.dir_counts()
    stack = [label for marker, label in STACK_MARKERS.items() if (workspace.root / marker).exists()]
    lines = [f"{len(files)} files" + (f"; stack: {', '.join(stack)}" if stack else "")]
    lines += [f"  {name}/ ({count} file{"" if count == 1 else "s"})" for name, count in dirs[:40]]
    if loose:
        lines.append(f"  plus {loose} files in the root")
    readme = next((workspace.root / n for n in ("README.md", "README", "readme.md") if (workspace.root / n).is_file()), None)
    if readme and workspace.in_scope(readme.name):
        head = workspace.read(readme).replace("\r\n", "\n").split("\n")[:30]
        lines += ["", f"{readme.name} (first 30 lines):", *head]
    return "\n".join(lines)[:4000]


class Agent:
    def __init__(self, workspace, model, think=False, num_ctx=16384, max_steps=30):
        self.workspace = workspace
        self.model = model
        self.think = think
        self.num_ctx = num_ctx
        self.max_steps = max_steps
        self.stop_event = threading.Event()
        self.reset()

    def reset(self):
        content = SYSTEM.format(
            root=self.workspace.root,
            scope=scope_text(self.workspace),
            overview=overview(self.workspace),
        )
        self.messages = [{"role": "system", "content": content}]

    def scope_changed(self):
        self.messages.append({"role": "system", "content": "Access changed. " + scope_text(self.workspace)})

    def trim(self):
        budget = self.num_ctx * 3
        size = sum(len(m.get("content") or "") for m in self.messages)
        for message in self.messages[1:-6]:
            if size <= budget:
                break
            content = message.get("content") or ""
            if message["role"] == "tool" and len(content) > 200:
                message["content"] = TRIMMED
                size -= len(content)

    def cancel(self):
        self.stop_event.set()

    def ask(self, text):
        ui = self.workspace.ui
        self.stop_event.clear()
        reviewing = bool(REVIEW_REQUEST.search(text))
        if reviewing:
            ui.info("Review mode: findings with line references, no grades.")
            self.messages.append({"role": "system", "content": REVIEW_GUIDE})
        self.messages.append({"role": "user", "content": text})
        nudges = 0
        review_redone = False
        seen = {}
        for _ in range(self.max_steps):
            self.trim()
            try:
                response = llm.chat(self.model, self.messages, tools=TOOLS, think=self.think,
                                    num_ctx=self.num_ctx, on_token=ui.token,
                                    should_stop=self.stop_event.is_set)
            finally:
                ui.end_stream()
            message = {"role": "assistant", "content": response["content"]}
            if response["tool_calls"]:
                message["tool_calls"] = response["tool_calls"]
            self.messages.append(message)
            if not response["tool_calls"]:
                if nudges < 2 and ANNOUNCES_ACTION.search(response["content"][-300:]):
                    nudges += 1
                    self.messages.append({"role": "user", "content": "Go ahead and do that now using the tools."})
                    continue
                if reviewing and not review_redone and not LINE_REFERENCE.search(response["content"]):
                    review_redone = True
                    ui.info("The review had no path:line references; asking for a more specific one.")
                    self.messages.append({"role": "user", "content": REVIEW_REDO})
                    continue
                ui.stats(response["stats"])
                return
            for call in response["tool_calls"]:
                if self.stop_event.is_set():
                    self.messages.append({"role": "tool", "tool_name": "", "content": "Cancelled by the user."})
                    raise llm.Cancelled()
                function = call.get("function") or {}
                name = function.get("name", "")
                args = function.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                ui.tool(name, args)
                key = json.dumps([name, args], sort_keys=True)
                earlier = seen.get(key)
                if reviewing and name in EDIT_TOOLS:
                    result = REVIEW_NO_EDITS
                elif earlier is not None and earlier["content"] != TRIMMED:
                    result = REPEATED_CALL if name in READ_ONLY_TOOLS else REPEATED_FAILURE
                else:
                    result = self.workspace.call(name, args)
                ui.result(result)
                tool_message = {"role": "tool", "tool_name": name, "content": result}
                self.messages.append(tool_message)
                if name in READ_ONLY_TOOLS or result.startswith(FAILURE_PREFIXES):
                    seen.setdefault(key, tool_message)
                else:
                    seen.clear()
        ui.error(f"Stopped after {self.max_steps} steps. Say 'continue' to keep going.")
