// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: Apache-2.0

/**
 * Postrule VS Code extension entry point.
 *
 * On activate:
 *   1. Wire a PostruleDiagnosticsProvider for Python documents.
 *   2. Register a CodeActionProvider that exposes the auto-lift quick fix.
 *   3. Register the `postrule.rescanWorkspace` and `postrule.aiAutoLift` commands.
 *
 * v1.1 deliberately avoids LSP. The CLI is fast enough that a debounced
 * re-scan on save / open is fine, and it keeps the extension small.
 */

import * as vscode from "vscode";
import { PostruleCli } from "./postruleClient";
import { PostruleDiagnosticsProvider } from "./diagnostics";
import {
  PostruleCodeActionProvider,
  RUN_AUTO_LIFT_COMMAND,
  RunAutoLiftArgs,
} from "./codeActions";

let diagnosticsProvider: PostruleDiagnosticsProvider | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const cli = new PostruleCli();
  const provider = new PostruleDiagnosticsProvider(cli);
  diagnosticsProvider = provider;
  context.subscriptions.push({ dispose: () => provider.dispose() });

  // Open + save -> refresh; close -> clear.
  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument((doc) => {
      void provider.refresh(doc);
    }),
    vscode.workspace.onDidSaveTextDocument((doc) => {
      void provider.refresh(doc);
    }),
    vscode.workspace.onDidCloseTextDocument((doc) => {
      provider.clear(doc.uri);
    }),
  );

  // Initial scan of already-open docs.
  void provider.refreshAll();

  // Code action provider — only for python.
  context.subscriptions.push(
    vscode.languages.registerCodeActionsProvider(
      { language: "python" },
      new PostruleCodeActionProvider(provider),
      { providedCodeActionKinds: PostruleCodeActionProvider.providedCodeActionKinds },
    ),
  );

  // Commands.
  context.subscriptions.push(
    vscode.commands.registerCommand("postrule.rescanWorkspace", async () => {
      await provider.refreshAll();
    }),
    vscode.commands.registerCommand(RUN_AUTO_LIFT_COMMAND, async (args: RunAutoLiftArgs) => {
      await runAutoLift(args, cli.binaryPath(), provider);
    }),
  );
}

export function deactivate(): void {
  diagnosticsProvider?.dispose();
  diagnosticsProvider = undefined;
}

/**
 * Spawn `postrule init <file>:<func> --author @you:team --auto-lift` in a
 * dedicated terminal, then queue a re-scan of the file once the user has
 * had a chance to inspect the output.
 */
export async function runAutoLift(
  args: RunAutoLiftArgs,
  binaryPath: string,
  provider: PostruleDiagnosticsProvider,
): Promise<void> {
  const target = `${args.file}:${args.functionName}`;
  const terminal = vscode.window.createTerminal({ name: "Postrule auto-lift" });
  terminal.show();
  // Quoting: target paths and identifiers are shell-safe in our tests, but
  // wrap defensively in single quotes in case a path has spaces.
  const quoted = target.includes(" ") ? `'${target.replace(/'/g, "'\\''")}'` : target;
  terminal.sendText(`${binaryPath} init ${quoted} --author @you:team --auto-lift`);

  // Re-scan after a short delay so the lifter has time to write changes.
  setTimeout(() => {
    const doc = vscode.workspace.textDocuments.find((d) => d.uri.fsPath === args.file);
    if (doc) {
      void provider.refresh(doc);
    } else {
      void provider.refreshAll();
    }
  }, 1500);
}
