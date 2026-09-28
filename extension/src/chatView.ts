import * as crypto from 'crypto';
import * as vscode from 'vscode';

export class ChatView implements vscode.WebviewViewProvider {
  private view: vscode.WebviewView | undefined;
  private queued: unknown[] = [];
  private listener: ((message: any) => void) | undefined;

  constructor(private readonly extensionUri: vscode.Uri) {}

  onMessage(listener: (message: any) => void): void {
    this.listener = listener;
  }

  post(message: unknown): void {
    if (this.view) {
      this.view.webview.postMessage(message);
    } else {
      this.queued.push(message);
    }
  }

  reveal(): void {
    this.view?.show(true);
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    const media = vscode.Uri.joinPath(this.extensionUri, 'media');
    view.webview.options = { enableScripts: true, localResourceRoots: [media] };
    view.webview.html = this.html(view.webview, media);
    view.webview.onDidReceiveMessage((message) => this.listener?.(message));
    view.onDidDispose(() => {
      this.view = undefined;
    });
    for (const message of this.queued.splice(0)) {
      view.webview.postMessage(message);
    }
  }

  private html(webview: vscode.Webview, media: vscode.Uri): string {
    const nonce = crypto.randomBytes(16).toString('base64');
    const script = webview.asWebviewUri(vscode.Uri.joinPath(media, 'chat.js'));
    const style = webview.asWebviewUri(vscode.Uri.joinPath(media, 'chat.css'));
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link href="${style}" rel="stylesheet">
</head>
<body>
  <div id="status" class="status"></div>
  <div id="log" class="log" aria-live="polite">
    <div class="empty" id="empty">
      <p><strong>Local assistant</strong></p>
      <p>Ask about your code or describe a change. Every edit is shown as a diff for you to accept or reject, and every command asks first.</p>
      <p>Use the <strong>Access</strong> panel below to choose which folders it can see.</p>
    </div>
  </div>
  <div id="activity" class="activity" hidden></div>
  <form id="composer" class="composer">
    <textarea id="input" rows="3" placeholder="Ask or describe a change. Enter to send, Shift+Enter for a new line."></textarea>
    <div class="composer-row">
      <span id="hint" class="hint"></span>
      <button id="send" type="submit">Send</button>
    </div>
  </form>
  <script nonce="${nonce}" src="${script}"></script>
</body>
</html>`;
  }
}
