(() => {
  const vscode = acquireVsCodeApi();
  const log = document.getElementById('log');
  const form = document.getElementById('composer');
  const input = document.getElementById('input');
  const sendButton = document.getElementById('send');
  const activity = document.getElementById('activity');
  const status = document.getElementById('status');
  const hint = document.getElementById('hint');

  let busy = false;
  let current = null;
  let lastTool = null;
  const openCards = new Map();

  const TOOL_LABELS = {
    list_files: 'Listed',
    search: 'Searched',
    read_file: 'Read',
    replace_in_file: 'Edit',
    write_file: 'Write',
    delegate_edit: 'Delegated edit',
    run_command: 'Command',
  };

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function renderMarkdown(text) {
    return text.split('```').map((part, index) => {
      if (index % 2 === 1) {
        const newline = part.indexOf('\n');
        const code = newline >= 0 ? part.slice(newline + 1) : part;
        return `<pre><code>${escapeHtml(code.replace(/\n$/, ''))}</code></pre>`;
      }
      return escapeHtml(part)
        .replace(/`([^`\n]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
        .replace(/^#{1,6}\s+(.*)$/gm, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
    }).join('');
  }

  function diffHtml(diff) {
    return diff.split('\n').map((line) => {
      let cls = '';
      if (line.startsWith('+++') || line.startsWith('---')) {
        cls = 'meta';
      } else if (line.startsWith('+')) {
        cls = 'add';
      } else if (line.startsWith('-')) {
        cls = 'del';
      } else if (line.startsWith('@@')) {
        cls = 'hunk';
      }
      return `<span class="${cls}">${escapeHtml(line)}</span>`;
    }).join('\n');
  }

  function diffCounts(diff) {
    let added = 0;
    let removed = 0;
    for (const line of diff.split('\n')) {
      if (line.startsWith('+') && !line.startsWith('+++')) {
        added += 1;
      } else if (line.startsWith('-') && !line.startsWith('---')) {
        removed += 1;
      }
    }
    return `<span class="add">+${added}</span> <span class="del">−${removed}</span>`;
  }

  function scrollToEnd() {
    log.scrollTop = log.scrollHeight;
  }

  function append(element) {
    document.getElementById('empty')?.remove();
    log.appendChild(element);
    scrollToEnd();
    return element;
  }

  function block(className, html) {
    const element = document.createElement('div');
    element.className = className;
    element.innerHTML = html;
    return append(element);
  }

  function setActivity(text) {
    activity.hidden = !text;
    activity.textContent = text || '';
  }

  function setBusy(value) {
    busy = value;
    sendButton.textContent = value ? 'Stop' : 'Send';
    sendButton.classList.toggle('stop', value);
    if (!value) {
      setActivity('');
      for (const [id, card] of openCards) {
        settle(card, id, 'Cancelled');
      }
    }
  }

  function stream(kind, text) {
    if (!current || current.kind !== kind) {
      if (kind === 'thinking') {
        const details = document.createElement('details');
        details.className = 'thinking';
        details.innerHTML = '<summary>Thinking…</summary><div class="body"></div>';
        append(details);
        current = { kind, box: details, element: details.querySelector('.body'), text: '' };
      } else {
        current = { kind, element: block('msg assistant', ''), text: '' };
      }
    }
    current.text += text;
    if (kind === 'thinking') {
      current.element.textContent = current.text;
    } else {
      current.element.innerHTML = renderMarkdown(current.text);
    }
    setActivity('');
    scrollToEnd();
  }

  function endStream() {
    if (current && current.kind === 'thinking') {
      current.box.querySelector('summary').textContent = 'Thought process';
    }
    current = null;
  }

  function toolDetail(name, args) {
    if (name === 'search') {
      return `“${args.pattern || ''}”${args.path && args.path !== '.' ? ` in ${args.path}` : ''}`;
    }
    if (name === 'read_file' && args.offset) {
      return `${args.path} from line ${args.offset}`;
    }
    return args.path || args.command || '';
  }

  function showTool(name, args) {
    const details = document.createElement('details');
    details.className = 'tool';
    details.innerHTML = `<summary><span class="tool-name">${escapeHtml(TOOL_LABELS[name] || name)}</span> `
      + `<span class="tool-detail">${escapeHtml(toolDetail(name, args || {}))}</span></summary>`
      + '<pre class="tool-output"></pre>';
    lastTool = append(details);
    setActivity('Working…');
  }

  function showToolResult(text) {
    if (!lastTool) {
      return;
    }
    lastTool.querySelector('.tool-output').textContent = text;
    if (text.startsWith('Error:') || text.startsWith('The user rejected') || text.startsWith('The user declined')) {
      lastTool.classList.add('failed');
    }
    setActivity('Thinking…');
  }

  function settle(card, id, outcome, approved) {
    openCards.delete(id);
    card.classList.add('resolved', approved ? 'approved' : 'rejected');
    card.querySelector('.actions')?.remove();
    card.querySelector('.feedback')?.remove();
    const note = document.createElement('div');
    note.className = 'outcome';
    note.textContent = outcome;
    card.appendChild(note);
  }

  function reply(card, id, approved, feedback) {
    vscode.postMessage({ type: 'confirm_reply', confirm_id: id, approved, feedback });
    let outcome = approved ? 'Accepted' : 'Rejected';
    if (feedback) {
      outcome = `Rejected with feedback: “${feedback}”`;
    }
    settle(card, id, outcome, approved);
    setActivity('Working…');
  }

  function confirmCard(id, titleHtml, bodyHtml, acceptLabel, rejectLabel, withDiffButton) {
    const card = block('card', `
      <div class="card-title">${titleHtml}</div>
      ${bodyHtml}
      <div class="actions">
        <button data-act="accept">${acceptLabel}</button>
        <button class="secondary" data-act="reject">${rejectLabel}</button>
        ${withDiffButton ? '<button class="link" data-act="diff">Open diff</button>' : ''}
      </div>
      <form class="feedback">
        <input placeholder="Or tell it what to do instead…" aria-label="Feedback">
        <button class="secondary" type="submit">Send</button>
      </form>`);
    openCards.set(id, card);
    card.querySelector('[data-act="accept"]').addEventListener('click', () => reply(card, id, true, ''));
    card.querySelector('[data-act="reject"]').addEventListener('click', () => reply(card, id, false, ''));
    card.querySelector('[data-act="diff"]')?.addEventListener('click', () => vscode.postMessage({ type: 'show_diff', confirm_id: id }));
    card.querySelector('.feedback').addEventListener('submit', (event) => {
      event.preventDefault();
      const feedback = card.querySelector('.feedback input').value.trim();
      if (feedback) {
        reply(card, id, false, feedback);
      }
    });
    setActivity('');
    card.querySelector('[data-act="accept"]').focus();
  }

  function describeAccess(scopes) {
    if (scopes === null || scopes === undefined) {
      return 'whole project';
    }
    if (scopes.length === 0) {
      return 'no files';
    }
    return scopes.length === 1 ? scopes[0] : `${scopes.length} folders`;
  }

  const handlers = {
    token: (m) => stream(m.kind, m.text),
    end_stream: () => endStream(),
    tool: (m) => showTool(m.name, m.args),
    tool_result: (m) => showToolResult(m.text),
    confirm_edit: (m) => confirmCard(
      m.confirm_id,
      `Change to <code>${escapeHtml(m.path)}</code> ${diffCounts(m.diff)}`,
      `<pre class="diff">${diffHtml(m.diff)}</pre>`,
      'Accept', 'Reject', true,
    ),
    confirm_command: (m) => confirmCard(
      m.confirm_id,
      'Run this command?',
      `<pre class="command">$ ${escapeHtml(m.command)}</pre>`,
      'Run', 'Deny', false,
    ),
    auto_applied: (m) => block('card resolved approved', `<div class="card-title">Applied automatically ${diffCounts(m.diff)}</div><pre class="diff">${diffHtml(m.diff)}</pre>`),
    coder_progress: (m) => setActivity(`Coder model writing… ${m.chars} characters`),
    coder_done: () => setActivity('Thinking…'),
    stats: (m) => block('meta', `${m.output_tokens} tokens · ${m.tokens_per_second} tok/s`),
    info: (m) => block('meta', escapeHtml(m.message)),
    error: (m) => block('msg error', escapeHtml(m.message)),
    turn_done: () => setBusy(false),
    ready: (m) => handlers.state(m),
    state: (m) => {
      status.textContent = `${m.model} + ${m.coder} · thinking ${m.think ? 'on' : 'off'} · access: ${describeAccess(m.scopes)}`;
      hint.textContent = m.think ? 'Thinking mode on' : '';
      if (busy !== m.busy) {
        setBusy(m.busy);
      }
    },
    exit: (m) => {
      block('msg error', `The harness stopped (exit code ${m.code}). Run “lh: Restart Harness” from the command palette, or “lh: Show Log” for details.`);
      status.textContent = 'Stopped';
      setBusy(false);
    },
    cleared: () => {
      log.innerHTML = '';
      current = null;
      lastTool = null;
      openCards.clear();
      setBusy(false);
    },
  };

  window.addEventListener('message', (event) => {
    const handler = handlers[event.data.type];
    if (handler) {
      handler(event.data);
    }
  });

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (busy) {
      vscode.postMessage({ type: 'cancel' });
      setActivity('Stopping…');
      return;
    }
    const text = input.value.trim();
    if (!text) {
      return;
    }
    endStream();
    block('msg user', escapeHtml(text).replace(/\n/g, '<br>'));
    vscode.postMessage({ type: 'ask', text });
    input.value = '';
    setBusy(true);
    setActivity('Thinking…');
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  vscode.postMessage({ type: 'webview_ready' });
})();
