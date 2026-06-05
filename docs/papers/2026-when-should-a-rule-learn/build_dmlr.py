# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# Build the DMLR LaTeX package from dmlr-draft.md using the official dmlr2e style
# (vendored in dmlr-submission/). DMLR is SINGLE-BLIND, so the package is NOT
# anonymized: the real author and the repository are shown.

import pathlib
import re
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "dmlr-draft.md"
OUT = HERE / "dmlr-submission"

OUT.mkdir(exist_ok=True)

raw = SRC.read_text(encoding="utf-8").split("\n")
title = next(ln[2:].strip() for ln in raw if ln.startswith("# "))

text = SRC.read_text(encoding="utf-8")
# Drop the italic working-draft note (build-process meta, not paper content).
text = re.sub(r"\*Working draft for DMLR.*?\*\n", "", text, flags=re.S)

# Split abstract / body.
abs = re.search(r"## Abstract\s+(.*?)\n## 1\.", text, flags=re.S).group(1).strip()
body = text[text.index("## 1.") :]

# Strip manual section numbering so LaTeX auto-numbers (## 1. / ### 1.1).
body = re.sub(r"^(#{2,3})\s+\d+(?:\.\d+)*\.?\s+", r"\1 ", body, flags=re.M)


# DMLR is single-blind: no anonymization. Author identity and the repository
# are shown (the previous TMLR-era anonymize() pass is intentionally removed).
(OUT / "_abstract.md").write_text(abs, encoding="utf-8")
(OUT / "_body.md").write_text(body, encoding="utf-8")

subprocess.run(
    [
        "pandoc",
        str(OUT / "_body.md"),
        "-f",
        "markdown",
        "-t",
        "latex",
        "--shift-heading-level-by=-1",
        "--no-highlight",
        "-o",
        str(OUT / "body.tex"),
    ],
    check=True,
)
subprocess.run(
    [
        "pandoc",
        str(OUT / "_abstract.md"),
        "-f",
        "markdown",
        "-t",
        "latex",
        "-o",
        str(OUT / "abstract.tex"),
    ],
    check=True,
)

# Normalize every figure include: drop pandoc's options (incl. the unsupported
# `alt={...}` accessibility key) and set a fixed width. Keeping width out of the
# markdown means the .md source previews cleanly (no literal `{width=...}` text)
# while the PDF figures are still sized.
_body = (OUT / "body.tex").read_text(encoding="utf-8")
_body = re.sub(
    r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}",
    r"\\includegraphics[width=0.92\\linewidth,keepaspectratio]{\1}",
    _body,
)
(OUT / "body.tex").write_text(_body, encoding="utf-8")

main = r"""\documentclass[twoside,11pt]{article}
% Official DMLR (JMLR-family) style. DMLR is SINGLE-BLIND: real author names.
\usepackage{dmlr2e}
\usepackage{lastpage}
\usepackage{microtype}
\usepackage{booktabs,longtable,array}
\usepackage{calc}
\usepackage{etoolbox}
\AtBeginEnvironment{longtable}{\small}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\providecommand{\pandocbounded}[1]{#1}
\providecommand{\real}[1]{#1}
\usepackage{newunicodechar}
\newunicodechar{→}{\ensuremath{\rightarrow}}
\newunicodechar{×}{\ensuremath{\times}}
\newunicodechar{≈}{\ensuremath{\approx}}
\newunicodechar{≤}{\ensuremath{\le}}
\newunicodechar{≥}{\ensuremath{\ge}}
\newunicodechar{−}{\ensuremath{-}}
\newunicodechar{ρ}{\ensuremath{\rho}}
\newunicodechar{ε}{\ensuremath{\epsilon}}
\newunicodechar{α}{\ensuremath{\alpha}}
\newunicodechar{σ}{\ensuremath{\sigma}}
\newunicodechar{∎}{\ensuremath{\blacksquare}}
\title{TITLE}
% DMLR is single-blind. Author block uses real identity (B-Tree Labs affiliation,
% kept separate from UT to preserve clean B-Tree IP ownership of Postrule).
\author{\name Benjamin Booth \email ben@b-treeventures.com \\
       \addr B-Tree Labs}
\editor{Under review for DMLR}
\def\openreview{\textit{(forum link assigned upon submission)}}
% {volume}{year}{pages}{date submitted}{date published}{paper id}{author-full-names}
\dmlrheading{1}{2026}{1-\pageref{LastPage}}{}{}{00-0000}{Benjamin Booth}
\ShortHeadings{When Should a Rule Learn?}{Booth}
\firstpageno{1}
\begin{document}
\maketitle
\begin{abstract}%
\input{abstract.tex}
\end{abstract}
\begin{keywords}
  classification, model selection, LLM cascades, data-centric machine learning, graduated autonomy
\end{keywords}
\input{body.tex}
\end{document}
"""
(OUT / "main.tex").write_text(main.replace("TITLE", title), encoding="utf-8")
print(f"wrote DMLR package -> {OUT}  (title: {title})")
