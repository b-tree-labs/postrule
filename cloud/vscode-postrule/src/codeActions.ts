// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

/**
 * PostruleCodeActionProvider — for any function that the analyzer flagged as
 * non-auto-liftable, offers a single QuickFix titled
 * "Postrule: run auto-lift here". Invoking it spawns
 * `postrule init <file>:<func> --author @you:team --auto-lift`
 * in a VS Code terminal, then re-scans the file.
 */

import * as vscode from "vscode";
import { PostruleDiagnosticsProvider } from "./diagnostics";
import { ClassificationSite } from "./postruleClient";

export const RUN_AUTO_LIFT_COMMAND = "postrule.aiAutoLift";

export interface RunAutoLiftArgs {
  file: string;
  functionName: string;
}

export class PostruleCodeActionProvider implements vscode.CodeActionProvider {
  public static readonly providedCodeActionKinds = [vscode.CodeActionKind.QuickFix];

  constructor(private readonly diagnostics: PostruleDiagnosticsProvider) {}

  provideCodeActions(
    document: vscode.TextDocument,
    range: vscode.Range | vscode.Selection,
  ): vscode.ProviderResult<vscode.CodeAction[]> {
    const report = this.diagnostics.getReport(document.uri);
    if (!report) {
      return [];
    }
    const actions: vscode.CodeAction[] = [];
    const seen = new Set<string>();
    for (const site of report.sites) {
      if (site.lift_status === "auto_liftable") {
        continue;
      }
      if (!siteIntersectsRange(site, range)) {
        continue;
      }
      if (seen.has(site.function_name)) {
        continue;
      }
      seen.add(site.function_name);
      actions.push(buildAutoLiftAction(document, site));
    }
    return actions;
  }
}

export function buildAutoLiftAction(
  document: vscode.TextDocument,
  site: ClassificationSite,
): vscode.CodeAction {
  const action = new vscode.CodeAction("Postrule: run auto-lift here", vscode.CodeActionKind.QuickFix);
  const args: RunAutoLiftArgs = {
    file: document.uri.fsPath,
    functionName: site.function_name,
  };
  action.command = {
    title: "Postrule: run auto-lift here",
    command: RUN_AUTO_LIFT_COMMAND,
    arguments: [args],
  };
  return action;
}

function siteIntersectsRange(site: ClassificationSite, range: vscode.Range): boolean {
  const startLine = Math.max(0, site.line_start - 1);
  const endLine = Math.max(startLine, site.line_end - 1);
  // Range overlap, half-open lines: site covers [startLine, endLine].
  return range.start.line <= endLine && range.end.line >= startLine;
}
