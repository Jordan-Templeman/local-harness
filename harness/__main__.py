import argparse
import os

try:
    import readline  # noqa: F401
except ImportError:
    pass

from .agent import Agent
from .llm import Cancelled, LLMError
from .tools import ToolError, Workspace
from .ui import BOLD, DIM, GREEN, RESET, ui

HELP = """Commands:
  /tree [folder] [depth]   show the folder tree with file counts (✓ = model has access)
  /scope [folder]          pick which folders the model can see and edit
  /scope clear             give access to the whole project again
  /think on|off            reasoning mode: slower, better for tricky tasks
  /model NAME              switch the assistant model
  /coder NAME              switch the coder model used for delegated edits
  /auto on|off             apply file edits without asking (commands always ask)
  /reset                   start a fresh conversation
  /exit                    quit"""


def confirm(question):
    try:
        answer = input(f"{BOLD}{question}{RESET} [y/N, or type feedback] ").strip()
    except EOFError:
        return False, ""
    if answer.lower() in ("y", "yes"):
        return True, ""
    if answer.lower() in ("", "n", "no"):
        return False, ""
    return False, answer


class TerminalReviewer:
    def edit(self, path, before, after, diff):
        ui.diff(diff)
        return confirm(f"Apply this change to {path}?")

    def command(self, command):
        ui.command(command)
        return confirm("Run this command?")


def mark(workspace, rel):
    if workspace.in_scope(rel):
        return f"{GREEN}✓{RESET}"
    if workspace.overlaps_scope(rel):
        return f"{GREEN}◐{RESET}"
    return " "


def print_tree(workspace, base, depth, indent=""):
    dirs, loose = workspace.dir_counts(base, scoped=False)
    for name, count in dirs:
        rel = f"{base}/{name}" if base else name
        print(f"{mark(workspace, rel)} {indent}{name}/ {DIM}({count}){RESET}")
        if depth > 1:
            print_tree(workspace, rel, depth - 1, indent + "  ")
    if loose:
        print(f"  {indent}{DIM}{loose} files{RESET}")


def pick_scope(workspace, agent, base):
    dirs, _ = workspace.dir_counts(base, scoped=False)
    if not dirs:
        ui.error(f"No folders under {base or 'the root'}.")
        return
    print(f"Folders under {base or 'the project root'} ({BOLD}✓{RESET} = has access):")
    for i, (name, count) in enumerate(dirs, 1):
        rel = f"{base}/{name}" if base else name
        print(f"  {i:>3}. {mark(workspace, rel)} {name}/ {DIM}({count} file{"" if count == 1 else "s"}){RESET}")
    try:
        answer = input("Numbers or paths to toggle (space-separated), 'all' for everything, blank to cancel: ").strip()
    except EOFError:
        return
    if not answer:
        return
    if answer == "all":
        workspace.clear_scope()
    else:
        for token in answer.split():
            rel = token
            if token.isdigit() and 1 <= int(token) <= len(dirs):
                name = dirs[int(token) - 1][0]
                rel = f"{base}/{name}" if base else name
            try:
                workspace.toggle_scope(rel)
            except ToolError as e:
                ui.error(str(e))
    show_scope(workspace)
    agent.scope_changed()


def show_scope(workspace):
    if workspace.restricted:
        ui.info("Access limited to: " + workspace.scope_label())
    else:
        ui.info("Access: whole project")


def on_off(value, current):
    if value in ("on", "true", "yes"):
        return True
    if value in ("off", "false", "no"):
        return False
    return not current


def handle_command(line, agent, workspace):
    parts = line.split()
    command, args = parts[0], parts[1:]
    if command in ("/exit", "/quit"):
        raise SystemExit
    if command == "/help":
        print(HELP)
    elif command == "/tree":
        base = workspace.rel(args[0]) if args else ""
        depth = int(args[1]) if len(args) > 1 else 2
        print_tree(workspace, base, depth)
    elif command == "/scope":
        if args and args[0] == "clear":
            workspace.clear_scope()
            show_scope(workspace)
            agent.scope_changed()
        else:
            pick_scope(workspace, agent, workspace.rel(args[0]) if args else "")
    elif command == "/think":
        agent.think = on_off(args[0] if args else "", agent.think)
        ui.info(f"Thinking {'on' if agent.think else 'off'}")
    elif command == "/model" and args:
        agent.model = args[0]
        ui.info(f"Assistant model: {agent.model}")
    elif command == "/coder" and args:
        workspace.coder_model = args[0]
        ui.info(f"Coder model: {workspace.coder_model}")
    elif command == "/auto":
        workspace.auto_edits = on_off(args[0] if args else "", workspace.auto_edits)
        ui.info(f"Auto-apply edits {'on' if workspace.auto_edits else 'off'}")
    elif command == "/reset":
        agent.reset()
        ui.info("Conversation cleared.")
    else:
        print(HELP)


def run(agent, text):
    try:
        agent.ask(text)
    except (KeyboardInterrupt, Cancelled):
        ui.end_stream()
        ui.error("Interrupted.")
    except LLMError as e:
        ui.error(str(e))


def main():
    parser = argparse.ArgumentParser(prog="lh", description="Local coding assistant running on Ollama")
    parser.add_argument("root", nargs="?", default=".", help="project folder (default: current folder)")
    parser.add_argument("--model", default=os.environ.get("LH_MODEL", "qwen3:8b"))
    parser.add_argument("--coder", default=os.environ.get("LH_CODER", "qwen2.5-coder:14b"))
    parser.add_argument("--ctx", type=int, default=int(os.environ.get("LH_CTX", "16384")))
    parser.add_argument("--think", action="store_true", help="start with reasoning mode on")
    parser.add_argument("--auto-edits", action="store_true", help="apply file edits without asking")
    parser.add_argument("--scope", default="", help="comma-separated folders the model may access")
    parser.add_argument("-p", "--prompt", help="run a single request and exit")
    parser.add_argument("--server", action="store_true", help="speak JSON lines on stdin/stdout for the editor extension")
    args = parser.parse_args()
    scopes = [s for s in args.scope.split(",") if s]

    if args.server:
        from .server import serve
        serve(args.root, args.model, args.coder, args.ctx, args.think, scopes)
        return

    workspace = Workspace(
        args.root, args.coder, TerminalReviewer(), ui, num_ctx=args.ctx, auto_edits=args.auto_edits,
        scopes=scopes,
    )
    agent = Agent(workspace, args.model, think=args.think, num_ctx=args.ctx)

    print(f"{BOLD}lh{RESET} {DIM}{workspace.root} · {args.model} + {args.coder} · /help for commands{RESET}")
    show_scope(workspace)
    if args.prompt:
        run(agent, args.prompt)
        return

    while True:
        try:
            line = input(f"\n{BOLD}›{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            try:
                handle_command(line, agent, workspace)
            except SystemExit:
                break
            except (ToolError, ValueError) as e:
                ui.error(str(e))
            continue
        run(agent, line)


if __name__ == "__main__":
    main()
