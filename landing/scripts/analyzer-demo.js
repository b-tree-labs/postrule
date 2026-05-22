// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

// Analyzer demo — preset chips load pre-computed JSON; custom URLs
// trigger the Pyodide live path (loaded on demand). All math runs in
// the browser; no backend, no upload, no telemetry.
//
// Public surface:
//   - data-analyzer-demo  marker on the section root.
//   - data-preset="<slug>"          on chip buttons.
//   - data-results-target            for the rendered table.
//   - data-summary-target            for the headline summary line.
//   - data-savings-target            for the dollar-savings readout.
//   - data-roi-input="<calls|cost>"  on the two range sliders.

(function () {
  "use strict";

  const root = document.querySelector("[data-analyzer-demo]");
  if (!root) return;

  const summary = root.querySelector("[data-summary-target]");
  const tableBody = root.querySelector("[data-results-target]");
  const savingsAnnual = root.querySelector("[data-savings='annual']");
  const savingsDaily = root.querySelector("[data-savings='daily']");
  const sitesCount = root.querySelector("[data-savings='sites']");
  const callsInput = root.querySelector("[data-roi-input='calls']");
  const callsOutput = root.querySelector("[data-roi-input-output='calls']");
  const costInput = root.querySelector("[data-roi-input='cost']");
  const costOutput = root.querySelector("[data-roi-input-output='cost']");
  const customForm = root.querySelector("[data-custom-form]");
  const customInput = root.querySelector("[data-custom-input]");

  // Current dataset — replaced when a preset is clicked or a
  // custom URL is analyzed. Shape: { repo, files_scanned, total_sites, sites: [...] }
  let current = null;

  // Cap the rendered rows so a 118-site result doesn't blow the page.
  const ROW_CAP = 12;

  function fmtUSD(n) {
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
    if (n >= 1e3) return "$" + (n / 1e3).toFixed(1) + "k";
    return "$" + Math.round(n).toLocaleString();
  }

  function fmtNum(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(0) + "k";
    return String(Math.round(n));
  }

  function regimeBadge(regime) {
    const cls = "badge badge--" + regime;
    return `<span class="${cls}">${regime}</span>`;
  }

  function patternBadge(pattern) {
    return `<span class="badge badge--pattern">${pattern}</span>`;
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Render a code block. Two modes:
  //   showLineNumbers: true  → gutter with line numbers + diff markers
  //                            (Source / Postrule-wrapped tabs)
  //   showLineNumbers: false → plain text, no gutter (Sample log / Config)
  // Line numbers + diff markers are CSS pseudo-elements so the clipboard
  // copy is always the bare source.
  //
  //   text          string of code (newlines split into lines)
  //   opts.startLine        1-based start line (default 1; gutter mode only)
  //   opts.addedRanges      [[start, end], ...] inclusive 0-based line indices
  //                         to render with diff-added styling (gutter mode only)
  //   opts.showLineNumbers  default true
  function renderCodeBlock(text, opts) {
    opts = opts || {};
    const showLineNumbers = opts.showLineNumbers !== false;
    if (!showLineNumbers) {
      return `<div class="code-snippet-wrap" data-copy-target>
        <button type="button" class="copy-btn copy-btn--code" data-copy-source aria-label="Copy code" title="Copy"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="2" width="9" height="11" rx="1.5"/><path d="M3 5v8.5A1.5 1.5 0 0 0 4.5 15H11"/></svg></button>
        <pre class="code-snippet"><code>${escapeHtml(String(text))}</code></pre>
        <div class="code-resize-handle" data-resize-target role="separator" aria-orientation="horizontal" aria-label="Drag to resize" tabindex="0"><span class="code-resize-grip" aria-hidden="true"></span></div>
      </div>`;
    }
    const startLine = opts.startLine || 1;
    const addedRanges = opts.addedRanges || [];
    const isAdded = (idx) =>
      addedRanges.some(([s, e]) => idx >= s && idx <= e);
    const rawLines = String(text).split("\n");
    // Join with "" — spans are display:block so each line breaks
    // naturally. Joining with "\n" would inject literal newline text
    // nodes that render as extra blank lines in white-space:pre.
    // copy-buttons.js reconstructs newlines by walking .cs-line nodes.
    const lineHtml = rawLines
      .map((raw, idx) => {
        const added = isAdded(idx);
        const cls = added ? "cs-line cs-line--added" : "cs-line";
        const marker = added ? "+" : " ";
        // empty line still renders so counters advance correctly
        const visible = raw.length === 0 ? "&#8203;" : escapeHtml(raw);
        return `<span class="${cls}" data-marker="${marker}">${visible}</span>`;
      })
      .join("");
    const counterStart = Math.max(0, startLine - 1);
    return `<div class="code-snippet-wrap" data-copy-target>
      <button type="button" class="copy-btn copy-btn--code" data-copy-source aria-label="Copy code" title="Copy"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="2" width="9" height="11" rx="1.5"/><path d="M3 5v8.5A1.5 1.5 0 0 0 4.5 15H11"/></svg></button>
      <pre class="code-snippet code-snippet--gutter" style="counter-reset: codeline ${counterStart};"><code>${lineHtml}</code></pre>
      <div class="code-resize-handle" data-resize-target role="separator" aria-orientation="horizontal" aria-label="Drag to resize" tabindex="0"><span class="code-resize-grip" aria-hidden="true"></span></div>
    </div>`;
  }

  // True when a label value reads as a clean identifier-like string —
  // not multi-line prose, HTML markup, or template literals. The
  // analyzer extracts return-statement values so labels can include
  // arbitrary content; we filter to the ones safe to render in the
  // preview.
  function looksLikeReasonableLabel(l) {
    if (l == null) return false;
    const s = String(l);
    if (s.length === 0 || s.length > 24) return false;
    if (/[\n<>{}\\]/.test(s)) return false;
    return true;
  }

  // Pull the `(param: type, ...)` parameter list off the function
  // header in the source snippet. Returns an array of
  //   { name, annotation }   (annotation may be null)
  // in declaration order. Falls back to a single `input` param when
  // the snippet is missing or the def line can't be parsed; that
  // mirrors the existing `def fn(...)` placeholder behavior.
  function extractParams(site) {
    const fallback = [{ name: "input", annotation: null }];
    if (!site || !site.snippet) return fallback;
    const fn = site.function_name;
    const re = new RegExp(`def\\s+${fn}\\s*\\(([^)]*)\\)`);
    const m = site.snippet.match(re);
    if (!m) return fallback;
    const raw = m[1].trim();
    if (!raw) return fallback;
    const parts = [];
    let depth = 0;
    let buf = "";
    for (const ch of raw) {
      if (ch === "[" || ch === "(" || ch === "{") depth++;
      else if (ch === "]" || ch === ")" || ch === "}") depth--;
      if (ch === "," && depth === 0) {
        parts.push(buf.trim());
        buf = "";
      } else {
        buf += ch;
      }
    }
    if (buf.trim()) parts.push(buf.trim());
    const out = [];
    for (const p of parts) {
      // Strip default values: "x: int = 3" -> "x: int"
      const noDefault = p.split("=")[0].trim();
      // Skip `self` and `cls` (instance/class methods).
      if (noDefault === "self" || noDefault === "cls") continue;
      // Skip *args / **kwargs varieties — evidence methods need named
      // positional parameters.
      if (noDefault.startsWith("*")) continue;
      const colonIdx = noDefault.indexOf(":");
      if (colonIdx === -1) {
        out.push({ name: noDefault, annotation: null });
      } else {
        const name = noDefault.slice(0, colonIdx).trim();
        const ann = noDefault.slice(colonIdx + 1).trim();
        if (name) out.push({ name, annotation: ann || null });
      }
    }
    return out.length > 0 ? out : fallback;
  }

  // snake_case or camelCase function name -> PascalCase class stem.
  function classNameFor(fn) {
    if (!fn) return "Site";
    const cleaned = String(fn).replace(/[^A-Za-z0-9_]/g, "_");
    const parts = cleaned.split(/[_\s]+/).filter(Boolean);
    if (parts.length === 0) return "Site";
    return parts
      .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
      .join("");
  }

  // Build the synthesized "Postrule-wrapped" preview by inserting the
  // decorator into the actual source snippet. Returns
  //   { text, addedRanges, startLine }
  // where addedRanges is a list of [start, end] inclusive 0-based line
  // indices into the merged text that should render with diff-added
  // styling (green tint, "+" marker).
  function buildWrappedPreview(site) {
    const reasonable =
      (site.labels || []).filter(looksLikeReasonableLabel);
    const labels = reasonable.length > 0
      ? reasonable.map((l) => `'${String(l).replace(/'/g, "\\'")}'`).join(", ")
      : "  # inferred from return statements when you run `postrule init`";
    const fn = site.function_name;
    const decoratorBlock = `from postrule import ml_switch

@ml_switch(
    labels=[${labels}],
    author='@you:team',
)`;
    const decoLines = decoratorBlock.split("\n");

    if (!site.snippet) {
      const text = `${decoratorBlock}\ndef ${fn}(...):\n    # body unchanged\n    ...`;
      return {
        text,
        addedRanges: [[0, decoLines.length - 1]],
        startLine: 1,
      };
    }
    const lines = site.snippet.split("\n");
    const defPattern = new RegExp(`(^\\s*)def\\s+${fn}\\s*\\(`);
    let defLine = -1;
    let indent = "";
    for (let i = 0; i < lines.length; i++) {
      const m = lines[i].match(defPattern);
      if (m) {
        defLine = i;
        indent = m[1] || "";
        break;
      }
    }
    if (defLine < 0) {
      const text = `${decoratorBlock}\ndef ${fn}(...):\n    ...`;
      return {
        text,
        addedRanges: [[0, decoLines.length - 1]],
        startLine: 1,
      };
    }
    const indentedDeco = decoLines.map((l) => (l.length ? indent + l : l));
    const before = lines.slice(0, defLine);
    const after = lines.slice(defLine);
    const merged = [...before, ...indentedDeco, ...after];
    const addedStart = before.length;
    const addedEnd = addedStart + indentedDeco.length - 1;
    return {
      text: merged.join("\n"),
      addedRanges: [[addedStart, addedEnd]],
      startLine: site.snippet_start_line || 1,
    };
  }

  // Build the synthesized "Switch-class" preview for the same site.
  // Mirrors examples/25_native_switch_class.py: per-evidence methods
  // (one per param), a single _rule, and stub _on_<label> handlers
  // commented as "potential side effects detected here." Returns the
  // same { text, addedRanges, startLine } shape as buildWrappedPreview
  // — the entire synthesized class is treated as an added block so
  // the diff styling is consistent with the Decorator view.
  function buildWrappedPreviewSwitch(site) {
    const fn = site.function_name;
    const cls = classNameFor(fn) + "Switch";
    const params = extractParams(site);
    const reasonable =
      (site.labels || []).filter(looksLikeReasonableLabel);

    // Evidence methods: one per non-self param, typed by the source
    // annotation when present, falling back to `Any`.
    const evidenceLines = params.map((p) => {
      const ann = p.annotation || "Any";
      return [
        `    def _evidence_${p.name}(self, ${p.name}: ${ann}) -> ${ann}:`,
        `        return ${p.name}`,
      ].join("\n");
    });

    // Rule body: when we have analyzer-detected return labels we
    // build an evidence.<first_param>-based ladder; otherwise emit a
    // sentinel TODO. The first param is the convention for single-arg
    // sites (which is most of what the analyzer surfaces).
    const firstParam = params[0].name;
    const labelList = reasonable.length > 0 ? reasonable : [];
    let ruleBody;
    if (labelList.length === 0) {
      ruleBody =
        `        # TODO: replace with the original branch logic, reading evidence.${firstParam}.\n` +
        `        return "default"`;
    } else {
      const lines = [];
      // Use the original return labels as a heuristic ladder; the
      // last label becomes the default. Real --auto-lift output
      // reproduces the original branch logic; this preview shows the
      // shape, not a hand-fabricated fake.
      // Sanitize the label twice: once as a Python identifier for the
      // _matches_<label> helper name, and once as a string literal for
      // the return value (single quotes, escape backslashes + quotes).
      const sanId = (s) => String(s).replace(/[^A-Za-z0-9_]/g, "_");
      const sanStr = (s) =>
        String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
      for (let i = 0; i < labelList.length - 1; i++) {
        const id = sanId(labelList[i]);
        const lit = sanStr(labelList[i]);
        lines.push(`        if _matches_${id}(evidence.${firstParam}):`);
        lines.push(`            return '${lit}'`);
      }
      const tailLit = sanStr(labelList[labelList.length - 1]);
      lines.push(`        return '${tailLit}'`);
      ruleBody = lines.join("\n");
    }

    // Action stubs: one _on_<label> per detected label, commented as
    // a side-effect placeholder so the analyzer's hidden-state
    // discovery is visible.
    const onMethods =
      labelList.length === 0
        ? `    def _on_default(self, ${firstParam}):\n        ...  # action handler — analyzer detected potential side effects here`
        : labelList
            .map((l) => {
              const safe = l.replace(/[^A-Za-z0-9_]/g, "_");
              return `    def _on_${safe}(self, ${firstParam}):\n        ...  # potential side effect, lifted from the original branch`;
            })
            .join("\n\n");

    const text =
      `from postrule import Switch\n` +
      `\n\n` +
      `class ${cls}(Switch):\n` +
      `    """Auto-lifted from ${fn}.\n` +
      `\n` +
      `    Conventions:\n` +
      `      _evidence_<name> -> typed evidence dataclass field\n` +
      `      _rule(evidence)  -> label name\n` +
      `      _on_<label>      -> action handler (lifted side effect)\n` +
      `    """\n` +
      `\n` +
      evidenceLines.join("\n\n") +
      `\n\n` +
      `    def _rule(self, evidence) -> str:\n` +
      ruleBody +
      `\n\n` +
      onMethods +
      `\n`;

    const lineCount = text.split("\n").length;
    return {
      text,
      addedRanges: [[0, lineCount - 1]],
      startLine: 1,
    };
  }

  // Build the Config tab preview — three sections, lightest to deepest.
  // Goal is "set me at ease" not "show me everything." The centralize
  // block is the load-bearing pattern for projects with many sites;
  // override knobs are listed for completeness.
  function buildConfigPreview(site) {
    const reasonable =
      (site.labels || []).filter(looksLikeReasonableLabel);
    const labels = reasonable.length > 0
      ? reasonable.map((l) => `'${String(l).replace(/'/g, "\\'")}'`).join(", ")
      : "  # inferred from return statements when you run `postrule init`";
    const fn = site.function_name;
    const safetyHint = site.regime === "narrow"
      ? "  # set True for authorization-class decisions (caps at Phase 4)"
      : "  # set True to cap at Phase 4 for any safety-critical site";
    return `# 1. Minimum viable. Drop-in two-liner — this is enough to ship.
from postrule import ml_switch

@ml_switch(
    labels=[${labels}],
    author='@you:team',
)
def ${fn}(...): ...


# 2. Centralize across your project — define once, reuse everywhere.
# Eliminates repetitive boilerplate across 10s or 100s of sites.
from postrule import SwitchConfig

team_config = SwitchConfig(
    author='@triage:support',
    safety_critical=False,${safetyHint}
)

# Then everywhere in your codebase:
@ml_switch(labels=[${labels}], config=team_config)
def ${fn}(...): ...


# 3. Override knobs — exposed when you need them.
#   phase           Phase.RULE      start at the rule; evidence advances
#   gate            McNemarGate     α=0.01 (1% per-step graduation FPR)
#   verifier        'default'       auto-detect Ollama / OpenAI / Anthropic
#   auto_advance    True            gate fires every 250 verdicts
#   storage         FileStorage     runtime/postrule/<switch>/, atomic + fsynced


# 4. Native class form — the canonical v1 authoring pattern.
# Same site as above, expressed as a Switch subclass instead of a
# decorator. This is what \`postrule init --auto-lift\` writes for you.
from postrule import Switch


class ${classNameFor(fn)}Switch(Switch):
    """Authoring conventions:
       _evidence_<name>(...) -> typed evidence dataclass field
       _rule(self, evidence) -> label name
       _on_<label>(...)      -> action handler
    """

${extractParams(site)
  .map((p) => {
    const ann = p.annotation || "Any";
    return `    def _evidence_${p.name}(self, ${p.name}: ${ann}) -> ${ann}:\n        return ${p.name}`;
  })
  .join("\n\n")}

    def _rule(self, evidence) -> str:
        # original branch logic, reading evidence.${extractParams(site)[0].name}
        ...

${
  reasonable.length > 0
    ? reasonable
        .map(
          (l) =>
            `    def _on_${String(l).replace(/[^A-Za-z0-9_]/g, "_")}(self, ${extractParams(site)[0].name}): ...`
        )
        .join("\n")
    : `    def _on_default(self, ${extractParams(site)[0].name}): ...`
}`;
  }

  // Build a realistic Phase progression for the Sample-log tab — seven
  // records showing the lifecycle journey from RULE to ML_PRIMARY as
  // outcomes accumulate and gates clear. Visitors see the full story
  // their code would take, not one isolated entry. Labels come from
  // the analyzer's detected return values when they look reasonable;
  // otherwise we use generic placeholders. We never mix real and
  // placeholder labels in the same progression.
  function buildSampleLog(site) {
    const fn = site.function_name;
    const reasonable =
      (site.labels || []).filter(looksLikeReasonableLabel);
    const fallback = ["label_a", "label_b", "label_c"];
    const pool = reasonable.length > 0 ? reasonable : fallback;
    const pick = (i) => pool[i % pool.length];
    const lblA = pick(0);
    const lblB = pick(1);
    const lblC = pick(2);

    // Timeline is intentionally compressed. The McNemar gate clears
    // at 250 outcomes; on a high-volume site (10k+ calls/day) that
    // accumulates in hours, not weeks. Below is a realistic best-case
    // progression for a typical SaaS-grade endpoint — actual cadence
    // depends on call volume and verdict latency.
    const entries = [
      {
        comment: "# Hour 0 — Phase RULE. Your hand-written rule decides; every call logs silently.",
        record: {
          id: "01HXM2K9PQYQB7TFR0VAB8XW3M",
          ts: "2026-04-27T09:00:11.392Z",
          switch: fn,
          phase: "RULE",
          label: lblA,
          source: "rule_output",
          confidence: 1.0,
          verdict: null,
        },
      },
      {
        comment: "# Hour 6 — verdicts streaming in from downstream signals (CSAT, resolution code, audit).",
        record: {
          id: "01HXR4D2VMR8RP4PT0AB9CQYYE",
          ts: "2026-04-27T15:13:44.807Z",
          switch: fn,
          phase: "RULE",
          label: lblB,
          source: "rule_output",
          confidence: 1.0,
          verdict: "correct",
        },
      },
      {
        comment: "# Day 1 — operator advances to MODEL_SHADOW. LLM shadow-classifies on every call.",
        record: {
          id: "01HXY7N5KQQ9X3M02WAB7C8DF1",
          ts: "2026-04-28T10:08:22.119Z",
          switch: fn,
          phase: "MODEL_SHADOW",
          label: lblA,
          source: "rule_output",
          confidence: 1.0,
          shadow: { source: "model_output", label: lblA, confidence: 0.86 },
          verdict: null,
        },
      },
      {
        comment: "# Day 2 — gate clears (α=0.01, 250 outcomes). Auto-advance to MODEL_PRIMARY.",
        record: {
          id: "01HZ3ME4D7XRP5T2BGN1Y9KZ2A",
          ts: "2026-04-29T11:32:09.554Z",
          switch: fn,
          phase: "MODEL_PRIMARY",
          label: lblC,
          source: "model_output",
          confidence: 0.92,
          verdict: "correct",
        },
      },
      {
        comment: "# Day 4 — Phase ML_SHADOW. Trained ML head shadows each model decision.",
        record: {
          id: "01J18XZ3R5MR6Y4PQAVV8T2N3K",
          ts: "2026-05-01T16:21:05.432Z",
          switch: fn,
          phase: "ML_SHADOW",
          label: lblB,
          source: "model_output",
          confidence: 0.89,
          shadow: { source: "ml_output", label: lblB, confidence: 0.94 },
          verdict: "correct",
        },
      },
      {
        comment: "# Day 7 — gate clears again. ML_WITH_FALLBACK: ml head primary, model + rule below.",
        record: {
          id: "01J3KFY9Z8MT2VBLWV3RA0HP6Q",
          ts: "2026-05-04T08:47:31.901Z",
          switch: fn,
          phase: "ML_WITH_FALLBACK",
          label: lblA,
          source: "ml_output",
          confidence: 0.96,
          fallback_chain_used: false,
          verdict: "correct",
        },
      },
      {
        comment: "# Day 14 — final graduation to ML_PRIMARY (sub-millisecond, ~$0/call).",
        record: {
          id: "01J6YBNKM7T3V8AGRP5HDQ2C9L",  // pragma: allowlist secret
          ts: "2026-05-11T13:14:48.211Z",
          switch: fn,
          phase: "ML_PRIMARY",
          label: lblC,
          source: "ml_output",
          confidence: 0.97,
          verdict: "correct",
        },
      },
    ];

    const fileHeader =
      `# Default storage is in-memory (BoundedInMemoryStorage). The records below are\n` +
      `# what you'd see on disk after ml_switch(..., persist=True), which derives a\n` +
      `# FileStorage at:\n` +
      `#\n` +
      `#   Log file:  runtime/postrule/${fn}/outcomes.jsonl   # active segment, append-only\n` +
      `#              runtime/postrule/${fn}/outcomes.jsonl.1 # rotated by size, not date\n` +
      `#   Source:    ${site.file_path}:${site.line_start}  (def ${fn})\n` +
      `#   Format:    one ClassificationRecord JSON per line; flock-protected, fsync optional.\n`;
    const body = entries
      .map((e) => `${e.comment}\n${JSON.stringify(e.record, null, 2)}`)
      .join("\n\n");
    return `${fileHeader}\n${body}`;
  }

  // Build a deep-link to the file at the exact line on GitHub.
  function buildGithubLink(data, site) {
    if (!data.github_blob_prefix) return null;
    return `${data.github_blob_prefix}/${site.file_path}#L${site.line_start}-L${site.line_end}`;
  }

  // Render the expanded panel for a clicked site row. Three tabs:
  // Source, Postrule-wrapped, Sample log.
  function renderExpanded(site, data) {
    const githubLink = buildGithubLink(data, site);
    const sourceText = site.snippet
      ? escapeHtml(site.snippet)
      : "(source snippet not available — install postrule and run `postrule analyze .` for the live scan)";
    const sourceStartLine = site.snippet_start_line || site.line_start;
    const wrapped = buildWrappedPreview(site);
    const wrappedSwitch = buildWrappedPreviewSwitch(site);
    return `
      <div class="site-expanded">
        <div class="site-tabs" role="tablist">
          ${githubLink
            ? `<a class="site-github-link" href="${githubLink}" target="_blank" rel="noopener noreferrer" title="Open this file at the right line on GitHub" aria-label="Open file on GitHub"><svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg></a>`
            : ""}
          <button type="button" class="site-tab" data-tab="source" aria-selected="true">Source</button>
          <button type="button" class="site-tab" data-tab="wrapped" aria-selected="false">Postrule-wrapped</button>
          <button type="button" class="site-tab" data-tab="log" aria-selected="false">Sample log entry</button>
          <button type="button" class="site-tab" data-tab="config" aria-selected="false">Config</button>
        </div>
        <div class="site-tab-pane" data-pane="source">
          ${renderCodeBlock(site.snippet || "(source snippet not available — install postrule and run `postrule analyze .` for the live scan)", {
            startLine: sourceStartLine,
          })}
          <p class="site-tab-cap">Lines ${sourceStartLine}-${site.snippet_end_line || site.line_end} of <code>${escapeHtml(site.file_path)}</code></p>
        </div>
        <div class="site-tab-pane" data-pane="wrapped" hidden>
          <div class="wrapped-style-toggle" role="group" aria-label="Authoring style">
            <button type="button" class="wrapped-style-toggle__btn" data-wrapped-style="decorator" aria-pressed="true">Decorator</button>
            <button type="button" class="wrapped-style-toggle__btn" data-wrapped-style="switch" aria-pressed="false">Switch class</button>
          </div>
          <div data-wrapped-render="decorator">
            ${renderCodeBlock(wrapped.text, { startLine: wrapped.startLine, addedRanges: wrapped.addedRanges })}
          </div>
          <div data-wrapped-render="switch" hidden>
            ${renderCodeBlock(wrappedSwitch.text, { startLine: wrappedSwitch.startLine, addedRanges: wrappedSwitch.addedRanges })}
          </div>
          <p class="site-tab-cap">Green <code>+</code> lines are what <code>postrule init</code> writes into <code>${escapeHtml(site.file_path)}</code>. <strong>Decorator</strong> is the drop-in two-liner; <strong>Switch class</strong> is what <code>--auto-lift</code> generates when you want per-branch handlers and per-evidence methods extracted. Run locally:</p>
          <div class="cap-cmd" data-copy-target>
            <code>postrule init ${escapeHtml(site.file_path)}:${escapeHtml(site.function_name)} --author @you:team --dry-run</code>
            <button type="button" class="copy-btn copy-btn--code" data-copy-source aria-label="Copy command" title="Copy"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="2" width="9" height="11" rx="1.5"/><path d="M3 5v8.5A1.5 1.5 0 0 0 4.5 15H11"/></svg></button>
          </div>
        </div>
        <div class="site-tab-pane" data-pane="log" hidden>
          ${renderCodeBlock(buildSampleLog(site), { showLineNumbers: false })}
          <p class="site-tab-cap">Seven entries illustrating the lifecycle from Phase 0 (RULE) → Phase 5 (ML_PRIMARY) as outcomes accumulate and gates clear. Real records have the same shape; one is appended per call.</p>
        </div>
        <div class="site-tab-pane" data-pane="config" hidden>
          ${renderCodeBlock(buildConfigPreview(site), { showLineNumbers: false })}
          <p class="site-tab-cap">Start with the minimum (labels + author). Centralize via a shared <code>SwitchConfig</code> to avoid repeating yourself across many sites. Override knobs are listed for reference.</p>
        </div>
      </div>`;
  }

  function renderTable(sites, data) {
    if (!tableBody) return;
    if (!sites || sites.length === 0) {
      tableBody.innerHTML =
        '<tr><td colspan="6" class="empty">No classification sites found in this repo. Try another preset.</td></tr>';
      return;
    }
    // Sort by priority score descending; highest-priority sites first.
    const sorted = [...sites].sort((a, b) => b.priority_score - a.priority_score);
    const shown = sorted.slice(0, ROW_CAP);
    const more = sorted.length - shown.length;

    const rows = shown
      .map((s, idx) => {
        // Keep the suffix (filename + line) — that's the part that
        // identifies the site to the reader. The full path is in the
        // title attribute for hover, and the "GitHub ↗" deep-link in
        // the expanded panel preserves it. Threshold 36 chars works
        // for ~22% of a 1024px wrap.
        const fullFL = `${s.file_path}:${s.line_start}`;
        const flMax = 36;
        const flDisplay =
          fullFL.length > flMax
            ? "…" + fullFL.slice(fullFL.length - flMax + 1)
            : fullFL;
        // Function name — keep last N chars when overlong (so suffix
        // like _kind / _formatter stays visible). Rare in practice
        // but cheap insurance.
        const fnMax = 28;
        const fnDisplay =
          s.function_name.length > fnMax
            ? "…" + s.function_name.slice(s.function_name.length - fnMax + 1)
            : s.function_name;
        return `
        <tr class="site-row" data-site-index="${idx}" tabindex="0" aria-expanded="false">
          <td class="mono cell-fileline" title="${escapeHtml(fullFL)}">${escapeHtml(flDisplay)}</td>
          <td class="mono cell-function" title="${escapeHtml(s.function_name)}">${escapeHtml(fnDisplay)}</td>
          <td>${patternBadge(s.pattern)}</td>
          <td class="num">${s.label_cardinality || "—"}</td>
          <td>${regimeBadge(s.regime)}</td>
          <td class="num">${s.priority_score.toFixed(2)}<span class="row-chevron" aria-hidden="true">›</span></td>
        </tr>
        <tr class="site-expansion-row" data-expansion-for="${idx}" hidden>
          <td colspan="6">${renderExpanded(s, data)}</td>
        </tr>`;
      })
      .join("");
    let footer = "";
    if (more > 0) {
      footer = `
        <tr class="more-row">
          <td colspan="6" class="dim">
            … and ${more} more sites. Run <code>postrule analyze .</code> locally for the full list.
          </td>
        </tr>`;
    }
    tableBody.innerHTML = rows + footer;
    wireRowClicks(shown);
  }

  function wireRowClicks(shown) {
    const rows = tableBody.querySelectorAll(".site-row");
    rows.forEach((row) => {
      const idx = row.dataset.siteIndex;
      const expansion = tableBody.querySelector(
        `.site-expansion-row[data-expansion-for='${idx}']`
      );
      if (!expansion) return;

      const toggle = () => {
        const isOpen = !expansion.hidden;
        // Close all other rows first.
        tableBody.querySelectorAll(".site-row").forEach((r) => {
          r.setAttribute("aria-expanded", "false");
          r.classList.remove("site-row--open");
        });
        tableBody
          .querySelectorAll(".site-expansion-row")
          .forEach((e) => (e.hidden = true));

        if (!isOpen) {
          expansion.hidden = false;
          row.setAttribute("aria-expanded", "true");
          row.classList.add("site-row--open");
          wireTabClicks(expansion);
        }
      };
      row.addEventListener("click", toggle);
      row.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          toggle();
        }
      });
    });
  }

  function wireTabClicks(expansion) {
    const tabs = expansion.querySelectorAll(".site-tab");
    const panes = expansion.querySelectorAll(".site-tab-pane");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const target = tab.dataset.tab;
        tabs.forEach((t) =>
          t.setAttribute(
            "aria-selected",
            t.dataset.tab === target ? "true" : "false"
          )
        );
        panes.forEach((p) => (p.hidden = p.dataset.pane !== target));
      });
    });
    wireWrappedStyleToggle(expansion);
  }

  // Wire the Decorator / Switch-class toggle inside the wrapped pane.
  // Default state ("decorator") is set in markup so the existing
  // visual behavior is unchanged for users who never click.
  function wireWrappedStyleToggle(expansion) {
    const buttons = expansion.querySelectorAll("[data-wrapped-style]");
    const renders = expansion.querySelectorAll("[data-wrapped-render]");
    if (buttons.length === 0 || renders.length === 0) return;
    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.dataset.wrappedStyle;
        buttons.forEach((b) =>
          b.setAttribute(
            "aria-pressed",
            b.dataset.wrappedStyle === target ? "true" : "false"
          )
        );
        renders.forEach(
          (r) => (r.hidden = r.dataset.wrappedRender !== target)
        );
      });
    });
  }

  function renderSummary(data) {
    if (!summary) return;
    const repo = data.repo || data.repo_label || data.root || "your repo";
    const display = repo.replace(/^.*\//, "");
    // Derive the repo home URL from github_blob_prefix when available
    // (strip "/blob/<branch>" off the end). Falls back to a plain text
    // span when we have no upstream link.
    let repoUrl = null;
    if (data.github_blob_prefix) {
      repoUrl = data.github_blob_prefix.replace(/\/blob\/[^/]+\/?$/, "");
    } else if (data.repo_url) {
      repoUrl = data.repo_url;
    }
    const repoTag = repoUrl
      ? `<a href="${repoUrl}" target="_blank" rel="noopener noreferrer" class="summary-repo">${escapeHtml(display)} ↗</a>`
      : `<strong>${escapeHtml(display)}</strong>`;
    summary.innerHTML = `
      ${repoTag} &mdash;
      <span class="num">${data.total_sites}</span> classification sites in
      <span class="num">${data.files_scanned.toLocaleString()}</span> Python files.
      <span class="dim">Click any row to explore.</span>
    `;
  }

  // Project savings from the analyzer output + slider state.
  function projectSavings(data, callsPerMonth, costPerCall) {
    if (!data || !data.sites || data.sites.length === 0) {
      return { annual: 0, daily: 0, sitesEligible: 0 };
    }
    const eligibleSites = data.sites.filter((s) => s.priority_score >= 2.0);
    const sitesEligible = eligibleSites.length;
    if (sitesEligible === 0) {
      return { annual: 0, daily: 0, sitesEligible: 0 };
    }
    const fullSavings = callsPerMonth * costPerCall * 12;
    const recoverable = Math.min(0.85, sitesEligible / data.sites.length);
    const annual = fullSavings * recoverable;
    return { annual, daily: annual / 365, sitesEligible };
  }

  function fmtPerCallUSD(cost) {
    // The slider goes down to $0.00001; cheap providers (Gemini Flash,
    // Together) land below $0.0001 where 4-decimal would round to $0.0000.
    // Use 6 decimal places below 0.0001, 4 places above.
    return cost < 0.0001 ? "$" + cost.toFixed(6) : "$" + cost.toFixed(4);
  }

  function renderSavings() {
    const calls = Number(callsInput.value);
    const cost = Number(costInput.value);
    if (callsOutput) callsOutput.textContent = fmtNum(calls);
    if (costOutput) costOutput.textContent = fmtPerCallUSD(cost);
    if (!current) {
      if (savingsAnnual) savingsAnnual.textContent = "—";
      if (savingsDaily) savingsDaily.textContent = "—";
      if (sitesCount) sitesCount.textContent = "—";
      return;
    }
    const proj = projectSavings(current, calls, cost);
    if (savingsAnnual) savingsAnnual.textContent = fmtUSD(proj.annual) + "/yr";
    if (savingsDaily) savingsDaily.textContent = fmtUSD(proj.daily) + "/day";
    if (sitesCount) sitesCount.textContent = String(proj.sitesEligible);
  }

  async function loadPreset(slug) {
    const url = `./data/analyze-${slug}.json`;
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      data.repo = data.repo || slug;
      current = data;
      renderSummary(data);
      renderTable(data.sites, data);
      renderSavings();
    } catch (err) {
      if (summary)
        summary.innerHTML = `<span class="error">Failed to load preset: ${err.message}</span>`;
    }
  }

  function setActiveChip(slug) {
    root.querySelectorAll("[data-preset]").forEach((el) => {
      el.setAttribute(
        "aria-pressed",
        el.dataset.preset === slug ? "true" : "false"
      );
    });
  }

  root.querySelectorAll("[data-preset]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const slug = chip.dataset.preset;
      setActiveChip(slug);
      loadPreset(slug);
    });
  });

  if (callsInput) callsInput.addEventListener("input", renderSavings);
  if (costInput) costInput.addEventListener("input", renderSavings);

  // ----- LLM provider preset chips -------------------------------
  // Load landing/data/llm-prices.json on init; render one chip per
  // provider; clicking a chip sets the cost slider to that provider's
  // typical classifier-call cost AND surfaces the rate breakdown so
  // the visitor sees what number they're using and where it came from.
  // Manual cost-slider drags still work (no chip is active after a
  // manual drag).
  const presetsContainer = root.querySelector("[data-llm-presets-chips]");
  const presetsDetail = root.querySelector("[data-llm-presets-detail]");

  function fmtPerMillionUSD(n) {
    return n === 0 ? "free" : "$" + n.toFixed(2) + "/M";
  }

  function renderPresetDetail(provider, lastUpdated) {
    if (!presetsDetail) return;
    const inputRate = fmtPerMillionUSD(provider.input_per_m_usd);
    const outputRate = fmtPerMillionUSD(provider.output_per_m_usd);
    const perCall = fmtPerCallUSD(provider.per_call_usd);
    const note = provider.notes ? ` ${provider.notes}` : "";
    presetsDetail.innerHTML = (
      `<strong>${escapeHtml(provider.label)}</strong> (${escapeHtml(provider.model)}): ` +
      `${escapeHtml(inputRate)} input + ${escapeHtml(outputRate)} output. ` +
      `~${escapeHtml(perCall)}/call at 250+50 tokens.` +
      `${escapeHtml(note)} ` +
      `<span class="try-roi__presets-asof">Last updated ${escapeHtml(lastUpdated)}.</span>`
    );
  }

  function setActivePresetChip(id) {
    if (!presetsContainer) return;
    presetsContainer.querySelectorAll("[data-llm-preset-id]").forEach((el) => {
      el.setAttribute(
        "aria-pressed",
        el.dataset.llmPresetId === id ? "true" : "false"
      );
    });
  }

  function applyPreset(provider, lastUpdated) {
    if (!costInput) return;
    const cost = Number(provider.per_call_usd);
    if (!Number.isFinite(cost) || cost <= 0) return;
    // Clamp to slider min so the value lands in-range; the rate detail
    // text still shows the unrounded provider price.
    const min = Number(costInput.min) || 0.00001;
    costInput.value = Math.max(cost, min);
    setActivePresetChip(provider.id);
    renderPresetDetail(provider, lastUpdated);
    renderSavings();
  }

  async function loadLlmPresets() {
    if (!presetsContainer) return;
    try {
      const r = await fetch("./data/llm-prices.json");
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      const lastUpdated = data.last_updated || "";
      presetsContainer.innerHTML = "";
      (data.providers || []).forEach((provider, idx) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "chip chip--preset";
        btn.dataset.llmPresetId = provider.id;
        btn.setAttribute("aria-pressed", "false");
        btn.textContent = provider.label;
        btn.addEventListener("click", () => applyPreset(provider, lastUpdated));
        presetsContainer.appendChild(btn);
        // Default selection: first chip (the baseline GPT-4o).
        if (idx === 0) applyPreset(provider, lastUpdated);
      });
      if (presetsDetail && (data.providers || []).length === 0) {
        presetsDetail.textContent = "No provider rates loaded.";
      }
    } catch (err) {
      if (presetsDetail) {
        presetsDetail.textContent =
          "Could not load provider rates; the slider still works. Drag it to your typical $/call.";
      }
      if (presetsContainer) presetsContainer.innerHTML = "";
    }
  }

  // Manual drag should clear the active chip so the visitor sees they
  // are off-preset.
  if (costInput) {
    costInput.addEventListener("input", () => setActivePresetChip(null));
  }

  loadLlmPresets();
  // ---------------------------------------------------------------

  if (customForm) {
    customForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const val = (customInput && customInput.value || "").trim();
      if (!val) return;
      if (summary) {
        summary.innerHTML = `
          <span class="info">
            Live custom-repo analysis is shipping in v1.1
            (Pyodide-in-browser; ~5-15 s for small/medium repos).
            Today: <code>pip install postrule &amp;&amp; postrule analyze ${escapeHtml(val.replace(/^https?:\/\/(www\.)?github\.com\//, ""))}</code>
            for the full scan locally.
          </span>`;
      }
    });
  }

  loadPreset("marimo");
  setActiveChip("marimo");
})();
