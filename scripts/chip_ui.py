#!/usr/bin/env python3
"""Path A local UI. Shells ./bin/chip. Does not call OpenRouter itself.

Bind: 127.0.0.1:8765
Live turns only when CHIP_CHAT_UI_LIVE=1. Otherwise the UI passes --mock.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from chip import law, store  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765
LAUNCHER = REPO / "bin" / "chip"


def list_rooms() -> list[str]:
    names = sorted(p.stem for p in law.CHANNELS_DIR.glob("*.md"))
    return names or ["demo"]


def recent_turns(room: str, n: int = 20) -> list[dict]:
    path = store.thread_path(law.safe_room_name(room) or room)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(
                {
                    "ts": rec.get("ts"),
                    "role": rec.get("role"),
                    "agent": rec.get("agent"),
                    "content": rec.get("content"),
                }
            )
    return rows[-max(1, n) :]


def send_via_cli(room: str, text: str) -> subprocess.CompletedProcess[str]:
    cmd = [str(LAUNCHER), "room", room, "--once", text]
    if os.environ.get("CHIP_CHAT_UI_LIVE", "").strip().lower() not in ("1", "true", "yes"):
        cmd.append("--mock")
    return subprocess.run(
        cmd,
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Chip Chat</title>
<style>
  body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem auto; max-width: 42rem; }
  pre { white-space: pre-wrap; background: #f4f4f4; padding: 0.75rem; }
  label { display: block; margin-top: 0.75rem; }
</style>
</head>
<body>
<h1>Chip Chat</h1>
<p>Local Path A. Sends through <code>bin/chip</code>.</p>
<form id="f">
  <label>Room <select id="room"></select></label>
  <label>Last turns <input id="n" type="number" value="12" min="1" max="100"/></label>
  <label>Message <input id="text" size="60" required/></label>
  <button type="submit">Send</button>
</form>
<pre id="log"></pre>
<script>
async function rooms() {
  const data = await (await fetch("/api/rooms")).json();
  const sel = document.getElementById("room");
  sel.innerHTML = "";
  for (const name of data.rooms) {
    const o = document.createElement("option");
    o.value = name; o.textContent = name; sel.appendChild(o);
  }
}
async function turns() {
  const room = document.getElementById("room").value;
  const n = document.getElementById("n").value || "12";
  const data = await (await fetch("/api/turns?room=" + encodeURIComponent(room) + "&n=" + n)).json();
  document.getElementById("log").textContent = (data.turns || []).map(t =>
    (t.role || "?") + (t.agent ? " (" + t.agent + ")" : "") + ": " + (t.content || "")
  ).join("\\n\\n") || "(no turns yet)";
}
document.getElementById("f").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = {
    room: document.getElementById("room").value,
    text: document.getElementById("text").value,
  };
  const res = await fetch("/api/send", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await res.json();
  document.getElementById("text").value = "";
  await turns();
  if (!res.ok) document.getElementById("log").textContent += "\\n\\nERROR " + (data.stderr || data.error || res.status);
});
document.getElementById("room").addEventListener("change", turns);
rooms().then(turns);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/rooms":
            self._json(200, {"rooms": list_rooms()})
            return
        if parsed.path == "/api/turns":
            qs = parse_qs(parsed.query)
            room = (qs.get("room") or ["demo"])[0]
            try:
                n = int((qs.get("n") or ["20"])[0])
            except ValueError:
                n = 20
            self._json(200, {"room": room, "turns": recent_turns(room, n)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/send":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        try:
            payload = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad json"})
            return
        room = str(payload.get("room") or "").strip()
        text = str(payload.get("text") or "").strip()
        if not room or not text:
            self._json(400, {"error": "room and text required"})
            return
        proc = send_via_cli(room, text)
        code = 200 if proc.returncode == 0 else 502
        self._json(
            code,
            {
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "turns": recent_turns(room, 20),
            },
        )


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Chip Chat UI http://{HOST}:{PORT}/  (mock unless CHIP_CHAT_UI_LIVE=1)", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
