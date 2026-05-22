// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: Apache-2.0

/**
 * Boots a VS Code test instance and points it at our suite under
 * `out/test/suite`.
 */

import * as path from "path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  try {
    const extensionDevelopmentPath = path.resolve(__dirname, "../../");
    const extensionTestsPath = path.resolve(__dirname, "./suite/index");
    const fixturesPath = path.resolve(__dirname, "../../src/test/fixtures");

    await runTests({
      extensionDevelopmentPath,
      extensionTestsPath,
      launchArgs: [fixturesPath, "--disable-extensions"],
    });
  } catch (err) {
    console.error("Failed to run tests", err);
    process.exit(1);
  }
}

void main();
