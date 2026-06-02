# TMLR resubmission runbook — "When Should a Rule Learn?"

The original submission (OpenReview `POmunHPNWl`) was **desk-rejected** for
two reasons. Both are fixed in this `tmlr/` package:

| Desk-reject reason | Fix |
|---|---|
| **Not the correct LaTeX format for TMLR** | The paper was authored in Typst. This package uses the **official TMLR LaTeX style** (`tmlr.sty`, `tmlr.bst`, `fancyhdr.sty`, `math_commands.tex` — vendored verbatim from `JmlrOrg/tmlr-style-file`) with the exact `\documentclass[10pt]{article}` + `\usepackage{tmlr}` template. |
| **Not anonymized correctly** | Full double-blind pass (see checklist). Author/affiliation/email/patent/GitHub/Acknowledgments removed; the product name is behind a `\sysname` macro that renders "our framework" under review; self-citation anonymized; `\usepackage{tmlr}` (no `accepted`/`preprint`) auto-prints "Under review as submission to TMLR." |

## Package contents
- `main.tex` — entry point (preamble + anonymized title/author + abstract + `\input{body.tex}`).
- `body.tex`, `abstract.tex` — converted from `../paper-draft.md` via `build_tmlr.py` (pandoc).
- `tmlr.sty`, `tmlr.bst`, `fancyhdr.sty`, `math_commands.tex` — official TMLR files.
- `results/*.png` — the 7 figures (vendored; figures switched from `\includesvg` → `\includegraphics` PNG for portability).
- `build_tmlr.py` — regenerates `body.tex`/`abstract.tex` from the markdown.

## Compile status — VERIFIED
This package **compiles clean** (`exit 0`, **32 pages**, US-letter) — verified
locally with **Tectonic**. `main.pdf` in this folder is that build (anonymized).
The earlier "couldn't compile" caveat is resolved: Unicode, the longtable
`\real`/column-width macros, the `\mathcal` math fix, figure paths (svg→png),
and caption numbering were all fixed and re-compiled to zero errors.

To rebuild:
- **Tectonic (what was used):** `cd tmlr && tectonic main.tex` → `main.pdf`. Single binary, fetches packages on first run.
- **Overleaf (reviewer-equivalent):** upload this `tmlr/` folder, set **Compiler → LuaLaTeX** (handles the UTF-8 mapped via `newunicodechar`), compile.
- To regenerate `body.tex`/`abstract.tex` from the markdown source: `python3 ../build_tmlr.py` (re-applies anonymization + fixes), then recompile.

Resolved during verification (no open items): section auto-numbering,
12 `longtable`s shrunk with `\small` (no margin overruns), figure captions
(manual "Figure N." kept, LaTeX's duplicate label suppressed), abstract in
`\begin{abstract}`. Residual: ~36 cosmetic overfull boxes (normal; not a
blocker).

## Anonymization checklist (done — verify before submit)
- [x] Author name / affiliation / email — removed (placeholder byline; auto-anonymized by `\usepackage{tmlr}`).
- [x] **Product name (prose)** → `\sysname` macro = "our framework".
- [x] **Package/code identifiers (lowercase `postrule`)** → `\pkgname` macro = "ourlib" (module paths, filenames, e.g. `ourlib.ml.SklearnTextHead`). 45 prose+code occurrences total across body + abstract; **compiled-PDF grep for `postrule`/`booth`/`b-tree`/`austin`/patent = 0.**
- [x] Company name ("B-Tree Labs") → "[Anonymous Org]".
- [x] GitHub URL → "[anonymized repository]" (2 spots).
- [x] Patent provisional number → "[patent application details omitted for anonymous review]".
- [x] Acknowledgments section — removed.
- [x] Self-citation (Booth 2026) in References → "Anonymous. (2026). … Under review."
- [x] Copyright footer — removed.
- [ ] **Final read-through** for any residual identifying phrasing (e.g. "we built Postrule at…", dataset names that deanonymize, a footnote URL). Grep the PDF for: your name, the company, the product, the city, the patent number.
- [ ] Supplementary/code, if attached, must also be anonymized (TMLR rule). Easiest: "code released on acceptance."

## De-anonymize for camera-ready / preprint (after acceptance)
1. `main.tex`: `\usepackage{tmlr}` → `\usepackage[accepted]{tmlr}` (camera-ready) or `\usepackage[preprint]{tmlr}` (arXiv).
2. `main.tex`: flip **two** macros — `\newcommand{\sysname}{our framework}` → `{Postrule}`, and `\newcommand{\pkgname}{ourlib}` → `{postrule}`.
3. Restore the real `\author{…}` block, Acknowledgments, GitHub URL, patent note, self-citation.

## Resubmit on OpenReview
TMLR is desk-rejected, not rejected-on-review — you resubmit as a **new submission** (the old `POmunHPNWl` thread is closed).
1. openreview.net → TMLR → **Submit**.
2. Upload the **compiled PDF** (anonymous, from step above).
3. Title/abstract: paste the anonymized title + abstract; confirm authors are entered in OpenReview but the **PDF itself stays anonymous** (OpenReview handles author identity separately and hides it from reviewers).
4. Confirm the submission renders "Under review as submission to TMLR" in the header.
