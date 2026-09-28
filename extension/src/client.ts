import * as cp from 'child_process';
import * as path from 'path';
import * as readline from 'readline';
import * as vscode from 'vscode';

export interface HarnessMessage {
  type: string;
  [key: string]: any;
}

export interface HarnessState {
  root: string;
  model: string;
  coder: string;
  think: boolean;
  auto_edits: boolean;
  scopes: string[] | null;
  busy: boolean;
}

export function toWslPath(windowsPath: string): string {
  const match = /^([A-Za-z]):[\\/](.*)$/.exec(windowsPath);
  if (!match) {
    return windowsPath.replace(/\\/g, '/');
  }
  return `/mnt/${match[1].toLowerCase()}/${match[2].replace(/\\/g, '/')}`;
}

export class HarnessClient implements vscode.Disposable {
  private process: cp.ChildProcessWithoutNullStreams | undefined;
  private nextId = 1;
  private pending = new Map<number, (message: HarnessMessage) => void>();
  private emitter = new vscode.EventEmitter<HarnessMessage>();
  readonly onMessage = this.emitter.event;
  state: HarnessState | undefined;

  constructor(
    private readonly extensionPath: string,
    private readonly root: string,
    private readonly log: vscode.OutputChannel,
  ) {}

  get running(): boolean {
    return this.process !== undefined;
  }

  start(): void {
    if (this.process) {
      return;
    }
    const config = vscode.workspace.getConfiguration('lh');
    const python = config.get<string>('pythonPath', 'python3');
    const useWsl = process.platform === 'win32' && config.get<boolean>('useWsl', true);
    const hostPath = (p: string) => (useWsl ? toWslPath(p) : p);
    const harnessArgs = [
      '-m', 'harness', '--server', hostPath(this.root),
      '--model', config.get<string>('assistantModel', 'qwen3:8b'),
      '--coder', config.get<string>('coderModel', 'qwen2.5-coder:14b'),
      '--ctx', String(config.get<number>('contextSize', 16384)),
    ];
    const harnessEnv: Record<string, string> = {
      PYTHONPATH: hostPath(path.join(this.extensionPath, 'python')),
      OLLAMA_API_BASE: config.get<string>('ollamaUrl', 'http://127.0.0.1:11434'),
      PYTHONUNBUFFERED: '1',
      PYTHONIOENCODING: 'utf-8',
    };
    const command = useWsl ? 'wsl.exe' : python;
    const args = useWsl
      ? ['-e', 'env', ...Object.entries(harnessEnv).map(([key, value]) => `${key}=${value}`), python, ...harnessArgs]
      : harnessArgs;
    this.log.appendLine(`Starting: ${command} ${args.join(' ')}`);
    const child = cp.spawn(command, args, { cwd: this.root, env: { ...process.env, ...harnessEnv } });
    this.process = child;

    readline.createInterface({ input: child.stdout }).on('line', (line) => this.receive(line));
    child.stderr.on('data', (data) => this.log.append(data.toString()));
    child.on('error', (error) => {
      this.emitter.fire({
        type: 'error',
        message: `Could not start the harness with "${python}": ${error.message}. Check the lh.pythonPath setting.`,
      });
    });
    child.on('exit', (code) => {
      if (this.process !== child) {
        return;
      }
      this.process = undefined;
      this.state = undefined;
      for (const resolve of this.pending.values()) {
        resolve({ type: 'response', error: 'The harness stopped.' });
      }
      this.pending.clear();
      this.log.appendLine(`Harness exited with code ${code}`);
      this.emitter.fire({ type: 'exit', code });
    });
  }

  send(message: HarnessMessage): void {
    this.start();
    this.process?.stdin.write(JSON.stringify(message) + '\n');
  }

  request(message: HarnessMessage): Promise<HarnessMessage> {
    const id = this.nextId++;
    return new Promise((resolve) => {
      this.pending.set(id, resolve);
      this.send({ ...message, id });
    });
  }

  restart(): void {
    this.stop();
    this.start();
  }

  stop(): void {
    const child = this.process;
    this.process = undefined;
    this.state = undefined;
    child?.kill();
  }

  dispose(): void {
    this.stop();
    this.emitter.dispose();
  }

  private receive(line: string): void {
    let message: HarnessMessage;
    try {
      message = JSON.parse(line);
    } catch {
      this.log.appendLine(`Unparseable output: ${line}`);
      return;
    }
    if (message.type === 'response') {
      const resolve = this.pending.get(message.id);
      this.pending.delete(message.id);
      resolve?.(message);
      return;
    }
    if (message.type === 'ready' || message.type === 'state') {
      this.state = message as unknown as HarnessState;
    }
    this.emitter.fire(message);
  }
}
