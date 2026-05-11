# When Should a Rule Learn? — paper directory

| File | What it is | How to read |
|---|---|---|
| **[paper.pdf](paper.pdf)** | **Canonical compiled paper.** ~39 pages. Read this. | Open in any PDF viewer. |
| `paper.typ` | Typst source — the entry point. | `typst compile paper.typ paper.pdf` (Typst v0.13+ required) |
| `body.typ` | Typst source — body, included by `paper.typ`. | (same) |
| `paper-draft.md` | Earlier markdown precursor. **Superseded by `paper.pdf`** but kept for grep-ability and historical context. | Read only if you can't open the PDF. |
| `outline.md` | Pre-write structural outline. Mostly historical. | — |
| `related-work-bibliography.md` | Curated bibliography source. Cited in the paper. | — |
| `results/` | All figures (PNG + SVG) + JSON benchmark data. Referenced from the paper. | — |

## Rebuilding the PDF

```bash
brew install typst         # or `cargo install typst-cli`
cd docs/papers/2026-when-should-a-rule-learn
typst compile paper.typ paper.pdf
```

Compile time: ~2 seconds on Apple Silicon.

## Status

Draft v0.1 as of 2026-04-26. Target submission: arXiv (cs.LG / cs.SE)
on ~2026-05-22 (decoupled from launch day 2026-05-20 to incorporate
the Snips bench rerun).
