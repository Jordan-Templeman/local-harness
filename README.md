# lh: local coding assistant

A small harness that turns local Ollama models into a coding assistant that can explore and edit a whole repo.

- **Assistant model** (`qwen3:8b`): talks with you, explores the repo with tools, and decides what to change.
- **Coder model** (`qwen2.5-coder:14b`): writes larger single-file changes when the assistant delegates them.
- **You**: approve every file edit (shown as a diff) and every shell command. Instead of `y`, type feedback and the assistant gets it.

No dependencies beyond Python 3.10+, git and a running Ollama.

## Run

```bash
cd ~/some/project
lh                                   # whole repo
lh --scope app/models,spec/models    # only these folders
lh -p "question"                     # one request, then exit
```

`lh` is an alias for `PYTHONPATH=/path/to/local-harness python3 -m harness`.

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
Paths are confined to the project root and to the folders in scope.

## Settings

Environment variables: `LH_MODEL`, `LH_CODER`, `LH_CTX` (context window, default 16384), `OLLAMA_API_BASE`.
With 8 GB of VRAM, a 16k context pushes about 20% of `qwen3:8b` onto the CPU; lower `LH_CTX` for speed, raise it for bigger tasks.

## Layout

- `harness/llm.py`: streaming Ollama chat client
- `harness/tools.py`: workspace, scoping, tools and their schemas
- `harness/agent.py`: system prompt, repo overview, tool loop
- `harness/ui.py`: terminal output
- `harness/__main__.py`: CLI and slash commands
