"""One live OpenRouter completion on the chipchat key — escalation identity receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from chip import openrouter
from chip.config import REPO_ROOT

DEFAULT_MODEL = "z-ai/glm-5.3-flash"
LOG_PATH = REPO_ROOT / "docs" / "logs" / "escalation_identity_smoke.json"


def run(model: str) -> dict:
    key = openrouter.require_api_key()
    http_code = 200
    try:
        payload = openrouter.chat_completion(
            key,
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: escalation-smoke-ok"}],
            max_tokens=32,
        )
    except openrouter.ChipConfigError as exc:
        msg = str(exc)
        if "HTTP " in msg:
            part = msg.split("HTTP ", 1)[1].split(":", 1)[0].strip()
            try:
                http_code = int(part)
            except ValueError:
                http_code = 0
        text = ""
        payload = {}
    else:
        text = openrouter.extract_assistant_text(payload)
    sha = hashlib.sha256(text.encode()).hexdigest() if text else ""
    receipt = {
        "ts": time.time(),
        "model": model,
        "http_code": http_code,
        "response_sha256": sha,
        "response_preview": text[:120],
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Escalation identity smoke (one live completion).")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    receipt = run(args.model)
    print(json.dumps(receipt))
    return 0 if receipt.get("http_code") == 200 and receipt.get("response_preview") else 1


if __name__ == "__main__":
    sys.exit(main())
