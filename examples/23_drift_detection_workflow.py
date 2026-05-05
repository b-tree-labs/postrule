# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Drift detection round-trip — what `dendra refresh` and `dendra
doctor` actually do.

Auto-lifters in v1 emit Switch subclasses into ``__dendra_generated__/``
sibling directories. Each generated file carries two hashes in its
header:

- The AST hash of the source function at generation time.
- The content hash of the generated body at generation time.

When the user later edits the source function, ``dendra refresh
--check`` exits non-zero (CI gate). When they edit the generated file
by hand, ``dendra refresh`` refuses to overwrite without ``--force``.

This script walks the cycle directly via the Python API so you can
read the contract end-to-end without a real lifter run.

Run:
    python examples/23_drift_detection_workflow.py
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory

from dendra.refresh import (
    DriftStatus,
    ast_hash,
    detect_drift,
    parse_generated_header,
    write_generated_file,
)

SOURCE_V1 = textwrap.dedent(
    """\
    def route_ticket(text: str) -> str:
        if "outage" in text.lower():
            return "page_oncall"
        if "feature" in text.lower():
            return "product"
        return "engineering"
    """
)

# v2: a developer adds a new branch ("question" → "support") without
# regenerating. Now the generated file is stale.
SOURCE_V2 = textwrap.dedent(
    """\
    def route_ticket(text: str) -> str:
        if "outage" in text.lower():
            return "page_oncall"
        if "feature" in text.lower():
            return "product"
        if "?" in text:
            return "support"
        return "engineering"
    """
)

GENERATED_BODY = textwrap.dedent(
    """\
    from dendra import Switch

    class RouteTicketSwitch(Switch):
        def _evidence_text_lower(self, text: str) -> str:
            return text.lower()

        def _rule(self, evidence) -> str:
            if "outage" in evidence.text_lower:
                return "page_oncall"
            if "feature" in evidence.text_lower:
                return "product"
            return "engineering"

        def _on_page_oncall(self, text): pass
        def _on_product(self, text): pass
        def _on_engineering(self, text): pass
    """
)


def section(label: str) -> None:
    print(f"\n=== {label} ===")


def main() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        src = root / "myapp" / "routing.py"
        gen = root / "myapp" / "__dendra_generated__" / "routing__route_ticket.py"

        section("1. Initial state — write the source + generate the wrapper")
        src.parent.mkdir(parents=True)
        src.write_text(SOURCE_V1)
        write_generated_file(
            gen,
            source_module="myapp.routing",
            source_function="route_ticket",
            source_ast_hash=ast_hash(SOURCE_V1),
            content=GENERATED_BODY,
            dendra_version="1.0.0",
        )
        status = detect_drift(src, "route_ticket", gen)
        print(f"  detect_drift -> {status.value}")
        assert status is DriftStatus.UP_TO_DATE

        section("2. Source drift — developer edits route_ticket without refreshing")
        src.write_text(SOURCE_V2)
        status = detect_drift(src, "route_ticket", gen)
        print(f"  detect_drift -> {status.value}")
        assert status is DriftStatus.SOURCE_DRIFT
        print("  CI would fail with `dendra refresh --check` (exit 1).")

        section("3. Re-generate — lifter would rewrite the file with the new hash")
        # Stand in for the real lifter run with a manual rewrite.
        write_generated_file(
            gen,
            source_module="myapp.routing",
            source_function="route_ticket",
            source_ast_hash=ast_hash(SOURCE_V2),
            content=GENERATED_BODY,  # body would change too, in real lifter run
            dendra_version="1.0.0",
        )
        status = detect_drift(src, "route_ticket", gen)
        print(f"  detect_drift -> {status.value}")
        assert status is DriftStatus.UP_TO_DATE

        section("4. User edits the GENERATED file by hand")
        gen.write_text(gen.read_text() + "\n# I added this manually\n")
        status = detect_drift(src, "route_ticket", gen)
        print(f"  detect_drift -> {status.value}")
        assert status is DriftStatus.USER_EDITED
        print("  `dendra refresh` would refuse without --force.")

        section("5. Source function deleted entirely (orphan)")
        src.write_text("# all the routing functions moved to another module\n")
        status = detect_drift(src, "route_ticket", gen)
        print(f"  detect_drift -> {status.value}")
        assert status is DriftStatus.ORPHANED
        print("  `dendra doctor` would flag this so the user can delete the generated file.")

        section("6. Generated file deleted (missing)")
        gen.unlink()
        # Restore the source so we can detect the missing-generated state.
        src.write_text(SOURCE_V2)
        status = detect_drift(src, "route_ticket", gen)
        print(f"  detect_drift -> {status.value}")
        assert status is DriftStatus.MISSING_GENERATED
        print("  `dendra refresh` would regenerate from the source.")

        section("7. Header inspection")
        # Re-create a generated file so we can read its header back.
        write_generated_file(
            gen,
            source_module="myapp.routing",
            source_function="route_ticket",
            source_ast_hash=ast_hash(SOURCE_V2),
            content=GENERATED_BODY,
            dendra_version="1.0.0",
        )
        header = parse_generated_header(gen.read_text())
        print(f"  Source: {header.source_module}:{header.source_function}")
        print(f"  Dendra version: {header.dendra_version}")
        print(f"  AST hash: {header.source_ast_hash[:16]}...")
        print(f"  Content hash: {header.generated_content_hash[:16]}...")

    print("\nAll five drift outcomes verified. The lifecycle is reproducible.")


if __name__ == "__main__":
    main()
