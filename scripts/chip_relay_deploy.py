"""Helpers for scripts/chip_relay_up.sh (health wait + stuck-state checks)."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from typing import Callable, Sequence

STUCK_CONTAINER_STATES = frozenset({"created", "exited", "dead"})

RunFn = Callable[..., subprocess.CompletedProcess[str]]


def container_is_stuck(status: str) -> bool:
    """True when Docker left chip-relay in a state that blocks a clean compose up."""
    return status.strip().lower() in STUCK_CONTAINER_STATES


def health_exec_python(port: int = 48082) -> str:
    return (
        "import urllib.request,sys;"
        f"r=urllib.request.urlopen('http://127.0.0.1:{port}/health',timeout=3);"
        "sys.exit(0 if r.status==200 else 1)"
    )


def wait_for_container_health(
    compose: Sequence[str],
    *,
    service: str = "chip-relay",
    timeout_sec: float = 300.0,
    poll_sec: float = 5.0,
    port: int = 48082,
    run: RunFn | None = None,
) -> None:
    """Poll GET /health inside the container until HTTP 200 or timeout."""
    if timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")
    if poll_sec <= 0:
        raise ValueError("poll_sec must be positive")

    runner = run if run is not None else _default_run
    deadline = time.monotonic() + timeout_sec
    exec_cmd = list(compose) + [
        "exec",
        "-T",
        service,
        "python3",
        "-c",
        health_exec_python(port),
    ]
    last_err = ""
    while time.monotonic() < deadline:
        try:
            proc = runner(exec_cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            last_err = "docker compose exec timed out"
        else:
            if proc.returncode == 0:
                return
            last_err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        time.sleep(poll_sec)
    raise TimeoutError(
        f"{service} /health on port {port} did not return 200 within {timeout_sec}s"
        + (f" (last: {last_err})" if last_err else "")
    )


def _default_run(cmd: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, **kwargs)  # type: ignore[arg-type]


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait for chip-relay container health.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(__import__("os").environ.get("CHIP_RELAY_UP_TIMEOUT", "300")),
        help="Seconds to wait for HTTP 200 on /health (default: CHIP_RELAY_UP_TIMEOUT or 300).",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=5.0,
        help="Seconds between health checks.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(__import__("os").environ.get("CHIP_RELAY_HEALTH_PORT", "48082")),
        help="Health port inside the container.",
    )
    parser.add_argument(
        "--service",
        default="chip-relay",
        help="Compose service name.",
    )
    parser.add_argument(
        "compose",
        nargs=argparse.REMAINDER,
        help="docker compose command prefix, e.g. docker compose",
    )
    args = parser.parse_args(argv)
    compose = [c for c in args.compose if c != "--"]
    if not compose:
        compose = ["docker", "compose"]
    try:
        wait_for_container_health(
            compose,
            service=args.service,
            timeout_sec=args.timeout,
            poll_sec=args.poll,
            port=args.port,
        )
    except TimeoutError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
