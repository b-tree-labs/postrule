#!/usr/bin/env python3
# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Download bundled GGUF models from HuggingFace, checksum, and prep for upload.

This script prepares the two GGUF files that ``postrule[bundled]`` lazy-
downloads from ``https://models.postrule.ai/`` once the CDN is provisioned.

It does NOT upload anything — that requires Cloudflare R2 credentials and
is intentionally a manual step (see the runbook at
``docs/working/models-cdn-runbook-2026-04-29.md``).

What it does:
    1. Download each GGUF from its HuggingFace ``resolve/main`` URL into
       ``./out/`` (or whatever ``--out`` you pass).
    2. Compute SHA-256 + size for each.
    3. Print the upload-ready filenames + the ``_REGISTRY`` block to paste
       back into ``src/postrule/bundled.py`` so ``size_bytes`` and ``sha256``
       become real values instead of placeholder zeros.
    4. Print the ``wrangler r2 object put`` commands for upload.

Usage::

    python scripts/cdn/prepare_models.py
    python scripts/cdn/prepare_models.py --out /tmp/postrule-models
    python scripts/cdn/prepare_models.py --skip-download   # if files are
                                                            # already local
    python scripts/cdn/prepare_models.py --only judge       # only one model

Estimated runtime: 5-15 minutes total depending on bandwidth.
Estimated disk: ~6.3 GB combined.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Source-of-truth registry — mirrors `src/postrule/bundled.py:_REGISTRY` but
# carries the HuggingFace download URLs the runtime registry doesn't.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    role: str  # "judge" or "classifier"
    canonical_filename: str  # lowercase, what we store on the CDN + cache
    hf_repo: str  # HuggingFace org/repo
    hf_filename: str  # filename in the repo's `resolve/main/` path
    description: str


SPECS: tuple[ModelSpec, ...] = (
    ModelSpec(
        role="judge",
        canonical_filename="qwen2.5-7b-instruct-q4_k_m.gguf",
        hf_repo="bartowski/Qwen2.5-7B-Instruct-GGUF",
        hf_filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        description="Qwen2.5-7B-Instruct (Q4_K_M) — judge default",
    ),
    ModelSpec(
        role="classifier",
        canonical_filename="gemma-2-2b-instruct-q4_k_m.gguf",
        hf_repo="bartowski/gemma-2-2b-it-GGUF",
        hf_filename="gemma-2-2b-it-Q4_K_M.gguf",
        description="Gemma-2-2B-Instruct (Q4_K_M) — classifier default",
    ),
)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _hf_url(spec: ModelSpec) -> str:
    return f"https://huggingface.co/{spec.hf_repo}/resolve/main/{spec.hf_filename}"


def _download(url: str, dest: Path, *, chunk_size: int = 1024 * 1024) -> int:
    """Stream-download ``url`` to ``dest`` with a one-line progress meter.

    Returns the byte count actually written. Resumable behavior is not
    implemented — partial files are deleted on interrupt; re-run from
    scratch.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "postrule-cdn-prep/1.0"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 — HTTPS only
        total = int(resp.headers.get("Content-Length", 0))
        written = 0
        with open(dest, "wb") as fh:
            while True:
                buf = resp.read(chunk_size)
                if not buf:
                    break
                fh.write(buf)
                written += len(buf)
                if total:
                    pct = 100.0 * written / total
                    sys.stderr.write(
                        f"\r  {dest.name}: {pct:5.1f}%  "
                        f"({written / 1e9:5.2f} / {total / 1e9:5.2f} GB)"
                    )
                    sys.stderr.flush()
        if total:
            sys.stderr.write("\n")
    return written


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedModel:
    spec: ModelSpec
    local_path: Path
    size_bytes: int
    sha256: str


def _render_registry_block(prepared: Iterable[PreparedModel]) -> str:
    """Render the ``_REGISTRY`` dict for src/postrule/bundled.py."""
    lines = ["_REGISTRY: dict[str, dict[str, Any]] = {"]
    for p in prepared:
        ollama_fallback = {
            "judge": "qwen2.5:7b",
            "classifier": "gemma2:2b",
        }[p.spec.role]
        lines.extend(
            [
                f"    {p.spec.role!r}: {{",
                f'        "filename": {p.spec.canonical_filename!r},',
                f'        "size_bytes": {p.size_bytes},',
                f'        "sha256": {p.sha256!r},',
                f'        "ollama_fallback": {ollama_fallback!r},',
                f'        "description": {p.spec.description!r},',
                "    },",
            ]
        )
    lines.append("}")
    return "\n".join(lines)


def _render_upload_commands(
    prepared: Iterable[PreparedModel],
    bucket: str,
) -> str:
    """Render `wrangler r2 object put` commands for each model."""
    lines = []
    for p in prepared:
        lines.append(
            f"wrangler r2 object put {bucket}/{p.spec.canonical_filename} "
            f'--file "{p.local_path}" --remote'
        )
    return "\n".join(lines)


def _render_summary(prepared: list[PreparedModel]) -> str:
    rows = [
        "| Role | Filename | Size | SHA-256 (truncated) |",
        "|---|---|---:|---|",
    ]
    for p in prepared:
        rows.append(
            f"| {p.spec.role} | `{p.spec.canonical_filename}` | "
            f"{p.size_bytes / 1e9:.2f} GB | `{p.sha256[:16]}…` |"
        )
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("./out/cdn-models"),
        help="Output directory for the downloaded GGUFs (default: ./out/cdn-models)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download; just hash whatever's already in --out.",
    )
    parser.add_argument(
        "--only",
        choices=[s.role for s in SPECS],
        help="Process only one model (judge or classifier).",
    )
    parser.add_argument(
        "--bucket",
        default="postrule-models",
        help=(
            "Cloudflare R2 bucket name for the rendered upload commands (default: postrule-models)."
        ),
    )
    args = parser.parse_args()

    specs = [s for s in SPECS if (args.only is None or s.role == args.only)]
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Output directory: {args.out.resolve()}")
    print(f"Models to prepare: {[s.role for s in specs]}")
    print()

    prepared: list[PreparedModel] = []
    for spec in specs:
        local = args.out / spec.canonical_filename
        if not args.skip_download:
            url = _hf_url(spec)
            print(f"Downloading {spec.role}: {url}")
            print(f"  → {local}")
            _download(url, local)
        elif not local.exists():
            print(
                f"  ✗ {local} not found; --skip-download requires the file already exists",
                file=sys.stderr,
            )
            return 2

        print(f"  Hashing {local} ...")
        sha = _sha256_file(local)
        size = local.stat().st_size
        prepared.append(PreparedModel(spec=spec, local_path=local, size_bytes=size, sha256=sha))
        print(f"  ✓ {size / 1e9:.2f} GB  sha256={sha[:32]}…")
        print()

    # ------- Output the artifacts the operator needs to act on -------
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print()
    print(_render_summary(prepared))
    print()
    print("=" * 72)
    print("Paste this _REGISTRY block into src/postrule/bundled.py")
    print("(replacing the existing _REGISTRY at line ~98):")
    print("=" * 72)
    print()
    print(_render_registry_block(prepared))
    print()
    print("=" * 72)
    print("Upload commands (requires `wrangler` CLI authenticated to your")
    print("Cloudflare account; bucket must exist before running these):")
    print("=" * 72)
    print()
    print(_render_upload_commands(prepared, bucket=args.bucket))
    print()
    print("=" * 72)
    print("After upload, verify with:")
    print("=" * 72)
    print()
    for p in prepared:
        print(f"  curl -I https://models.postrule.ai/{p.spec.canonical_filename}")
    print()
    print("Expected: 200 OK with Content-Length matching size above.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
