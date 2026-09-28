import * as vscode from 'vscode';

export const DIFF_SCHEME = 'lh-diff';

export interface ProposedEdit {
  confirm_id: number;
  path: string;
  before: string;
  after: string;
}

export class DiffReview implements vscode.TextDocumentContentProvider {
  private documents = new Map<string, string>();
  private edits = new Map<number, ProposedEdit>();

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.documents.get(uri.path) ?? '';
  }

  async open(edit: ProposedEdit): Promise<void> {
    this.edits.set(edit.confirm_id, edit);
    const left = vscode.Uri.from({ scheme: DIFF_SCHEME, path: `/${edit.confirm_id}/current/${edit.path}` });
    const right = vscode.Uri.from({ scheme: DIFF_SCHEME, path: `/${edit.confirm_id}/proposed/${edit.path}` });
    this.documents.set(left.path, edit.before);
    this.documents.set(right.path, edit.after);
    await vscode.commands.executeCommand('vscode.diff', left, right, `${edit.path} (proposed change)`, {
      preview: true,
      preserveFocus: true,
    });
  }

  async reopen(confirmId: number): Promise<void> {
    const edit = this.edits.get(confirmId);
    if (edit) {
      await this.open(edit);
    }
  }

  async close(confirmId: number): Promise<void> {
    const prefix = `/${confirmId}/`;
    const tabs = vscode.window.tabGroups.all.flatMap((group) => group.tabs).filter((tab) => {
      const input = tab.input;
      return input instanceof vscode.TabInputTextDiff
        && input.modified.scheme === DIFF_SCHEME
        && input.modified.path.startsWith(prefix);
    });
    if (tabs.length) {
      await vscode.window.tabGroups.close(tabs);
    }
    for (const key of [...this.documents.keys()]) {
      if (key.startsWith(prefix)) {
        this.documents.delete(key);
      }
    }
    this.edits.delete(confirmId);
  }
}
