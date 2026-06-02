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

## How to compile (do this first — it is the verification step)
I could **not** compile locally (no TeX install here), so the first compile on
Overleaf is the verification gate.

1. **Overleaf** → New Project → Upload Project → zip this `tmlr/` folder.
2. **Menu → Compiler → LuaLaTeX.** *(Required: the paper uses UTF-8 — α, →, ×, ≥, β — which pdfLaTeX chokes on. LuaLaTeX handles it natively.)*
3. Compile. Then verify the conversion artifacts below.

### Conversion artifacts to eyeball on first compile
pandoc → LaTeX is faithful but a few things deserve a look (none are
format/anonymization blockers; they're typesetting polish):
- **Section numbering** — headings are auto-numbered by LaTeX (manual "1." prefixes were stripped). Confirm 1–11 + appendices read right; `\appendix` is injected before Appendix A.
- **Wide tables** — 12 `longtable`s. If any overrun the margin, wrap with `\resizebox{\textwidth}{!}{…}` or `\small`.
- **Figure sizing** — each is `\includegraphics[width=\linewidth]`; adjust per-figure if a panel is too large.
- **Abstract** — lives in `\begin{abstract}` in `main.tex` (pulled from `abstract.tex`).

## Anonymization checklist (done — verify before submit)
- [x] Author name / affiliation / email — removed (placeholder byline; auto-anonymized by `\usepackage{tmlr}`).
- [x] **Product name** → `\sysname` macro = "our framework". (22 occurrences in body.)
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
2. `main.tex`: `\newcommand{\sysname}{our framework}` → `\renewcommand{\sysname}{Postrule}` (one line).
3. Restore the real `\author{…}` block, Acknowledgments, GitHub URL, patent note, self-citation.

## Resubmit on OpenReview
TMLR is desk-rejected, not rejected-on-review — you resubmit as a **new submission** (the old `POmunHPNWl` thread is closed).
1. openreview.net → TMLR → **Submit**.
2. Upload the **compiled PDF** (anonymous, from step above).
3. Title/abstract: paste the anonymized title + abstract; confirm authors are entered in OpenReview but the **PDF itself stays anonymous** (OpenReview handles author identity separately and hides it from reviewers).
4. Confirm the submission renders "Under review as submission to TMLR" in the header.
