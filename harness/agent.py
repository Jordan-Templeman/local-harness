import json
import re

from . import llm
from .tools import TOOLS
from .ui import ui

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
    if not workspace.scopes:
        return "You have access to the whole project."
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
                message["content"] = "[old tool output removed to save space; run the tool again if needed]"
                size -= len(content)

    def ask(self, text):
        self.messages.append({"role": "user", "content": text})
        nudges = 0
        for _ in range(self.max_steps):
            self.trim()
            try:
                response = llm.chat(self.model, self.messages, tools=TOOLS, think=self.think,
                                    num_ctx=self.num_ctx, on_token=ui.token)
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
                ui.stats(response["stats"])
                return
            for call in response["tool_calls"]:
                function = call.get("function") or {}
                name = function.get("name", "")
                args = function.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                ui.tool(name, args)
                result = self.workspace.call(name, args)
                ui.result(result)
                self.messages.append({"role": "tool", "tool_name": name, "content": result})
        ui.error(f"Stopped after {self.max_steps} steps. Say 'continue' to keep going.")
