"""CI workflow guard (ci-fix-agent rescue, 2026-09-28).

Race-corruption caught live: a merge stripped the ci.yml scaffolding and left
25 duplicated pytest run blocks (workflow parse-fail, 0s runs on main).
This guard pins the contract so the class is machine-caught in future:
  - ci.yml exists and has real scaffolding
  - exactly ONE pytest run line (not N duplicated blocks)
  - the pinned test-file list matches the suite files on disk (no silent drift)
"""

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _lines() -> list[str]:
    return WORKFLOW.read_text(encoding="utf-8").splitlines()


def test_workflow_exists_with_scaffolding() -> None:
    lines = _lines()
    assert WORKFLOW.is_file()
    assert lines[0].strip() == "name: CI"
    joined = "\n".join(lines)
    for marker in ("on:", "jobs:", "pull_request:", "runs-on:", "actions/checkout@"):
        assert marker in joined, f"missing scaffolding marker: {marker}"


def test_exactly_one_pytest_run_block() -> None:
    pytest_lines = [l for l in _lines() if "pytest" in l and "run:" in l]
    assert len(pytest_lines) == 1, (
        f"expected 1 pytest run line, found {len(pytest_lines)} — duplicated-block corruption"
    )


def test_pinned_list_matches_suite_on_disk() -> None:
    pytest_line = next(l for l in _lines() if "pytest" in l and "run:" in l)
    toks = pytest_line.split()
    assert toks[:4] == ["run:", "python3", "-m", "pytest"]
    assert toks[-1] == "-q"
    pinned = sorted(t.rsplit("/", 1)[-1] for t in toks[4:-1])
    scripts_root = Path(__file__).resolve().parent
    on_disk = sorted(p.name for p in scripts_root.glob("test_*.py"))
    assert sorted(pinned) == on_disk, "ci.yml test-file list drifted from disk"
