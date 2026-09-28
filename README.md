# lh: local coding assistant

A small harness that turns local [Ollama](https://ollama.com) models into a coding assistant that can explore and edit a whole repository, from the terminal or from a VS Code–based editor. No cloud service, no account, no telemetry: it keeps working offline.

- **Assistant model** (default `qwen3:8b`): talks with you, explores the repo with tools, and decides what to change.
- **Coder model** (default `qwen2.5-coder:14b`): writes larger single-file changes when the assistant delegates them.
- **You**: approve every file edit (shown as a diff) and every shell command. Instead of approving, you can type feedback and the assistant gets it.

The harness is plain Python 3.10+ with no dependencies beyond git and a running Ollama.

## What to expect

Small local models are good at finding code, answering questions about it, and making focused edits you describe clearly. They are much weaker than frontier models at open-ended reasoning such as code review or multi-file design. The harness compensates where it can: it shows you every step, suggests real paths when the model guesses wrong, stops repeated calls, and gives reviews a strict format. It cannot give an 8B model better judgment.

## Setup

1. Install [Ollama](https://ollama.com/download) and pull the models:

   ```bash
   ollama pull qwen3:8b
   ollama pull qwen2.5-coder:14b   # or qwen2.5-coder:7b on smaller GPUs
   ```

2. Clone this repo and add an alias (bash/zsh):

   ```bash
   git clone https://github.com/Jordan-Templeman/local-harness.git
   echo "alias lh='PYTHONPATH=$PWD/local-harness python3 -m harness'" >> ~/.zshrc
   ```

On Windows, run the harness in WSL. If WSL cannot reach Ollama on `127.0.0.1:11434`, enable mirrored networking by adding `networkingMode=mirrored` under `[wsl2]` in `%USERPROFILE%\.wslconfig`, then run `wsl --shutdown`.

## Run

```bash
cd ~/some/project
lh                                   # whole repo
lh --scope app/models,spec/models    # only these folders
lh -p "question"                     # one request, then exit
```

## Commands

| Command | What it does |
| --- | --- |
| `/tree [folder] [depth]` | Folder tree with file counts; ✓ = assistant has access, ◐ = partial |
| `/scope [folder]` | Pick folders the assistant may see and edit, by number or path |
| `/scope clear` | Whole project again |
| `/think on\|off` | Reasoning mode: slower, better for tricky tasks |
| `/model NAME`, `/coder NAME` | Swap models |
| `/auto on\|off` | Apply edits without asking (commands always ask) |
| `/reset`, `/exit` | Fresh conversation, quit |

## Tools the assistant has

`list_files`, `search`, `read_file`, `replace_in_file`, `write_file`, `delegate_edit`, `run_command`.
Paths are confined to the project root and to the folders in scope. Asking for a review (“review”, “audit”, “find bugs”) switches on review mode: findings only, each with `path:line`, and edits disabled.

## Editor extension (VS Code and forks)

`extension/` is a thin TypeScript client. It starts `python3 -m harness --server` and exchanges JSON lines with it, so the terminal and the editor share one core. On Windows it runs the harness inside WSL by default (`lh.useWsl`).

- **Chat** panel in the lh sidebar: streaming replies, collapsible tool calls, Accept / Reject / feedback cards for every edit and command, and a Stop button.
- **Access** panel: project folders with checkboxes. No boxes checked means the whole project; the toolbar can grant everything or remove all access.
- Proposed edits open in the diff editor.
- Status bar shows the models; click to switch.

Build and install:

```bash
cd extension && npm install && npm run package
code --install-extension lh-local-assistant.vsix
```

`npm run package` copies `harness/` into the extension, so rebuild after changing the Python.

Some VS Code forks keep their data in another product's folders; if the extension installs but never appears, pass that editor's `--user-data-dir` and `--extensions-dir` to the install command. For example, Devin currently needs `--user-data-dir "$APPDATA/Windsurf" --extensions-dir ~/.windsurf/extensions`.

## Settings

Environment variables for the terminal: `LH_MODEL`, `LH_CODER`, `LH_CTX` (context window, default 16384), `OLLAMA_API_BASE`. The extension has matching `lh.*` settings.

With 8 GB of VRAM, a 16k context pushes about 20% of `qwen3:8b` onto the CPU; lower the context for speed, raise it for bigger tasks.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## Layout

- `harness/llm.py`: streaming Ollama chat client
- `harness/tools.py`: workspace, scoping, tools and their schemas
- `harness/agent.py`: system prompt, repo overview, tool loop, review mode
- `harness/server.py`: JSON-lines protocol for the extension
- `harness/ui.py`: terminal output
- `harness/__main__.py`: CLI and slash commands
- `extension/`: VS Code extension

## License

[Apache License 2.0](LICENSE)
