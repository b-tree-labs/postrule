import pathlib
import re
import subprocess

src = pathlib.Path("paper-draft.md").read_text(encoding="utf-8")
lines = src.split("\n")

# 1-indexed boundaries (from grep): Abstract@11, Intro@23, Acks@734, Refs@740, AppA@818
abstract_md = "\n".join(lines[11:22]).strip()  # lines 12..22
sections_md = "\n".join(lines[22:733])  # 23..733  (§1..§11/Conclusion)
refs_md = "\n".join(lines[739:817])  # 740..817 References
appendix_md = "\n".join(lines[817:])  # 818..end Appendices
# (Acknowledgments 734..739 deliberately dropped — anonymization.)


def anonymize(t: str) -> str:
    t = t.replace("https://github.com/b-tree-labs/postrule", "[anonymized repository]")
    t = t.replace("github.com/b-tree-labs/postrule", "[anonymized repository]")
    t = t.replace("B-Tree Labs", "[Anonymous Org]")
    t = t.replace("Benjamin Booth", "Anonymous Author")
    t = t.replace("research@b-treeventures.com", "[anonymized]")
    # Patent provisional number → omit specifics under double-blind.
    t = re.sub(
        r"U\.S\. Provisional Patent Application No\.?\s*64/045,809[^.]*\.",
        "[patent application details omitted for anonymous review].",
        t,
    )
    t = re.sub(r"Copyright \(c\) 2026 .*?Apache-2\.0 licensed\.", "", t)
    # Self-citation in References (Booth 2026) → anonymized placeholder.
    t = re.sub(
        r"Booth,\s*B\.[^\n]*2026[^\n]*",
        "Anonymous. (2026). When Should a Rule Learn? Under review.",
        t,
    )
    return t


# Strip manual "N. " numbering from section headings so LaTeX auto-numbers.
sections_md = re.sub(r"^## \d+\.\s+", "## ", sections_md, flags=re.M)

abstract_md = anonymize(abstract_md)
sections_md = anonymize(sections_md)
refs_md = anonymize(refs_md)
appendix_md = anonymize(appendix_md)

# Assemble body markdown: sections + References, then a raw \appendix marker, then appendices.
body_md = (
    sections_md
    + "\n\n"
    + refs_md
    + "\n\n```{=latex}\n\\appendix\n```\n\n"
    + re.sub(r"^## Appendix [A-Z]:\s*", "## ", appendix_md, flags=re.M)
)
pathlib.Path("tmlr/_body.md").write_text(body_md, encoding="utf-8")
pathlib.Path("tmlr/_abstract.md").write_text(abstract_md, encoding="utf-8")

# pandoc: shift H2(sections)→H1(\section), H3→H2(\subsection).
subprocess.run(
    [
        "pandoc",
        "tmlr/_body.md",
        "-f",
        "markdown",
        "-t",
        "latex",
        "--shift-heading-level-by=-1",
        "-o",
        "tmlr/body.tex",
    ],
    check=True,
)
subprocess.run(
    ["pandoc", "tmlr/_abstract.md", "-f", "markdown", "-t", "latex", "-o", "tmlr/abstract.tex"],
    check=True,
)

# Macro-ize the product name: anonymous now, one-line flip for camera-ready.
b = pathlib.Path("tmlr/body.tex").read_text(encoding="utf-8")
n = b.count("Postrule")
b = b.replace("Postrule", r"\sysname{}")
pathlib.Path("tmlr/body.tex").write_text(b, encoding="utf-8")
print(f"body.tex: {len(b.splitlines())} lines, {n} Postrule→\\sysname")
print("abstract.tex:", len(pathlib.Path("tmlr/abstract.tex").read_text().splitlines()), "lines")
