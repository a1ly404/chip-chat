"""Lane pins, global-deny escalation, and receipt-before-write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chip import lanes
from chip import store


def _claim(persona: str, scope: str, room: str = "dev") -> None:
    path = store.thread_path(room)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"agent": persona, "content": f"claim: {persona} {scope}"}) + "\n")


@pytest.fixture()
def pins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CHIP_LANE_GO_TICKET", raising=False)
    monkeypatch.delenv("CHIP_LANE_RETRO_LIVE", raising=False)
    roots = {}
    for persona, env in (
        ("homelabdev", "CHIP_LANE_HOMELAB_ROOT"),
        ("opsdev", "CHIP_LANE_OPS_ROOT"),
        ("buffrdev", "CHIP_LANE_BUFFR_ROOT"),
        ("chipchatdev", "CHIP_LANE_CHIP_ROOT"),
        ("pm", "CHIP_LANE_PM_ROOT"),
    ):
        root = tmp_path / persona
        root.mkdir()
        monkeypatch.setenv(env, str(root))
        roots[persona] = root
    return roots


def test_missing_pin_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHIP_CHAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CHIP_LANE_HOMELAB_ROOT", raising=False)
    with pytest.raises(FileNotFoundError):
        lanes.read_text(persona="homelabdev", relpath="a.txt")


def test_write_then_read_roundtrip(pins: dict[str, Path]) -> None:
    _claim("opsdev", "raz-298")
    lanes.write_text(
        persona="opsdev",
        relpath="notes/a.txt",
        content="oracle\n",
        ticket_id="RAZ-298",
        room="dev",
        scope="raz-298",
    )
    assert lanes.read_text(persona="opsdev", relpath="notes/a.txt") == "oracle\n"
    lines = [
        json.loads(line)
        for line in lanes.receipt_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    writes = [row for row in lines if row["op"] == "write"]
    assert writes[0]["phase"] == "start"
    assert writes[1]["phase"] == "complete"
    assert writes[0]["sha256"]


def test_write_without_ticket_refused(pins: dict[str, Path]) -> None:
    _claim("chipchatdev", "scope-a")
    with pytest.raises(PermissionError):
        lanes.write_text(
            persona="chipchatdev",
            relpath="a.txt",
            content="x",
            ticket_id="",
            room="dev",
            scope="scope-a",
        )


def test_write_without_claim_refused(pins: dict[str, Path]) -> None:
    with pytest.raises(PermissionError, match="claim_owner"):
        lanes.write_text(
            persona="homelabdev",
            relpath="a.txt",
            content="x",
            ticket_id="RAZ-278",
            room="dev",
            scope="raz-278",
        )


def test_media_deny_beats_homelab_root(pins: dict[str, Path]) -> None:
    _claim("homelabdev", "raz-278")
    rel = Path("mnt/nas/media/movie.mkv")
    with pytest.raises(PermissionError, match="global deny"):
        lanes.write_text(
            persona="homelabdev",
            relpath=str(rel),
            content="nope",
            ticket_id="RAZ-278",
            room="dev",
            scope="raz-278",
        )
    assert not (pins["homelabdev"] / rel).exists()


def test_receipt_log_cannot_be_unlinked_via_write(pins: dict[str, Path]) -> None:
    _claim("buffrdev", "raz-1")
    with pytest.raises(PermissionError, match="tool_receipts.jsonl"):
        lanes.write_text(
            persona="buffrdev",
            relpath="audit/tool_receipts.jsonl",
            content="",
            ticket_id="RAZ-1",
            room="dev",
            scope="raz-1",
        )


def test_infisical_refused_without_go(pins: dict[str, Path]) -> None:
    _claim("opsdev", "raz-94")
    with pytest.raises(PermissionError, match="infisical"):
        lanes.write_text(
            persona="opsdev",
            relpath="infisical/token.txt",
            content="nope",
            ticket_id="RAZ-94",
            room="dev",
            scope="raz-94",
        )


def test_env_file_hard_deny(pins: dict[str, Path]) -> None:
    with pytest.raises(PermissionError, match="global deny"):
        lanes.read_text(persona="chipchatdev", relpath=".env")


def test_path_escape_refused(pins: dict[str, Path]) -> None:
    _claim("buffrdev", "raz-1b")
    with pytest.raises(ValueError):
        lanes.write_text(
            persona="buffrdev",
            relpath="../outside.txt",
            content="x",
            ticket_id="RAZ-1",
            room="dev",
            scope="raz-1b",
        )


def test_live_retro_records_refusal(pins: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIP_LANE_RETRO_LIVE", "1")
    _claim("homelabdev", "raz-278b")
    with pytest.raises(PermissionError):
        lanes.write_text(
            persona="homelabdev",
            relpath="mnt/nas/media/x.mkv",
            content="x",
            ticket_id="RAZ-278",
            room="dev",
            scope="raz-278b",
        )
    retro = lanes.retro_path().read_text(encoding="utf-8")
    assert "global deny" in retro
    assert "RAZ-278" in retro


def test_allow_rule_cannot_outrank_media_deny() -> None:
    decision = lanes.decide(
        "/mnt/nas/media/movie.mkv",
        [("deny", "/mnt/nas/media"), ("allow", "/mnt/nas/media")],
    )
    assert decision == "deny"


def test_unlink_and_truncate_of_receipt_log_refused(pins: dict[str, Path], tmp_path: Path) -> None:
    ledger = tmp_path / "tool_receipts.jsonl"
    ledger.write_text('{"ok":1}\n', encoding="utf-8")
    for op in ("unlink", "truncate"):
        with pytest.raises(PermissionError, match="global deny"):
            lanes.refuse_ledger_mutation(ledger, op)
    assert ledger.read_text(encoding="utf-8") == '{"ok":1}\n'


def test_infisical_mismatched_go_ticket(pins: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _claim("opsdev", "raz-94")
    monkeypatch.setenv("CHIP_LANE_GO_TICKET", "RAZ-1")
    with pytest.raises(PermissionError, match="infisical"):
        lanes.write_text(
            persona="opsdev",
            relpath="infisical/token.txt",
            content="nope",
            ticket_id="RAZ-94",
            room="dev",
            scope="raz-94",
        )


def test_build_local_tag_and_learning_row(pins: dict[str, Path]) -> None:
    _claim("buffrdev", "raz-b")
    code = lanes.run_build(
        persona="buffrdev",
        tool_id="vite-build",
        room="dev",
        scope="raz-b",
        ticket_id="RAZ-B",
        runner=lambda: (0, "dist/index.js"),
    )
    assert code == 0
    row = json.loads(lanes.learning_path().read_text(encoding="utf-8").splitlines()[-1])
    assert row["exit_code"] == 0
    assert "duration_s" in row
    assert len(row["artifact_sha256"]) == 64
    assert row["reflection"].startswith("buffrdev vite-build")


def test_registry_push_and_compose_need_go(pins: dict[str, Path]) -> None:
    _claim("opsdev", "raz-d")
    for tool_id in ("registry-push", "deploy", "compose-up"):
        with pytest.raises(PermissionError, match="without GO"):
            lanes.run_build(
                persona="opsdev",
                tool_id=tool_id,
                room="dev",
                scope="raz-d",
                ticket_id="RAZ-D",
                runner=lambda: (0, ""),
            )


def test_docker_build_rejects_registry_tag(pins: dict[str, Path]) -> None:
    _claim("homelabdev", "raz-db")
    with pytest.raises(PermissionError, match="local-only"):
        lanes.run_build(
            persona="homelabdev",
            tool_id="docker-build",
            room="dev",
            scope="raz-db",
            ticket_id="RAZ-DB",
            docker_tag="ghcr.io/app:latest",
            runner=lambda: (0, ""),
        )


def test_one_build_per_lane(pins: dict[str, Path]) -> None:
    _claim("chipchatdev", "raz-c")
    lanes.acquire_build("chipchatdev")
    try:
        with pytest.raises(PermissionError, match="1 concurrent"):
            lanes.run_build(
                persona="chipchatdev",
                tool_id="lane-test",
                room="dev",
                scope="raz-c",
                ticket_id="RAZ-C",
                runner=lambda: (0, ""),
            )
    finally:
        lanes.release_build("chipchatdev")


def test_failed_build_files_retro_template(pins: dict[str, Path]) -> None:
    _claim("buffrdev", "raz-f")
    code = lanes.run_build(
        persona="buffrdev",
        tool_id="lane-test",
        room="dev",
        scope="raz-f",
        ticket_id="RAZ-F",
        runner=lambda: (1, "fail-log"),
    )
    assert code == 1
    retro = json.loads(lanes.retro_path().read_text(encoding="utf-8").splitlines()[-1])
    assert retro["attempted"] == "lane-test"
    assert "exit 1" in retro["why"]
    assert "RAZ-F" in retro["envelope"]


def test_pm_write_inside_root_and_refuse_outside(pins: dict[str, Path], tmp_path: Path) -> None:
    _claim("pm", "raz-pm")
    written = lanes.write_text(
        persona="pm",
        relpath="notes.txt",
        content="owned",
        ticket_id="RAZ-PM",
        room="dev",
        scope="raz-pm",
    )
    assert written.read_text(encoding="utf-8") == "owned"
    assert written.is_relative_to(pins["pm"])
    with pytest.raises(ValueError):
        lanes.write_text(
            persona="pm",
            relpath=str(tmp_path / "outside.txt"),
            content="no",
            ticket_id="RAZ-PM",
            room="dev",
            scope="raz-pm",
        )
    assert lanes.decide(
        "/mnt/nas/media/movie",
        [("allow", "/mnt/nas/media"), ("allow", "pm")],
    ) == "deny"
    with pytest.raises(PermissionError, match="boards/"):
        lanes.write_text(
            persona="pm",
            relpath="boards/dev.json",
            content="no",
            ticket_id="RAZ-PM",
            room="dev",
            scope="raz-pm",
        )


def test_build_writes_start_then_completion(pins: dict[str, Path]) -> None:
    _claim("buffrdev", "raz-r")
    lanes.run_build(
        persona="buffrdev",
        tool_id="vite-build",
        room="dev",
        scope="raz-r",
        ticket_id="RAZ-R",
        runner=lambda: (0, "dist/app.js"),
    )
    rows = [json.loads(line) for line in lanes.receipt_path().read_text(encoding="utf-8").splitlines() if line.strip()]
    builds = [row for row in rows if row["op"] == "build"]
    assert [row["phase"] for row in builds] == ["start", "complete"]
    assert builds[0]["tool_id"] == "vite-build"
    assert builds[1]["sha256"] != "incomplete"


def test_build_exception_still_completes_receipt(pins: dict[str, Path]) -> None:
    _claim("buffrdev", "raz-x")

    def boom():
        raise RuntimeError("died mid artifact")

    with pytest.raises(RuntimeError):
        lanes.run_build(
            persona="buffrdev",
            tool_id="docker-build",
            room="dev",
            scope="raz-x",
            ticket_id="RAZ-X",
            docker_tag="throwaway:local",
            runner=boom,
        )
    rows = [json.loads(line) for line in lanes.receipt_path().read_text(encoding="utf-8").splitlines() if line.strip()]
    builds = [row for row in rows if row["op"] == "build"]
    assert builds[0]["phase"] == "start"
    assert builds[-1]["phase"] == "complete"
    assert builds[-1]["sha256"] == "incomplete"


def test_second_build_files_retro(pins: dict[str, Path]) -> None:
    _claim("chipchatdev", "raz-cap")
    lanes.acquire_build("chipchatdev")
    try:
        with pytest.raises(PermissionError, match="1 concurrent"):
            lanes.run_build(
                persona="chipchatdev",
                tool_id="lane-test",
                room="dev",
                scope="raz-cap",
                ticket_id="RAZ-CAP",
                runner=lambda: (0, ""),
            )
    finally:
        lanes.release_build("chipchatdev")
    retro = lanes.retro_path().read_text(encoding="utf-8")
    assert "1 concurrent" in retro
    assert not lanes.receipt_path().exists()


def test_chipchatdev_read_wake_needs_claim(pins: dict[str, Path]) -> None:
    ok, reason = lanes.chipchatdev_wake_allowed("read scope:raz-w notes", "dev")
    assert ok is False
    assert "claim_owner" in reason
    _claim("chipchatdev", "raz-w")
    ok, reason = lanes.chipchatdev_wake_allowed("read scope:raz-w notes", "dev")
    assert ok is True
    assert reason == "read tier under claim"


def test_persona_allow_later_loses_to_global_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow rules sit later than nothing; global denies are appended and win."""
    monkeypatch.setenv("CHIP_LANE_GO_TICKET", "RAZ-1")
    media = lanes.decide(
        "/mnt/nas/media/movie.mkv",
        [("allow", "/mnt/nas/media")],
    )
    unlink = lanes.decide(
        "unlink tool_receipts.jsonl",
        [("allow", "unlink"), ("allow", "tool_receipts.jsonl")],
    )
    truncate = lanes.decide(
        "truncate tool_receipts.jsonl",
        [("allow", "truncate"), ("allow", "tool_receipts.jsonl")],
    )
    infisical = lanes.decide(
        "read infisical/token.txt GO: RAZ-94",
        [("allow", "infisical")],
        room_go="RAZ-94",
    )
    assert media == "deny"
    assert unlink == "deny"
    assert truncate == "deny"
    assert infisical == "deny"


def test_nonzero_tool_run_files_task_ledger_retro(pins: dict[str, Path]) -> None:
    from chip import task_ledger

    lanes.record_outcome(
        tool_id="pytest",
        persona="chipchatdev",
        exit_code=1,
        duration_s=0.02,
        artifact="fail",
        reflection="chipchatdev pytest exited 1",
    )
    row = json.loads(lanes.learning_path().read_text(encoding="utf-8").splitlines()[-1])
    assert row["exit_code"] == 1
    assert "duration_s" in row
    assert len(row["artifact_sha256"]) == 64
    assert row["reflection"] == "chipchatdev pytest exited 1"
    retro = json.loads(task_ledger.retro_tickets_path().read_text(encoding="utf-8").splitlines()[-1])
    assert retro["task_id"] == "pytest"
    assert "exited 1" in retro["claimed"]
    assert retro["catch"]


def test_prior_session_false_dirty_tree_retro(pins: dict[str, Path]) -> None:
    from chip import task_ledger

    task_ledger.file_retro_ticket(
        task_id="RETRO-PRIOR-SESSION",
        claimed="Dirty uncommitted tree, and lane access absent from the checkout.",
        verified="Working tree was clean at d6a88a6. Lane access was already on origin/main at 07b3225.",
        catch="Sitreps must run git status and name the commit before describing dirty files or missing lane-access.",
    )
    retro = json.loads(task_ledger.retro_tickets_path().read_text(encoding="utf-8").splitlines()[-1])
    assert retro["task_id"] == "RETRO-PRIOR-SESSION"
    assert "d6a88a6" in retro["verified"]
    assert "07b3225" in retro["verified"]
    assert "git status" in retro["catch"]


def test_chipchatdev_build_wake_needs_named_go(pins: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _claim("chipchatdev", "raz-g")
    ok, reason = lanes.chipchatdev_wake_allowed("vite build scope:raz-g", "dev")
    assert ok is False
    assert "GO" in reason
    monkeypatch.setenv("CHIP_LANE_GO_TICKET", "RAZ-G")
    ok, reason = lanes.chipchatdev_wake_allowed("GO: RAZ-G vite build scope:raz-g", "dev")
    assert ok is True
