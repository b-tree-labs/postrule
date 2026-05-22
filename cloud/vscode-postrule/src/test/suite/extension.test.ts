// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: Apache-2.0

/**
 * Integration test: load the extension into a test VS Code instance, open
 * a fixture Python file with the side_effect_evidence hazard pattern, and
 * confirm a diagnostic is published with the expected severity and range.
 *
 * The fixture mirrors test_charge_then_branch_is_refused_with_specific_diagnostic
 * from tests/test_analyzer_hazards.py.
 *
 * Note: this test does NOT shell out to the real postrule CLI. We construct
 * the analyzer report ourselves and feed it through PostruleDiagnosticsProvider
 * directly, which is enough to prove the integration wiring works inside
 * a real vscode runtime (DiagnosticCollection, language filter, etc.).
 */

import * as assert from "assert";
import * as path from "path";
import * as vscode from "vscode";
import { PostruleDiagnosticsProvider, DIAGNOSTIC_SOURCE } from "../../diagnostics";
import { AnalyzerReport, CliRunner } from "../../postruleClient";

class StaticCli implements CliRunner {
  constructor(private report: AnalyzerReport) {}
  binaryPath(): string {
    return "fake-postrule";
  }
  async analyze(): Promise<AnalyzerReport> {
    return this.report;
  }
}

suite("Extension integration", () => {
  test("publishes a diagnostic on the refused fixture", async () => {
    const fixturePath = path.resolve(__dirname, "../../../src/test/fixtures/refused.py");
    const doc = await vscode.workspace.openTextDocument(fixturePath);
    await vscode.window.showTextDocument(doc);
    assert.strictEqual(doc.languageId, "python");

    const report: AnalyzerReport = {
      root: path.dirname(fixturePath),
      files_scanned: 1,
      total_sites: 1,
      errors: [],
      sites: [
        {
          file_path: fixturePath,
          function_name: "maybe_charge",
          line_start: 7,
          line_end: 11,
          pattern: "P1",
          labels: ["charged", "skipped"],
          label_cardinality: 2,
          regime: "narrow",
          fit_score: 3.0,
          lift_status: "refused",
          hazards: [
            {
              category: "side_effect_evidence",
              line: 8,
              reason: "api.charge(req) is bound then branched on.",
              suggested_fix: "Pull the side-effect out before the if.",
              severity: "error",
            },
          ],
        },
      ],
    };
    const provider = new PostruleDiagnosticsProvider(new StaticCli(report));
    await provider.refresh(doc);

    const diags = provider.collection.get(doc.uri) ?? [];
    assert.strictEqual(diags.length, 1, "expected exactly one diagnostic");
    const d = diags[0];
    assert.strictEqual(d.severity, vscode.DiagnosticSeverity.Error);
    assert.strictEqual(d.source, DIAGNOSTIC_SOURCE);
    assert.strictEqual(d.code, "side_effect_evidence");
    // Range covers function body. line_start=7 -> 0-based 6.
    assert.strictEqual(d.range.start.line, 6);
    assert.strictEqual(d.range.end.line, 10);
    provider.dispose();
  });

  test("clean fixture yields zero diagnostics", async () => {
    const fixturePath = path.resolve(__dirname, "../../../src/test/fixtures/clean.py");
    const doc = await vscode.workspace.openTextDocument(fixturePath);
    await vscode.window.showTextDocument(doc);
    const report: AnalyzerReport = {
      root: path.dirname(fixturePath),
      files_scanned: 1,
      total_sites: 1,
      errors: [],
      sites: [
        {
          file_path: fixturePath,
          function_name: "triage",
          line_start: 4,
          line_end: 9,
          pattern: "P1",
          labels: ["bug", "feature", "other"],
          label_cardinality: 3,
          regime: "narrow",
          fit_score: 5.0,
          lift_status: "auto_liftable",
          hazards: [],
        },
      ],
    };
    const provider = new PostruleDiagnosticsProvider(new StaticCli(report));
    await provider.refresh(doc);
    const diags = provider.collection.get(doc.uri) ?? [];
    assert.strictEqual(diags.length, 0);
    provider.dispose();
  });

  test("postrule.rescanWorkspace command is registered", async () => {
    const all = await vscode.commands.getCommands(true);
    assert.ok(all.includes("postrule.rescanWorkspace"), "postrule.rescanWorkspace should be registered");
    assert.ok(all.includes("postrule.aiAutoLift"), "postrule.aiAutoLift should be registered");
  });
});
