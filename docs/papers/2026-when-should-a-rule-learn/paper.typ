// paper.typ — academic-style wrapper around the pandoc-generated body.
// Usage: typst compile paper.typ paper.pdf

// Page setup — US Letter, generous margins for printability + readability.
#set page(
  paper: "us-letter",
  margin: (x: 1.1in, y: 1.0in),
  numbering: "1 / 1",
  number-align: center,
  footer: context [
    #set text(size: 9pt, fill: gray.darken(20%))
    #grid(
      columns: (1fr, auto, 1fr),
      align: (left, center, right),
      [_When Should a Rule Learn?_],
      counter(page).display("1 / 1", both: true),
      [Benjamin Booth (2026)],
    )
  ],
)

// Typography — body in serif (Charter or fallback), headings in sans (Helvetica/SF Pro).
#set text(
  font: ("Charter", "Georgia", "New Computer Modern", "Times New Roman"),
  size: 10.5pt,
  hyphenate: true,
)
#set par(
  justify: true,
  leading: 0.62em,
  first-line-indent: 0pt,
  spacing: 0.85em,
)

// Code — monospaced, slightly smaller, light background.
#show raw.where(block: true): set block(
  fill: rgb("#f7f7f8"),
  inset: 8pt,
  radius: 3pt,
  width: 100%,
)
#show raw.where(block: true): set text(size: 9pt)
#show raw.where(block: false): set text(size: 9.5pt, fill: rgb("#0f3a85"))

// Headings — sans-serif, color cue, tighter spacing.
#show heading: set text(font: ("Helvetica Neue", "Helvetica"))
#show heading.where(level: 1): it => {
  pagebreak(weak: true)
  v(0.4em)
  block(below: 0.6em, text(size: 18pt, weight: "bold", fill: rgb("#0a2540"), it.body))
}
#show heading.where(level: 2): it => {
  v(0.5em)
  block(below: 0.4em, text(size: 13pt, weight: "bold", fill: rgb("#0a2540"), it.body))
}
#show heading.where(level: 3): it => {
  v(0.3em)
  block(below: 0.3em, text(size: 11pt, weight: "bold", fill: rgb("#0a2540"), it.body))
}
#show heading.where(level: 4): it => {
  v(0.2em)
  block(below: 0.2em, text(size: 10.5pt, weight: "bold", style: "italic", fill: rgb("#3a3a3a"), it.body))
}

// Links — subtle blue, no underline.
#show link: set text(fill: rgb("#0a4d8c"))

// Figures — caption styling.
#show figure.caption: it => {
  set text(size: 9.5pt, fill: rgb("#3a3a3a"))
  set par(leading: 0.55em, justify: true)
  it
}

// Tables — additive styling that doesn't conflict with pandoc's emitted
// structure (which uses table.header + table.hline). Just shrink text and
// tint header background; let pandoc's hlines do the dividers.
#show table: set text(size: 8.5pt)
#show table.cell.where(y: 0): set text(weight: "bold")
// Disable paragraph justification inside table cells — narrow cells with
// short wrapped lines produce visually-jarring word-spacing gaps under
// the global `par(justify: true)`. Also disable hyphenation in cells.
#show table.cell: set par(justify: false)
#show table.cell: set text(hyphenate: false)
// Wide-table accommodation: tables are emitted inside `align(center)[#table(...)]`
// blocks. We can't change column widths after the fact, but we can let them
// run into the figure's caption area and use full text width if needed.
#set table(inset: (x: 4pt, y: 3pt))

// Pandoc compatibility shims — pandoc's typst writer emits these.
#let horizontalrule = align(center, line(length: 30%, stroke: 0.5pt + gray))

// Title block — typeset before pulling in the body.
#align(center)[
  #v(1em)
  #text(size: 22pt, weight: "bold", fill: rgb("#0a2540"))[
    When Should a Rule Learn?
  ]
  #v(0.3em)
  #text(size: 14pt, weight: "regular", fill: rgb("#3a3a3a"), style: "italic")[
    Transition Curves for Safe Rule-to-ML Graduation
  ]
  #v(1.4em)
  #text(size: 11pt)[
    *Benjamin Booth* — B-Tree Labs \
    Draft v0.1 · 2026-04-26 \
    Target: arXiv (cs.LG / cs.SE), 2026-05-13
  ]
  #v(0.6em)
  #text(size: 9.5pt, fill: gray.darken(20%))[
    Code + data: #link("https://github.com/b-tree-labs/dendra")[github.com/b-tree-labs/dendra] (Apache 2.0) \
    Patent pending: U.S. Provisional Patent Application No. 64/045,809, filed 2026-04-21
  ]
  #v(2em)
  #line(length: 30%, stroke: 0.6pt + gray)
  #v(2em)
]

// Pull in the pandoc-generated body, but skip its first heading (we already
// rendered the title above) by using a content selector.
#include "body.typ"
