import * as vscode from 'vscode';
import { HarnessClient } from './client';

interface Entry {
  name: string;
  path: string;
  count: number;
  has_children: boolean;
  access: 'full' | 'partial' | 'none';
}

export class AccessTree implements vscode.TreeDataProvider<Entry>, vscode.Disposable {
  private changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;
  readonly view: vscode.TreeView<Entry>;

  constructor(private readonly client: HarnessClient) {
    this.view = vscode.window.createTreeView('lh.access', {
      treeDataProvider: this,
      manageCheckboxStateManually: true,
    });
    this.view.onDidChangeCheckboxState(async (event) => {
      for (const [entry] of event.items) {
        const response = await client.request({ type: 'toggle_scope', path: entry.path });
        if (response.error) {
          vscode.window.showErrorMessage(response.error);
        }
      }
      this.refresh();
    });
  }

  refresh(): void {
    this.updateMessage();
    this.changed.fire();
  }

  getTreeItem(entry: Entry): vscode.TreeItem {
    const item = new vscode.TreeItem(
      entry.name,
      entry.has_children ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None,
    );
    item.id = entry.path;
    item.iconPath = vscode.ThemeIcon.Folder;
    const files = `${entry.count} file${entry.count === 1 ? '' : 's'}`;
    item.description = entry.access === 'partial' ? `${files} · partly shared` : files;
    item.checkboxState = entry.access === 'full'
      ? vscode.TreeItemCheckboxState.Checked
      : vscode.TreeItemCheckboxState.Unchecked;
    item.tooltip = {
      full: `The assistant can read and edit ${entry.path}`,
      partial: `The assistant can access some folders inside ${entry.path}`,
      none: `The assistant cannot access ${entry.path}`,
    }[entry.access];
    return item;
  }

  async getChildren(entry?: Entry): Promise<Entry[]> {
    const response = await this.client.request({ type: 'tree', base: entry?.path ?? '' });
    return (response.entries as Entry[]) ?? [];
  }

  private updateMessage(): void {
    const scopes = this.client.state?.scopes;
    if (scopes === undefined) {
      this.view.message = undefined;
    } else if (scopes === null) {
      this.view.message = 'The assistant can access the whole project. Check folders to limit it to just those.';
    } else if (scopes.length === 0) {
      this.view.message = 'The assistant has no access to any files. Check folders, or use "Give Access to the Whole Project".';
    } else {
      this.view.message = `Access limited to ${scopes.length} item${scopes.length === 1 ? '' : 's'}.`;
    }
  }

  dispose(): void {
    this.view.dispose();
    this.changed.dispose();
  }
}
