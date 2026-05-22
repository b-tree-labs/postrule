// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

/**
 * PostruleDiagnosticsProvider walks open Python documents, asks the postrule
 * CLI to analyze them, and publishes one vscode.Diagnostic per hazard
 * (or one summary diagnostic per non-auto-liftable site).
 *
 * Severity mapping:
 *   hazard.severity === "error" -> DiagnosticSeverity.Error
 *   hazard.severity === "warn"  -> DiagnosticSeverity.Warning
 *
 * The diagnostic Range covers `line_start`..`line_end` (1-based from the
 * analyzer; we convert to 0-based for vscode).
 */

import * as vscode from "vscode";
import { CliRunner, ClassificationSite, PostruleCliError, Hazard, AnalyzerReport } from "./postruleClient";

export const DIAGNOSTIC_SOURCE = "postrule";

/** Pure: convert an AnalyzerReport into vscode.Diagnostic[] for a doc. */
export function buildDiagnostics(report: AnalyzerReport, doc: vscode.TextDocument): vscode.Diagnostic[] {
  const out: vscode.Diagnostic[] = [];

  for (const site of report.sites) {
    if (site.lift_status === "auto_liftable") {
      continue;
    }
    if (site.hazards.length === 0) {
      // Defensive: a non-auto-liftable site should always carry at least
      // one hazard. If it doesn't, surface a generic warning.
      out.push(siteSummaryDiagnostic(site, doc));
      continue;
    }
    for (const hazard of site.hazards) {
      out.push(hazardDiagnostic(site, hazard, doc));
    }
  }
  return out;
}

function hazardDiagnostic(
  site: ClassificationSite,
  hazard: Hazard,
  doc: vscode.TextDocument,
): vscode.Diagnostic {
  const range = functionRange(site, doc);
  const message =
    `${site.function_name}: ${hazard.reason}` +
    (hazard.suggested_fix ? `\nSuggested fix: ${hazard.suggested_fix}` : "");
  const severity =
    hazard.severity === "error" ? vscode.DiagnosticSeverity.Error : vscode.DiagnosticSeverity.Warning;
  const diag = new vscode.Diagnostic(range, message, severity);
  diag.source = DIAGNOSTIC_SOURCE;
  diag.code = hazard.category;
  return diag;
}

function siteSummaryDiagnostic(site: ClassificationSite, doc: vscode.TextDocument): vscode.Diagnostic {
  const range = functionRange(site, doc);
  const diag = new vscode.Diagnostic(
    range,
    `${site.function_name}: lift_status=${site.lift_status} (no detailed hazard reported)`,
    vscode.DiagnosticSeverity.Warning,
  );
  diag.source = DIAGNOSTIC_SOURCE;
  diag.code = site.lift_status;
  return diag;
}

function functionRange(site: ClassificationSite, doc: vscode.TextDocument): vscode.Range {
  const startLine = Math.max(0, site.line_start - 1);
  const endLine = Math.max(startLine, site.line_end - 1);
  const safeEndLine = Math.min(endLine, Math.max(0, doc.lineCount - 1));
  const endChar = doc.lineAt(safeEndLine).text.length;
  return new vscode.Range(new vscode.Position(startLine, 0), new vscode.Position(safeEndLine, endChar));
}

/**
 * PostruleDiagnosticsProvider — owns a DiagnosticCollection, runs the CLI on
 * Python documents, publishes diagnostics. Degrades gracefully if the CLI
 * is missing.
 */
export class PostruleDiagnosticsProvider {
  public readonly collection: vscode.DiagnosticCollection;
  private readonly cli: CliRunner;
  private cliMissingWarned = false;
  /** Most-recent report per file, exposed for the code action provider. */
  private readonly reportCache = new Map<string, AnalyzerReport>();

  constructor(cli: CliRunner, collection?: vscode.DiagnosticCollection) {
    this.cli = cli;
    this.collection = collection ?? vscode.languages.createDiagnosticCollection(DIAGNOSTIC_SOURCE);
  }

  /** Run analyze on a document and publish its diagnostics. */
  async refresh(doc: vscode.TextDocument): Promise<void> {
    if (doc.languageId !== "python") {
      return;
    }
    const cfg = vscode.workspace.getConfiguration("postrule");
    if (!cfg.get<boolean>("enable", true)) {
      this.collection.delete(doc.uri);
      return;
    }
    const filePath = doc.uri.fsPath;
    let report: AnalyzerReport;
    try {
      report = await this.cli.analyze(filePath);
    } catch (e) {
      if (e instanceof PostruleCliError && e.code === "not_found") {
        this.warnCliMissing();
        this.collection.delete(doc.uri);
        return;
      }
      // exec_error / parse_error: clear stale diagnostics, log to console.
      // Don't spam the user with a popup on every keystroke.
      console.warn(`[postrule] analyze failed for ${filePath}: ${(e as Error).message}`);
      this.collection.delete(doc.uri);
      return;
    }
    this.reportCache.set(doc.uri.toString(), report);
    const diagnostics = buildDiagnostics(report, doc);
    this.collection.set(doc.uri, diagnostics);
  }

  /** Refresh every open Python document. */
  async refreshAll(): Promise<void> {
    const docs = vscode.workspace.textDocuments.filter((d) => d.languageId === "python");
    await Promise.all(docs.map((d) => this.refresh(d)));
  }

  /** Drop stored diagnostics for a closed file. */
  clear(uri: vscode.Uri): void {
    this.collection.delete(uri);
    this.reportCache.delete(uri.toString());
  }

  /** Look up the cached report for a doc — code actions need this. */
  getReport(uri: vscode.Uri): AnalyzerReport | undefined {
    return this.reportCache.get(uri.toString());
  }

  dispose(): void {
    this.collection.dispose();
    this.reportCache.clear();
  }

  private warnCliMissing(): void {
    if (this.cliMissingWarned) {
      return;
    }
    this.cliMissingWarned = true;
    void vscode.window.showWarningMessage(
      "Postrule CLI not found on PATH; run `pip install postrule` or set postrule.binaryPath.",
    );
  }
}
