import difflib
import fnmatch
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from . import llm

MAX_OUTPUT = 6000
MAX_LIST = 200
MAX_MATCHES = 80

CODER_SYSTEM = (
    "You are an expert programmer who edits files exactly as instructed. "
    "Return the complete updated file in one fenced code block and nothing else. "
    "Keep everything the instructions do not ask you to change exactly as it is."
)


class ToolError(Exception):
    pass


def clip(text, limit=MAX_OUTPUT):
    if len(text) <= limit:
        return text
    head = text[: limit // 3]
    tail = text[-(limit * 2 // 3):]
    return f"{head}\n... [{len(text) - limit} characters omitted] ...\n{tail}"


def extract_code(text):
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)
    return max(blocks, key=len) if blocks else None


class Workspace:
    def __init__(self, root, coder_model, reviewer, ui, num_ctx=16384, auto_edits=False, scopes=()):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise SystemExit(f"{root} is not a directory")
        self.coder_model = coder_model
        self.reviewer = reviewer
        self.ui = ui
        self.num_ctx = num_ctx
        self.auto_edits = auto_edits
        self.is_git = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=self.root, capture_output=True, text=True,
        ).stdout.strip() == "true"
        self.scopes = None
        for scope in scopes:
            self.add_scope(scope)
        self.handlers = {
            "list_files": self.list_files,
            "search": self.search,
            "read_file": self.read_file,
            "replace_in_file": self.replace_in_file,
            "write_file": self.write_file,
            "delegate_edit": self.delegate_edit,
            "run_command": self.run_command,
        }

    def call(self, name, args):
        handler = self.handlers.get(name)
        if handler is None:
            return f"Error: unknown tool '{name}'. Available tools: {', '.join(self.handlers)}"
        try:
            return handler(**args)
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}"
        except ToolError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    def rel(self, path):
        resolved = (self.root / (path or ".")).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ToolError(f"{path} is outside the project")
        return "" if resolved == self.root else resolved.relative_to(self.root).as_posix()

    @property
    def restricted(self):
        return self.scopes is not None

    def scope_label(self):
        return ", ".join(self.scopes) if self.scopes else "nothing"

    def in_scope(self, rel):
        if not self.restricted:
            return True
        return any(rel == s or rel.startswith(s + "/") for s in self.scopes)

    def overlaps_scope(self, rel):
        if not self.restricted or self.in_scope(rel):
            return True
        return any(rel == "" or s.startswith(rel + "/") for s in self.scopes)

    def resolve(self, path):
        rel = self.rel(path)
        if not self.in_scope(rel):
            raise ToolError(f"{path} is outside the folders the user gave you access to: {self.scope_label()}")
        return self.root / rel

    def clear_scope(self):
        self.scopes = None

    def add_scope(self, path):
        rel = self.rel(path)
        if not (self.root / rel).exists():
            raise ToolError(f"{path} does not exist")
        if rel == "":
            self.clear_scope()
            return
        if self.restricted and self.in_scope(rel):
            return
        kept = [s for s in (self.scopes or []) if not s.startswith(rel + "/")]
        self.scopes = sorted(kept + [rel])

    def remove_scope(self, path):
        rel = self.rel(path)
        if self.restricted:
            self.scopes = [s for s in self.scopes if s != rel]

    def children(self, base):
        prefix = f"{base}/" if base else ""
        names = set()
        for f in self.all_files(scoped=False):
            if f.startswith(prefix):
                names.add(prefix + f[len(prefix):].split("/", 1)[0])
        return sorted(names)

    def toggle_scope(self, path):
        rel = self.rel(path)
        if self.restricted and rel in self.scopes:
            self.remove_scope(rel)
        elif not self.restricted or not self.in_scope(rel):
            self.add_scope(rel)
        else:
            ancestor = next(s for s in self.scopes if rel.startswith(s + "/"))
            self.scopes.remove(ancestor)
            current = ancestor
            while current != rel:
                following = f"{current}/{rel[len(current) + 1:].split('/', 1)[0]}"
                self.scopes += [c for c in self.children(current) if c != following]
                current = following
            self.scopes.sort()

    def all_files(self, scoped=True):
        if self.is_git:
            output = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=self.root, capture_output=True, text=True,
            ).stdout
            files = sorted({line for line in output.splitlines() if line})
        else:
            skip = {".git", "node_modules", ".venv", "venv", "__pycache__", "tmp", "log", "dist", "build"}
            files = sorted(
                p.relative_to(self.root).as_posix()
                for p in self.root.rglob("*")
                if p.is_file() and not skip.intersection(p.relative_to(self.root).parts)
            )
        return [f for f in files if self.in_scope(f)] if scoped else files

    def dir_counts(self, base="", scoped=True):
        prefix = f"{base}/" if base else ""
        dirs, loose = Counter(), 0
        for f in self.all_files(scoped):
            if not f.startswith(prefix):
                continue
            rest = f[len(prefix):]
            if "/" in rest:
                dirs[rest.split("/", 1)[0]] += 1
            else:
                loose += 1
        return sorted(dirs.items()), loose

    def list_files(self, path=".", pattern=None):
        base = self.rel(path)
        if not self.overlaps_scope(base):
            raise ToolError(f"{path} is outside the folders the user gave you access to: {self.scope_label()}")
        prefix = f"{base}/" if base else ""
        files = [f for f in self.all_files() if f == base or f.startswith(prefix)]
        if pattern:
            files = [f for f in files if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(Path(f).name, pattern)]
        if not files:
            return "No files matched."
        if len(files) <= MAX_LIST:
            return "\n".join(files)
        if pattern:
            return "\n".join(files[:MAX_LIST]) + f"\n... and {len(files) - MAX_LIST} more; narrow the path or pattern"
        dirs, loose = self.dir_counts(base)
        lines = [f"{len(files)} files under {base or 'the project root'}; too many to list. Folders:"]
        lines += [f"  {prefix}{name}/  ({count} file{"" if count == 1 else "s"})" for name, count in dirs]
        if loose:
            lines.append(f"  plus {loose} files directly in {base or 'the root'}")
        lines.append("Call list_files on a folder to see its files.")
        return "\n".join(lines)

    def search(self, pattern, path=".", glob=None):
        base = self.rel(path)
        if not self.overlaps_scope(base):
            raise ToolError(f"{path} is outside the folders the user gave you access to: {self.scope_label()}")
        if self.in_scope(base):
            targets = [base or "."]
        else:
            targets = [s for s in self.scopes if base == "" or s.startswith(base + "/")]
            if not targets:
                raise ToolError(f"{path} is outside the folders the user gave you access to: {self.scope_label()}")
        if shutil.which("rg"):
            cmd = ["rg", "-n", "-S", "--max-columns", "200", "--max-count", "20"]
            if glob:
                cmd += ["-g", glob]
            cmd += ["-e", pattern, "--", *targets]
        elif self.is_git:
            cmd = ["git", "grep", "-n", "-I", "-P", *([] if re.search(r"[A-Z]", pattern) else ["-i"]),
                   "-e", pattern, "--", *targets]
        else:
            cmd = ["grep", "-rnIE", "-e", pattern, "--", *targets]
        result = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=60)
        lines = [line[:240] for line in result.stdout.splitlines()]
        if glob and not shutil.which("rg"):
            lines = [line for line in lines if fnmatch.fnmatch(Path(line.split(":", 1)[0]).name, glob)]
        if not lines:
            if result.returncode > 1 and result.stderr.strip():
                raise ToolError(result.stderr.strip()[:500])
            return "No matches."
        more = f"\n... {len(lines) - MAX_MATCHES} more matches; narrow the pattern or path" if len(lines) > MAX_MATCHES else ""
        return "\n".join(lines[:MAX_MATCHES]) + more

    def read(self, path):
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            return f.read()

    def read_file(self, path, offset=1, limit=250):
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"{path} does not exist or is not a file")
        text = self.read(target)
        if "\0" in text[:2000]:
            raise ToolError(f"{path} looks like a binary file")
        lines = text.replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        offset, limit = max(1, int(offset)), min(max(1, int(limit)), 400)
        chunk = lines[offset - 1: offset - 1 + limit]
        if not chunk:
            return f"{path} has {len(lines)} lines; nothing at line {offset}."
        body = "\n".join(f"{n:>5}  {line}" for n, line in enumerate(chunk, offset))
        end = offset + len(chunk) - 1
        footer = f"\n[lines {offset}-{end} of {len(lines)}]" if end < len(lines) or offset > 1 else ""
        return clip(body) + footer

    def apply(self, target, before, after):
        if before == after:
            return "No changes were made; the new content is identical."
        rel = target.relative_to(self.root).as_posix()
        diff_lines = list(difflib.unified_diff(
            before.replace("\r\n", "\n").splitlines(True),
            after.replace("\r\n", "\n").splitlines(True),
            f"a/{rel}", f"b/{rel}",
        ))
        if self.auto_edits:
            self.ui.diff("".join(diff_lines))
        else:
            approved, feedback = self.reviewer.edit(rel, before, after, "".join(diff_lines))
            if not approved:
                return f"The user rejected the change to {rel}." + (f" Their feedback: {feedback}" if feedback else "")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(after)
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        return f"Applied the change to {rel} (+{added} -{removed} lines)."

    def replace_in_file(self, path, old_text, new_text):
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"{path} does not exist; use write_file to create it")
        if not old_text:
            raise ToolError("old_text is empty")
        before = self.read(target)
        if "\r\n" in before:
            old_text = old_text.replace("\r\n", "\n").replace("\n", "\r\n")
            new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
        count = before.count(old_text)
        if count == 0:
            raise ToolError("old_text was not found. Read the file again and copy the text exactly, including indentation, without line numbers.")
        if count > 1:
            raise ToolError(f"old_text matches {count} places. Include more surrounding lines so it matches exactly one.")
        return self.apply(target, before, before.replace(old_text, new_text, 1))

    def write_file(self, path, content):
        target = self.resolve(path)
        before = self.read(target) if target.is_file() else ""
        return self.apply(target, before, content)

    def delegate_edit(self, path, instructions):
        target = self.resolve(path)
        before = self.read(target) if target.is_file() else ""
        if before.count("\n") > 600:
            raise ToolError(f"{path} is too large for delegate_edit; use replace_in_file for targeted changes")
        rel = target.relative_to(self.root).as_posix()
        normalized = before.replace("\r\n", "\n")
        messages = [
            {"role": "system", "content": CODER_SYSTEM},
            {"role": "user", "content": f"File: {rel}\n```\n{normalized}\n```\n\nInstructions:\n{instructions}"},
        ]
        try:
            response = llm.chat(self.coder_model, messages, num_ctx=self.num_ctx, on_token=self.ui.coder_token)
        finally:
            self.ui.coder_done()
        after = extract_code(response["content"])
        if after is None:
            raise ToolError("the coder model did not return a code block; try again with clearer instructions or use replace_in_file")
        if normalized.endswith("\n") and not after.endswith("\n"):
            after += "\n"
        if "\r\n" in before:
            after = after.replace("\n", "\r\n")
        return self.apply(target, before, after)

    def run_command(self, command):
        approved, feedback = self.reviewer.command(command)
        if not approved:
            return "The user declined to run the command." + (f" Their feedback: {feedback}" if feedback else "")
        try:
            result = subprocess.run(
                command, shell=True, cwd=self.root, capture_output=True, text=True,
                timeout=300, executable=shutil.which("bash"),
            )
        except subprocess.TimeoutExpired:
            return "The command timed out after 300 seconds."
        output = result.stdout
        if result.stderr.strip():
            output += f"\n[stderr]\n{result.stderr}"
        return f"Exit code {result.returncode}\n{clip(output.strip()) or '(no output)'}"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the project. For big folders it returns a per-folder summary instead; drill in by calling it on a subfolder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder relative to the project root. Defaults to the root."},
                    "pattern": {"type": "string", "description": "Optional glob such as '*.rb' or 'app/models/*.rb'."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search file contents with a regular expression. Returns file:line:text matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression to search for."},
                    "path": {"type": "string", "description": "Folder or file to search in. Defaults to the root."},
                    "glob": {"type": "string", "description": "Optional file-name glob such as '*.py'."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file with line numbers. Long files are returned in pages; use offset to continue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "First line to read, starting at 1."},
                    "limit": {"type": "integer", "description": "Number of lines to read, at most 400."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace one exact, unique snippet of a file. Best for small, targeted edits. Copy old_text exactly from read_file output without the line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "description": "Exact existing text; must match exactly one place."},
                    "new_text": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file, or completely overwrite a small file, with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_edit",
            "description": "Hand a larger change to one file to a specialist coder model. Give precise, complete instructions: what to change, where, and the expected behaviour.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "instructions": {"type": "string"},
                },
                "required": ["path", "instructions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the project root, e.g. tests, linters or a script. The user approves each command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
]
