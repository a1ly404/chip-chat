"""The 11:25 crontab prefixes env onto `cd` only. The wrapper must still see paths."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "bin" / "chip-standup-morning"


def _stub_python(bin_dir: Path) -> None:
    stub = bin_dir / "python3"
    stub.write_text(
        "#!/bin/sh\n"
        'printf "DATA=%s\\nSIGNAL=%s\\nTZ=%s\\n" '
        '"$CHIP_CHAT_DATA_DIR" "$CHIP_CHAT_STANDUP_SIGNAL_FILE" "$TZ"\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _run(cmd: str, env: dict[str, str]) -> str:
    proc = subprocess.run(
        ["bash", "-c", cmd],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_cron_prefix_on_cd_still_exports_signal_paths(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub_python(bindir)
    env = os.environ.copy()
    env.pop("CHIP_CHAT_DATA_DIR", None)
    env.pop("CHIP_CHAT_STANDUP_SIGNAL_FILE", None)
    env.pop("TZ", None)
    env["HOME"] = str(tmp_path / "home")
    env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
    # Verbatim shape from docs/ops/cron-env-audit-20260930.md (paths swapped).
    lost = "/should-not-reach-the-script"
    out = _run(
        f"CHIP_CHAT_STANDUP_SIGNAL_FILE={lost}/standup_signal.json "
        f"CHIP_CHAT_DATA_DIR={lost} "
        f"cd {ROOT} && ./bin/chip-standup-morning",
        env,
    )
    data = str(tmp_path / "home" / "appdata" / "chip-chat")
    assert f"DATA={data}\n" in out
    assert f"SIGNAL={data}/standup_signal.json\n" in out
    assert "TZ=America/Los_Angeles\n" in out
    assert lost not in out


def test_preset_env_wins_over_wrapper_fallbacks(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub_python(bindir)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
    env["CHIP_CHAT_DATA_DIR"] = "/keep-data"
    env["CHIP_CHAT_STANDUP_SIGNAL_FILE"] = "/keep-signal"
    env["TZ"] = "UTC"
    out = _run(f"cd {ROOT} && ./bin/chip-standup-morning", env)
    assert "DATA=/keep-data\n" in out
    assert "SIGNAL=/keep-signal\n" in out
    assert "TZ=UTC\n" in out
