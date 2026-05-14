# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``scripts/coverage_ratchet.py``.

The script is invoked from CI and ship-check; its exit codes and rule
semantics are the contract. These tests exercise each rule independently
against synthetic coverage.json + coverage_floors.json fixtures.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "coverage_ratchet.py"


def _make_coverage_json(files: dict[str, float], total: float) -> dict:
    """Synthesize a coverage.py JSON report shape."""
    return {
        "totals": {"percent_covered": total},
        "files": {p: {"summary": {"percent_covered": v}} for p, v in files.items()},
    }


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke the script with cwd=tmp_path so it reads the fixtures there."""
    # Patch the script to read from cwd by invoking with PYTHONPATH=cwd.
    # The script hard-codes REPO_ROOT relative to its own location, so we
    # copy the script into tmp_path's `scripts/` dir for true isolation.
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "coverage_ratchet.py").write_bytes(SCRIPT.read_bytes())
    return subprocess.run(
        [sys.executable, str(scripts_dir / "coverage_ratchet.py"), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def lab(tmp_path: Path):
    """Yield a helper that writes coverage.json + coverage_floors.json."""

    def _write(current: dict[str, float], total: float, floors: dict | None):
        (tmp_path / "coverage.json").write_text(json.dumps(_make_coverage_json(current, total)))
        if floors is not None:
            (tmp_path / "coverage_floors.json").write_text(json.dumps(floors))

    return tmp_path, _write


class TestCheckMode:
    def test_green_when_all_above_floor(self, lab):
        tmp, write = lab
        write(
            current={"src/postrule/a.py": 90.0, "src/postrule/b.py": 80.0},
            total=85.0,
            floors={"TOTAL": 85.0, "src/postrule/a.py": 90.0, "src/postrule/b.py": 80.0},
        )
        r = _run(tmp)
        assert r.returncode == 0, r.stderr
        assert "GREEN" in r.stdout

    def test_r1_regression_fails(self, lab):
        tmp, write = lab
        write(
            current={"src/postrule/a.py": 80.0},  # dropped from 90
            total=80.0,
            floors={"TOTAL": 90.0, "src/postrule/a.py": 90.0},
        )
        r = _run(tmp)
        assert r.returncode == 1
        assert "R1" in r.stderr
        assert "regressed" in r.stderr

    def test_r2_low_cov_must_have_buffer(self, lab):
        tmp, write = lab
        # cli.py at floor — needs floor + 5pp.
        write(
            current={"src/postrule/cli.py": 48.53},
            total=48.53,
            floors={"TOTAL": 48.53, "src/postrule/cli.py": 48.53},
        )
        r = _run(tmp)
        assert r.returncode == 1
        assert "R2" in r.stderr
        assert "5pp buffer" in r.stderr

    def test_r2_passes_when_buffer_satisfied(self, lab):
        tmp, write = lab
        # floor is 48.53, current is 53.53 — exactly at floor + 5.
        write(
            current={"src/postrule/cli.py": 53.53},
            total=53.53,
            floors={"TOTAL": 53.53, "src/postrule/cli.py": 48.53},
        )
        r = _run(tmp)
        assert r.returncode == 0, r.stderr

    def test_r2_does_not_apply_to_high_cov_files(self, lab):
        tmp, write = lab
        # 80% file with floor at 80%. R2 doesn't apply (floor >= 70).
        # R1 still passes — current == floor.
        write(
            current={"src/postrule/a.py": 80.0},
            total=80.0,
            floors={"TOTAL": 80.0, "src/postrule/a.py": 80.0},
        )
        r = _run(tmp)
        assert r.returncode == 0, r.stderr

    def test_r3_new_file_below_minimum_fails(self, lab):
        tmp, write = lab
        # b.py is new (not in floors) and below 60%.
        write(
            current={"src/postrule/a.py": 90.0, "src/postrule/b.py": 50.0},
            total=70.0,
            floors={"TOTAL": 70.0, "src/postrule/a.py": 90.0},
        )
        r = _run(tmp)
        assert r.returncode == 1
        assert "R3" in r.stderr
        assert "src/postrule/b.py" in r.stderr

    def test_r3_new_file_at_minimum_passes(self, lab):
        tmp, write = lab
        # New file at exactly 60% — passes R3.
        write(
            current={"src/postrule/a.py": 90.0, "src/postrule/b.py": 60.0},
            total=75.0,
            floors={"TOTAL": 75.0, "src/postrule/a.py": 90.0},
        )
        r = _run(tmp)
        assert r.returncode == 0, r.stderr
        assert "cleared the 60% bar" in r.stdout

    def test_total_regression_fails(self, lab):
        tmp, write = lab
        write(
            current={"src/postrule/a.py": 90.0},
            total=70.0,  # dropped from 85
            floors={"TOTAL": 85.0, "src/postrule/a.py": 90.0},
        )
        r = _run(tmp)
        assert r.returncode == 1
        assert "TOTAL" in r.stderr

    def test_floor_file_missing_from_run_fails(self, lab):
        tmp, write = lab
        # File in floors but not in current run (deleted file).
        write(
            current={"src/postrule/a.py": 90.0},
            total=90.0,
            floors={
                "TOTAL": 85.0,
                "src/postrule/a.py": 90.0,
                "src/postrule/deleted.py": 80.0,
            },
        )
        r = _run(tmp)
        assert r.returncode == 1
        assert "deleted.py" in r.stderr

    def test_missing_coverage_json_exits_2(self, lab):
        tmp, _ = lab
        # No coverage.json written — should exit 2 (not just 1).
        (tmp / "coverage_floors.json").write_text("{}")
        r = _run(tmp)
        assert r.returncode == 2
        assert "coverage.json" in r.stderr


class TestUpdateMode:
    def test_update_refuses_when_rules_violated(self, lab):
        tmp, write = lab
        write(
            current={"src/postrule/cli.py": 48.0},  # below floor
            total=48.0,
            floors={"TOTAL": 50.0, "src/postrule/cli.py": 48.53},
        )
        r = _run(tmp, "--update")
        assert r.returncode == 1
        assert "Refusing to --update" in r.stderr

    def test_update_writes_lagged_floors_for_low_cov_files(self, lab):
        tmp, write = lab
        # cli.py at 60% — should record floor at 55 (60 - 5pp buffer).
        write(
            current={"src/postrule/cli.py": 60.0, "src/postrule/a.py": 90.0},
            total=75.0,
            floors={"TOTAL": 75.0, "src/postrule/cli.py": 48.53, "src/postrule/a.py": 90.0},
        )
        r = _run(tmp, "--update")
        assert r.returncode == 0, r.stderr
        new_floors = json.loads((tmp / "coverage_floors.json").read_text())
        assert new_floors["src/postrule/cli.py"] == 55.0
        assert new_floors["src/postrule/a.py"] == 90.0

    def test_update_no_lag_for_files_at_or_above_threshold(self, lab):
        tmp, write = lab
        # File at 70% — exactly at threshold. R2 doesn't apply. No lag.
        write(
            current={"src/postrule/a.py": 70.0},
            total=70.0,
            floors={"TOTAL": 65.0, "src/postrule/a.py": 65.0},
        )
        r = _run(tmp, "--update")
        assert r.returncode == 0, r.stderr
        new = json.loads((tmp / "coverage_floors.json").read_text())
        assert new["src/postrule/a.py"] == 70.0


class TestBootstrap:
    def test_bootstrap_seeds_when_no_snapshot_exists(self, lab):
        tmp, write = lab
        write(
            current={"src/postrule/cli.py": 53.53, "src/postrule/a.py": 90.0},
            total=70.0,
            floors=None,  # no snapshot file
        )
        r = _run(tmp, "--bootstrap")
        assert r.returncode == 0, r.stderr
        new = json.loads((tmp / "coverage_floors.json").read_text())
        # Low-cov file: lagged by 5pp.
        assert new["src/postrule/cli.py"] == 48.53
        # High-cov file: no lag.
        assert new["src/postrule/a.py"] == 90.0
        assert new["TOTAL"] == 70.0

    def test_bootstrap_refuses_when_snapshot_exists(self, lab):
        tmp, write = lab
        write(
            current={"src/postrule/a.py": 90.0},
            total=90.0,
            floors={"TOTAL": 90.0, "src/postrule/a.py": 90.0},
        )
        r = _run(tmp, "--bootstrap")
        assert r.returncode == 1
        assert "Refusing to --bootstrap" in r.stderr

    def test_bootstrap_does_not_apply_r3(self, lab):
        tmp, write = lab
        # File at 30% — below R3's 60% bar. Bootstrap accepts it anyway.
        write(
            current={"src/postrule/junk.py": 30.0},
            total=30.0,
            floors=None,
        )
        r = _run(tmp, "--bootstrap")
        assert r.returncode == 0, r.stderr
        new = json.loads((tmp / "coverage_floors.json").read_text())
        # 30 - 5pp lag = 25.
        assert new["src/postrule/junk.py"] == 25.0
