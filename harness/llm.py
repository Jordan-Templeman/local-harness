import json
import os
import urllib.error
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434").rstrip("/")


class LLMError(RuntimeError):
    pass


def chat(model, messages, tools=None, think=None, num_ctx=16384, on_token=None):
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"num_ctx": num_ctx},
    }
    if think is not None:
        body["think"] = think
    if tools:
        body["tools"] = tools

    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    content, thinking, tool_calls, stats = [], [], [], {}
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            for raw in response:
                if not raw.strip():
                    continue
                chunk = json.loads(raw)
                if "error" in chunk:
                    raise LLMError(chunk["error"])
                message = chunk.get("message") or {}
                if message.get("thinking"):
                    thinking.append(message["thinking"])
                    if on_token:
                        on_token("thinking", message["thinking"])
                if message.get("content"):
                    content.append(message["content"])
                    if on_token:
                        on_token("content", message["content"])
                tool_calls.extend(message.get("tool_calls") or [])
                if chunk.get("done"):
                    stats = chunk
    except urllib.error.HTTPError as e:
        raise LLMError(f"Ollama returned {e.code}: {e.read().decode(errors='replace')}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"Cannot reach Ollama at {OLLAMA_URL}: {e.reason}") from e

    return {
        "content": "".join(content),
        "thinking": "".join(thinking),
        "tool_calls": tool_calls,
        "stats": stats,
    }
