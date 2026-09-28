import itertools
import json
import sys
import threading
import urllib.request

from . import llm
from .agent import Agent
from .tools import ToolError, Workspace


class Channel:
    def __init__(self, out):
        self.out = out
        self.lock = threading.Lock()

    def send(self, type_, **data):
        with self.lock:
            self.out.write(json.dumps({"type": type_, **data}) + "\n")
            self.out.flush()


class ServerUI:
    def __init__(self, channel):
        self.channel = channel
        self.coder_chars = 0
        self.reported_chars = 0

    def token(self, kind, text):
        self.channel.send("token", kind=kind, text=text)

    def end_stream(self):
        self.channel.send("end_stream")

    def coder_token(self, kind, text):
        self.coder_chars += len(text)
        if self.coder_chars - self.reported_chars >= 80:
            self.reported_chars = self.coder_chars
            self.channel.send("coder_progress", chars=self.coder_chars)

    def coder_done(self):
        self.coder_chars = self.reported_chars = 0
        self.channel.send("coder_done")

    def tool(self, name, args):
        self.channel.send("tool", name=name, args=args)

    def result(self, text):
        self.channel.send("tool_result", text=text[:8000])

    def diff(self, text):
        self.channel.send("auto_applied", diff=text)

    def stats(self, stats):
        seconds = stats.get("eval_duration", 0) / 1e9 or 1
        self.channel.send(
            "stats",
            prompt_tokens=stats.get("prompt_eval_count", 0),
            output_tokens=stats.get("eval_count", 0),
            tokens_per_second=round(stats.get("eval_count", 0) / seconds, 1),
        )

    def info(self, text):
        self.channel.send("info", message=text)

    def error(self, text):
        self.channel.send("error", message=text)


class ServerReviewer:
    def __init__(self, channel):
        self.channel = channel
        self.ids = itertools.count(1)
        self.pending = {}
        self.lock = threading.Lock()

    def wait(self, type_, **data):
        confirm_id = next(self.ids)
        done, answer = threading.Event(), {}
        with self.lock:
            self.pending[confirm_id] = (done, answer)
        self.channel.send(type_, confirm_id=confirm_id, **data)
        done.wait()
        return bool(answer.get("approved")), answer.get("feedback") or ""

    def edit(self, path, before, after, diff):
        return self.wait("confirm_edit", path=path, before=before, after=after, diff=diff)

    def command(self, command):
        return self.wait("confirm_command", command=command)

    def reply(self, confirm_id, approved, feedback=""):
        with self.lock:
            done, answer = self.pending.pop(confirm_id, (None, None))
        if done:
            answer.update(approved=approved, feedback=feedback)
            done.set()

    def reject_all(self):
        with self.lock:
            waiting = list(self.pending.values())
            self.pending.clear()
        for done, _ in waiting:
            done.set()


def tree_entries(workspace, base):
    prefix = f"{base}/" if base else ""
    counts, nested = {}, set()
    for f in workspace.all_files(scoped=False):
        if not f.startswith(prefix):
            continue
        parts = f[len(prefix):].split("/")
        if len(parts) < 2:
            continue
        counts[parts[0]] = counts.get(parts[0], 0) + 1
        if len(parts) > 2:
            nested.add(parts[0])
    entries = []
    for name in sorted(counts):
        path = f"{prefix}{name}"
        if not workspace.restricted:
            access = "none"
        elif workspace.in_scope(path):
            access = "full"
        elif workspace.overlaps_scope(path):
            access = "partial"
        else:
            access = "none"
        entries.append({
            "name": name, "path": path, "count": counts[name],
            "has_children": name in nested, "access": access,
        })
    return entries


def list_models():
    with urllib.request.urlopen(f"{llm.OLLAMA_URL}/api/tags", timeout=10) as response:
        return sorted(m["name"] for m in json.load(response).get("models", []))


def serve(root, model, coder, num_ctx, think, scopes):
    channel = Channel(sys.stdout)
    sys.stdout = sys.stderr
    ui = ServerUI(channel)
    reviewer = ServerReviewer(channel)
    workspace = Workspace(root, coder, reviewer, ui, num_ctx=num_ctx, scopes=scopes)
    agent = Agent(workspace, model, think=think, num_ctx=num_ctx)
    worker = None

    def busy():
        return worker is not None and worker.is_alive()

    def state(type_="state", is_busy=None):
        channel.send(
            type_, root=str(workspace.root), model=agent.model, coder=workspace.coder_model,
            think=agent.think, auto_edits=workspace.auto_edits, scopes=workspace.scopes,
            busy=busy() if is_busy is None else is_busy,
        )

    def run_turn(text):
        try:
            agent.ask(text)
        except llm.Cancelled:
            ui.info("Stopped.")
        except llm.LLMError as e:
            ui.error(str(e))
        except Exception as e:
            ui.error(f"{type(e).__name__}: {e}")
        finally:
            ui.end_stream()
            channel.send("turn_done")
            state(is_busy=False)

    state("ready")
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind, request_id = message.get("type"), message.get("id")
        try:
            if kind == "ask":
                if busy():
                    raise ToolError("Still working on the previous request. Stop it first.")
                worker = threading.Thread(target=run_turn, args=(message["text"],), daemon=True)
                worker.start()
                state()
            elif kind == "cancel":
                agent.cancel()
                reviewer.reject_all()
            elif kind == "confirm_reply":
                reviewer.reply(message["confirm_id"], bool(message.get("approved")), message.get("feedback", ""))
            elif kind == "tree":
                channel.send("response", id=request_id, entries=tree_entries(workspace, message.get("base", "")))
            elif kind == "toggle_scope":
                workspace.toggle_scope(message["path"])
                agent.scope_changed()
                channel.send("response", id=request_id)
                state()
            elif kind == "clear_scope":
                workspace.clear_scope()
                agent.scope_changed()
                channel.send("response", id=request_id)
                state()
            elif kind == "set_scope":
                if message.get("scopes") is None:
                    workspace.clear_scope()
                else:
                    workspace.scopes = []
                    for path in message["scopes"]:
                        workspace.add_scope(path)
                agent.scope_changed()
                channel.send("response", id=request_id)
                state()
            elif kind == "set":
                if "model" in message:
                    agent.model = message["model"]
                if "coder" in message:
                    workspace.coder_model = message["coder"]
                if "think" in message:
                    agent.think = bool(message["think"])
                if "auto_edits" in message:
                    workspace.auto_edits = bool(message["auto_edits"])
                state()
            elif kind == "reset":
                if busy():
                    raise ToolError("Stop the current request before starting a new chat.")
                agent.reset()
                state()
            elif kind == "models":
                channel.send("response", id=request_id, models=list_models())
            elif kind == "state":
                state()
        except Exception as e:
            if request_id is not None:
                channel.send("response", id=request_id, error=str(e))
            else:
                ui.error(str(e))
    agent.cancel()
    reviewer.reject_all()
