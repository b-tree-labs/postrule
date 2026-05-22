// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

/**
 * Unit tests for the CodeActionProvider.
 *
 * Coverage:
 *   - A function with hazards yields exactly one "Postrule: run auto-lift here"
 *     CodeAction.
 *   - A clean function yields zero CodeActions.
 *   - The CodeAction's command targets postrule.aiAutoLift with the right args.
 */

import * as assert from "assert";
import * as vscode from "vscode";
import { PostruleDiagnosticsProvider } from "../../diagnostics";
import { PostruleCodeActionProvider, RUN_AUTO_LIFT_COMMAND, RunAutoLiftArgs } from "../../codeActions";
import { AnalyzerReport, CliRunner } from "../../postruleClient";

class FakeCli implements CliRunner {
  constructor(private report: AnalyzerReport) {}
  binaryPath(): string {
    return "fake-postrule";
  }
  async analyze(): Promise<AnalyzerReport> {
    return this.report;
  }
}

async function openPython(content: string): Promise<vscode.TextDocument> {
  return vscode.workspace.openTextDocument({ language: "python", content });
}

suite("PostruleCodeActionProvider", () => {
  test("yields one auto-lift action for a function with hazards", async () => {
    const doc = await openPython(
      [
        "def maybe_charge(req):",
        "    response = api.charge(req)",
        "    if response.ok:",
        "        return 'charged'",
        "    return 'skipped'",
      ].join("\n"),
    );
    const report: AnalyzerReport = {
      root: ".",
      files_scanned: 1,
      total_sites: 1,
      errors: [],
      sites: [
        {
          file_path: doc.uri.fsPath,
          function_name: "maybe_charge",
          line_start: 1,
          line_end: 5,
          pattern: "P1",
          labels: [],
          label_cardinality: 0,
          regime: "narrow",
          fit_score: 3.0,
          lift_status: "refused",
          hazards: [
            {
              category: "side_effect_evidence",
              line: 2,
              reason: "side-effect",
              suggested_fix: "extract",
              severity: "error",
            },
          ],
        },
      ],
    };
    const provider = new PostruleDiagnosticsProvider(new FakeCli(report));
    await provider.refresh(doc);
    const actions = new PostruleCodeActionProvider(provider).provideCodeActions(
      doc,
      new vscode.Range(0, 0, 4, 0),
    ) as vscode.CodeAction[];
    assert.strictEqual(actions.length, 1);
    assert.strictEqual(actions[0].title, "Postrule: run auto-lift here");
    assert.strictEqual(actions[0].kind?.value, vscode.CodeActionKind.QuickFix.value);
    assert.ok(actions[0].command);
    assert.strictEqual(actions[0].command!.command, RUN_AUTO_LIFT_COMMAND);
    const args = actions[0].command!.arguments![0] as RunAutoLiftArgs;
    assert.strictEqual(args.functionName, "maybe_charge");
    assert.strictEqual(args.file, doc.uri.fsPath);
    provider.dispose();
  });

  test("yields zero actions for a clean function", async () => {
    const doc = await openPython(
      ["def triage(t):", "    if 'bug' in t:", "        return 'bug'", "    return 'other'"].join("\n"),
    );
    const report: AnalyzerReport = {
      root: ".",
      files_scanned: 1,
      total_sites: 1,
      errors: [],
      sites: [
        {
          file_path: doc.uri.fsPath,
          function_name: "triage",
          line_start: 1,
          line_end: 4,
          pattern: "P1",
          labels: ["bug", "other"],
          label_cardinality: 2,
          regime: "narrow",
          fit_score: 5.0,
          lift_status: "auto_liftable",
          hazards: [],
        },
      ],
    };
    const provider = new PostruleDiagnosticsProvider(new FakeCli(report));
    await provider.refresh(doc);
    const actions = new PostruleCodeActionProvider(provider).provideCodeActions(
      doc,
      new vscode.Range(0, 0, 3, 0),
    ) as vscode.CodeAction[];
    assert.strictEqual(actions.length, 0);
    provider.dispose();
  });

  test("yields zero actions when cursor is outside any hazardous site", async () => {
    const doc = await openPython(
      [
        "def clean(t):",
        "    return 'x'",
        "",
        "def maybe_charge(req):",
        "    response = api.charge(req)",
        "    if response.ok:",
        "        return 'charged'",
        "    return 'skipped'",
      ].join("\n"),
    );
    const report: AnalyzerReport = {
      root: ".",
      files_scanned: 1,
      total_sites: 2,
      errors: [],
      sites: [
        {
          file_path: doc.uri.fsPath,
          function_name: "clean",
          line_start: 1,
          line_end: 2,
          pattern: "P1",
          labels: ["x"],
          label_cardinality: 1,
          regime: "narrow",
          fit_score: 5.0,
          lift_status: "auto_liftable",
          hazards: [],
        },
        {
          file_path: doc.uri.fsPath,
          function_name: "maybe_charge",
          line_start: 4,
          line_end: 8,
          pattern: "P1",
          labels: [],
          label_cardinality: 0,
          regime: "narrow",
          fit_score: 3.0,
          lift_status: "refused",
          hazards: [
            {
              category: "side_effect_evidence",
              line: 5,
              reason: "side-effect",
              suggested_fix: "extract",
              severity: "error",
            },
          ],
        },
      ],
    };
    const provider = new PostruleDiagnosticsProvider(new FakeCli(report));
    await provider.refresh(doc);
    // Cursor is on line 0 (the clean function), not the hazardous one.
    const actions = new PostruleCodeActionProvider(provider).provideCodeActions(
      doc,
      new vscode.Range(0, 0, 0, 0),
    ) as vscode.CodeAction[];
    assert.strictEqual(actions.length, 0);
    provider.dispose();
  });
});
