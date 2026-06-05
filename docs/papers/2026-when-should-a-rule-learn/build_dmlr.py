# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# Build an anonymized DMLR LaTeX package from dmlr-draft.md.
# Reuses the vendored TMLR style files as a stand-in (DMLR's style is JMLR-derived
# and near-identical); swap in the official DMLR .sty before final submission.

import pathlib
import re
import shutil
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "dmlr-draft.md"
OUT = HERE / "dmlr-submission"
STYLE = HERE.parent.parent / "internal" / "tmlr-submission" / "tmlr"

OUT.mkdir(exist_ok=True)
for fn in ("tmlr.sty", "tmlr.bst", "fancyhdr.sty", "math_commands.tex"):
    shutil.copy(STYLE / fn, OUT / fn)

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


def anonymize(t: str) -> str:
    t = t.replace("B-Tree Labs", "[Anonymous Org]")
    t = t.replace("Benjamin Booth", "Anonymous Author")
    t = re.sub(r"github\.com/[^\s)]+", "[anonymized repository]", t)
    return t


abs, body = anonymize(abs), anonymize(body)
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

main = r"""\documentclass[10pt]{article}
\usepackage{tmlr}
\input{math_commands.tex}
\usepackage{microtype}
\usepackage{hyperref}
\usepackage{url}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs,longtable,array}
\usepackage{calc}
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
\newcommand{\sysname}{our framework}
\newcommand{\pkgname}{ourlib}
\title{TITLE}
\author{\AND Anonymous Author(s)\\Under review}
\begin{document}
\maketitle
\begin{abstract}
\input{abstract.tex}
\end{abstract}
\input{body.tex}
\end{document}
"""
(OUT / "main.tex").write_text(main.replace("TITLE", title), encoding="utf-8")
print(f"wrote DMLR package -> {OUT}  (title: {title})")
