import * as vscode from 'vscode';
import { AccessTree } from './accessTree';
import { ChatView } from './chatView';
import { HarnessClient, HarnessMessage } from './client';
import { DIFF_SCHEME, DiffReview } from './diffReview';

export function activate(context: vscode.ExtensionContext): void {
  const log = vscode.window.createOutputChannel('lh');
  const chat = new ChatView(context.extensionUri);
  context.subscriptions.push(
    log,
    vscode.window.registerWebviewViewProvider('lh.chat', chat, { webviewOptions: { retainContextWhenHidden: true } }),
  );

  const root = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!root || root.scheme !== 'file') {
    chat.post({ type: 'error', message: 'Open a project folder to use the local assistant.' });
    return;
  }

  const client = new HarnessClient(context.extensionPath, root.fsPath, log);
  const diffs = new DiffReview();
  const access = new AccessTree(client);
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  status.command = 'lh.pickModels';
  status.show();

  const updateStatus = () => {
    const state = client.state;
    if (!state) {
      status.text = '$(circle-slash) lh stopped';
      status.tooltip = 'The local assistant is not running. Click to choose models, or run "lh: Restart Harness".';
      return;
    }
    status.text = `$(hubot) ${state.model}${state.think ? ' · thinking' : ''}`;
    status.tooltip = `Assistant: ${state.model}\nCoder: ${state.coder}\nClick to change models`;
  };

  client.onMessage((message: HarnessMessage) => {
    chat.post(message);
    switch (message.type) {
      case 'ready':
      case 'state':
        updateStatus();
        access.refresh();
        break;
      case 'confirm_edit':
        diffs.open(message as any);
        break;
      case 'exit':
        updateStatus();
        break;
    }
  });

  chat.onMessage(async (message) => {
    switch (message.type) {
      case 'webview_ready':
        if (client.state) {
          chat.post({ type: 'state', ...client.state });
        }
        break;
      case 'ask':
        client.send({ type: 'ask', text: message.text });
        break;
      case 'cancel':
        client.send({ type: 'cancel' });
        break;
      case 'confirm_reply':
        client.send(message);
        diffs.close(message.confirm_id);
        break;
      case 'show_diff':
        diffs.reopen(message.confirm_id);
        break;
    }
  });

  const pickModels = async () => {
    const response = await client.request({ type: 'models' });
    if (response.error) {
      vscode.window.showErrorMessage(`Could not list Ollama models: ${response.error}`);
      return;
    }
    const models: string[] = response.models ?? [];
    const state = client.state;
    const assistant = await vscode.window.showQuickPick(
      models.map((name) => ({ label: name, description: name === state?.model ? 'current' : undefined })),
      { title: 'Assistant model (the one you chat with)', placeHolder: state?.model },
    );
    if (!assistant) {
      return;
    }
    const coder = await vscode.window.showQuickPick(
      models.map((name) => ({ label: name, description: name === state?.coder ? 'current' : undefined })),
      { title: 'Coder model (writes delegated file changes)', placeHolder: state?.coder },
    );
    client.send({ type: 'set', model: assistant.label, ...(coder ? { coder: coder.label } : {}) });
  };

  context.subscriptions.push(
    client,
    access,
    status,
    vscode.workspace.registerTextDocumentContentProvider(DIFF_SCHEME, diffs),
    vscode.commands.registerCommand('lh.newChat', () => {
      if (client.state?.busy) {
        vscode.window.showWarningMessage('Stop the current request before starting a new chat.');
        return;
      }
      client.send({ type: 'reset' });
      chat.post({ type: 'cleared' });
    }),
    vscode.commands.registerCommand('lh.toggleThink', () => {
      const think = !client.state?.think;
      client.send({ type: 'set', think });
      vscode.window.setStatusBarMessage(`lh: thinking mode ${think ? 'on (slower, more careful)' : 'off'}`, 3000);
    }),
    vscode.commands.registerCommand('lh.pickModels', pickModels),
    vscode.commands.registerCommand('lh.wholeProject', async () => {
      await client.request({ type: 'clear_scope' });
      access.refresh();
    }),
    vscode.commands.registerCommand('lh.noAccess', async () => {
      await client.request({ type: 'set_scope', scopes: [] });
      access.refresh();
    }),
    vscode.commands.registerCommand('lh.refreshAccess', () => access.refresh()),
    vscode.commands.registerCommand('lh.restart', () => {
      chat.post({ type: 'cleared' });
      client.restart();
    }),
    vscode.commands.registerCommand('lh.showLog', () => log.show()),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration('lh')) {
        vscode.window.showInformationMessage('lh settings changed. Restart the harness to apply them.', 'Restart')
          .then((choice) => choice && vscode.commands.executeCommand('lh.restart'));
      }
    }),
  );

  client.start();
  updateStatus();
}

export function deactivate(): void {}
