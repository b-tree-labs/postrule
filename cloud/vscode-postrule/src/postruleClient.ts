// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

/**
 * Thin wrapper over the postrule CLI. We invoke `postrule analyze --json <path>`
 * and parse the resulting AnalyzerReport. The CLI binary path is resolved
 * from the `postrule.binaryPath` config (default: "postrule" on PATH).
 *
 * No LSP. v1.1 stays small: child_process invocations, JSON parsing, done.
 */

import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import * as vscode from "vscode";

export interface Hazard {
  category: string;
  line: number;
  reason: string;
  suggested_fix: string;
  severity: string; // "warn" | "error"
}

export interface ClassificationSite {
  file_path: string;
  function_name: string;
  line_start: number;
  line_end: number;
  pattern: string;
  labels: string[];
  label_cardinality: number;
  regime: string;
  fit_score: number;
  hazards: Hazard[];
  lift_status: string; // "auto_liftable" | "needs_annotation" | "refused"
}

export interface AnalyzerReport {
  root: string;
  files_scanned: number;
  total_sites: number;
  sites: ClassificationSite[];
  errors: string[];
}

export class PostruleCliError extends Error {
  public readonly code: "not_found" | "exec_error" | "parse_error";
  constructor(code: "not_found" | "exec_error" | "parse_error", message: string) {
    super(message);
    this.code = code;
  }
}

export interface CliRunner {
  analyze(filePath: string): Promise<AnalyzerReport>;
  binaryPath(): string;
}

/**
 * Real CliRunner. Spawns `postrule analyze --json <path>` and parses stdout.
 */
export class PostruleCli implements CliRunner {
  private readonly resolveBinary: () => string;
  private readonly spawnImpl: typeof spawn;

  constructor(resolveBinary?: () => string, spawnImpl?: typeof spawn) {
    this.resolveBinary =
      resolveBinary ??
      (() => {
        const cfg = vscode.workspace.getConfiguration("postrule");
        return cfg.get<string>("binaryPath", "postrule");
      });
    this.spawnImpl = spawnImpl ?? spawn;
  }

  binaryPath(): string {
    return this.resolveBinary();
  }

  analyze(filePath: string): Promise<AnalyzerReport> {
    return new Promise((resolve, reject) => {
      const bin = this.resolveBinary();
      let proc: ChildProcessWithoutNullStreams;
      try {
        proc = this.spawnImpl(bin, ["analyze", "--json", filePath]) as ChildProcessWithoutNullStreams;
      } catch (e) {
        return reject(new PostruleCliError("not_found", `Failed to spawn ${bin}: ${(e as Error).message}`));
      }

      let stdout = "";
      let stderr = "";

      proc.stdout.on("data", (chunk) => {
        stdout += chunk.toString();
      });
      proc.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
      });

      proc.on("error", (err: NodeJS.ErrnoException) => {
        if (err.code === "ENOENT") {
          reject(new PostruleCliError("not_found", `postrule binary not found at '${bin}'`));
        } else {
          reject(new PostruleCliError("exec_error", `postrule failed to start: ${err.message}`));
        }
      });

      proc.on("close", (exitCode) => {
        if (exitCode !== 0) {
          reject(
            new PostruleCliError(
              "exec_error",
              `postrule exited with code ${exitCode}: ${stderr.trim() || stdout.trim()}`,
            ),
          );
          return;
        }
        try {
          const parsed = JSON.parse(stdout) as AnalyzerReport;
          resolve(parsed);
        } catch (e) {
          reject(new PostruleCliError("parse_error", `Could not parse postrule JSON: ${(e as Error).message}`));
        }
      });
    });
  }
}
