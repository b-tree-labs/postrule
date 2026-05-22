// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

/**
 * Unit tests for the diagnostic-publishing logic. The postrule CLI is mocked.
 *
 * Coverage:
 *   - buildDiagnostics maps "error" hazards to DiagnosticSeverity.Error.
 *   - buildDiagnostics maps "warn" hazards to DiagnosticSeverity.Warning.
 *   - auto_liftable sites produce zero diagnostics.
 *   - PostruleDiagnosticsProvider warns once when the CLI is missing.
 *   - PostruleDiagnosticsProvider publishes via its DiagnosticCollection.
 */

import * as assert from "assert";
import * as path from "path";
import * as vscode from "vscode";
import { buildDiagnostics, PostruleDiagnosticsProvider, DIAGNOSTIC_SOURCE } from "../../diagnostics";
import { AnalyzerReport, CliRunner, PostruleCliError } from "../../postruleClient";

function fakeReport(filePath: string): AnalyzerReport {
  return {
    root: path.dirname(filePath),
    files_scanned: 1,
    total_sites: 2,
    errors: [],
    sites: [
      {
        file_path: filePath,
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
            reason: "api.charge(req) is bound to a name then branched on.",
            suggested_fix: "Pull the side-effect call out before the if.",
            severity: "error",
          },
        ],
      },
      {
        file_path: filePath,
        function_name: "triage",
        line_start: 4,
        line_end: 8,
        pattern: "P1",
        labels: ["bug", "feature", "other"],
        label_cardinality: 3,
        regime: "narrow",
        fit_score: 4.5,
        lift_status: "auto_liftable",
        hazards: [],
      },
    ],
  };
}

class FakeCli implements CliRunner {
  public calls: string[] = [];
  constructor(private report: AnalyzerReport | PostruleCliError) {}
  binaryPath(): string {
    return "fake-postrule";
  }
  async analyze(filePath: string): Promise<AnalyzerReport> {
    this.calls.push(filePath);
    if (this.report instanceof PostruleCliError) {
      throw this.report;
    }
    return this.report;
  }
}

async function openPython(content: string, name: string): Promise<vscode.TextDocument> {
  const doc = await vscode.workspace.openTextDocument({ language: "python", content });
  // The synthesized URI doesn't have an fsPath that matches name, but for
  // these unit tests buildDiagnostics is what we care about.
  void name;
  return doc;
}

suite("buildDiagnostics", () => {
  test("maps error severity to DiagnosticSeverity.Error", async () => {
    const doc = await openPython(
      [
        '"""f"""',
        "",
        "",
        "def triage(text):",
        "    return 'x'",
        "",
        "",
        "def maybe_charge(req):",
        "    response = api.charge(req)",
        "    if response.ok:",
        "        return 'charged'",
        "    return 'skipped'",
      ].join("\n"),
      "fixture.py",
    );
    const report = fakeReport(doc.uri.fsPath);
    const diags = buildDiagnostics(report, doc);
    assert.strictEqual(diags.length, 1, "should emit exactly one diagnostic");
    assert.strictEqual(diags[0].severity, vscode.DiagnosticSeverity.Error);
    assert.strictEqual(diags[0].source, DIAGNOSTIC_SOURCE);
    assert.strictEqual(diags[0].code, "side_effect_evidence");
    assert.ok(diags[0].message.includes("api.charge"));
    assert.ok(diags[0].message.includes("Suggested fix"));
  });

  test("maps warn severity to DiagnosticSeverity.Warning", async () => {
    const doc = await openPython("def f(x):\n    return 'x'\n", "warn.py");
    const report: AnalyzerReport = {
      root: ".",
      files_scanned: 1,
      total_sites: 1,
      errors: [],
      sites: [
        {
          file_path: doc.uri.fsPath,
          function_name: "f",
          line_start: 1,
          line_end: 2,
          pattern: "P1",
          labels: [],
          label_cardinality: 0,
          regime: "narrow",
          fit_score: 2.0,
          lift_status: "needs_annotation",
          hazards: [
            {
              category: "multi_arg_no_annotation",
              line: 1,
              reason: "f takes multiple args without type hints",
              suggested_fix: "Add annotations.",
              severity: "warn",
            },
          ],
        },
      ],
    };
    const diags = buildDiagnostics(report, doc);
    assert.strictEqual(diags.length, 1);
    assert.strictEqual(diags[0].severity, vscode.DiagnosticSeverity.Warning);
  });

  test("auto_liftable sites yield zero diagnostics", async () => {
    const doc = await openPython("def triage(t):\n    return 'x'\n", "clean.py");
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
          line_end: 2,
          pattern: "P1",
          labels: ["x"],
          label_cardinality: 1,
          regime: "narrow",
          fit_score: 5.0,
          lift_status: "auto_liftable",
          hazards: [],
        },
      ],
    };
    const diags = buildDiagnostics(report, doc);
    assert.strictEqual(diags.length, 0);
  });

  test("range covers line_start..line_end (1-based -> 0-based)", async () => {
    const lines = ["", "", "", "def f(x):", "    return 'a'", "    return 'b'"];
    const doc = await openPython(lines.join("\n"), "range.py");
    const report: AnalyzerReport = {
      root: ".",
      files_scanned: 1,
      total_sites: 1,
      errors: [],
      sites: [
        {
          file_path: doc.uri.fsPath,
          function_name: "f",
          line_start: 4,
          line_end: 6,
          pattern: "P1",
          labels: ["a", "b"],
          label_cardinality: 2,
          regime: "narrow",
          fit_score: 3.0,
          lift_status: "refused",
          hazards: [
            {
              category: "x",
              line: 4,
              reason: "r",
              suggested_fix: "s",
              severity: "error",
            },
          ],
        },
      ],
    };
    const diags = buildDiagnostics(report, doc);
    assert.strictEqual(diags.length, 1);
    assert.strictEqual(diags[0].range.start.line, 3);
    assert.strictEqual(diags[0].range.end.line, 5);
  });
});

suite("PostruleDiagnosticsProvider", () => {
  test("publishes diagnostics into its collection on refresh", async () => {
    const doc = await openPython(
      [
        "def maybe_charge(req):",
        "    response = api.charge(req)",
        "    if response.ok:",
        "        return 'charged'",
        "    return 'skipped'",
      ].join("\n"),
      "publish.py",
    );
    const report = fakeReport(doc.uri.fsPath);
    // Adjust fake report's only-refused site to match line range of doc.
    report.sites[0].line_start = 1;
    report.sites[0].line_end = 5;
    report.sites = report.sites.filter((s) => s.lift_status !== "auto_liftable");
    const cli = new FakeCli(report);
    const provider = new PostruleDiagnosticsProvider(cli);
    await provider.refresh(doc);
    const diags = provider.collection.get(doc.uri) ?? [];
    assert.strictEqual(diags.length, 1);
    assert.strictEqual(diags[0].severity, vscode.DiagnosticSeverity.Error);
    provider.dispose();
  });

  test("non-python documents are ignored", async () => {
    const doc = await vscode.workspace.openTextDocument({ language: "plaintext", content: "hi" });
    const cli = new FakeCli(fakeReport(doc.uri.fsPath));
    const provider = new PostruleDiagnosticsProvider(cli);
    await provider.refresh(doc);
    assert.strictEqual(cli.calls.length, 0);
    provider.dispose();
  });

  test("CLI not_found degrades gracefully (no diagnostics, no throw)", async () => {
    const doc = await openPython("def f(x):\n    return 'x'\n", "missing.py");
    const cli = new FakeCli(new PostruleCliError("not_found", "binary missing"));
    const provider = new PostruleDiagnosticsProvider(cli);
    await provider.refresh(doc); // must not throw
    const diags = provider.collection.get(doc.uri) ?? [];
    assert.strictEqual(diags.length, 0);
    provider.dispose();
  });
});
