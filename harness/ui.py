import sys

DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
RESET = "\033[0m"


class UI:
    def __init__(self):
        self.mode = None
        self.coder_chars = 0
        self.show_thinking = True

    def _write(self, text):
        sys.stdout.write(text)
        sys.stdout.flush()

    def token(self, kind, text):
        if kind != self.mode:
            if self.mode is not None:
                self._write(RESET + "\n")
            self.mode = kind
            if kind == "thinking":
                self._write(DIM if self.show_thinking else f"{DIM}thinking...")
        if kind == "thinking" and not self.show_thinking:
            return
        self._write(text)

    def end_stream(self):
        if self.mode is not None:
            self._write(RESET + "\n")
        self.mode = None

    def coder_token(self, kind, text):
        self.coder_chars += len(text)
        self._write(f"\r{DIM}  coder writing... {self.coder_chars} chars{RESET}")

    def coder_done(self):
        if self.coder_chars:
            self._write("\n")
        self.coder_chars = 0

    def tool(self, name, args):
        summary = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
        print(f"{CYAN}● {name}{RESET} {DIM}{summary}{RESET}")

    def result(self, text, max_lines=4):
        lines = text.splitlines() or [""]
        for line in lines[:max_lines]:
            print(f"{DIM}  │ {line[:160]}{RESET}")
        if len(lines) > max_lines:
            print(f"{DIM}  │ ... {len(lines) - max_lines} more lines{RESET}")

    def diff(self, text):
        for line in text.splitlines():
            if line.startswith(("+++", "---")):
                print(f"{BOLD}{line}{RESET}")
            elif line.startswith("+"):
                print(f"{GREEN}{line}{RESET}")
            elif line.startswith("-"):
                print(f"{RED}{line}{RESET}")
            elif line.startswith("@@"):
                print(f"{CYAN}{line}{RESET}")
            else:
                print(line)

    def command(self, command):
        print(f"{YELLOW}$ {command}{RESET}")

    def stats(self, stats):
        if not stats.get("eval_count"):
            return
        seconds = stats.get("eval_duration", 0) / 1e9 or 1
        print(f"{DIM}[{stats.get('prompt_eval_count', 0)} in, {stats['eval_count']} out, "
              f"{stats['eval_count'] / seconds:.1f} tok/s]{RESET}")

    def info(self, text):
        print(f"{BLUE}{text}{RESET}")

    def error(self, text):
        print(f"{RED}{text}{RESET}")


def _short(value, limit=60):
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"


ui = UI()
