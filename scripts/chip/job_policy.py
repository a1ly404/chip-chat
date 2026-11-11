"""Engine-native job policy: PM as team lead, operator-confirmed sudo rules, YOLO (parity D slice 1).

Roles
-----
* **PM** (actor ``pm``) is always the team lead: it is the only principal that may evaluate a job
  request. It approves ordinary (non-sudo) jobs that fit the declarative policy on its own.
* **Dev bots** (``dev:<name>``) *request* jobs. They never evaluate, approve or confirm anything.
* **Operator** is not a runtime principal at all: operator actions (confirm, decline, revoke, pin)
  require the operator key file (:class:`OperatorKey`), which the relay / job environment must not
  carry. No request field, job, PM or dev bot can name itself ``operator``.

Decision order (:func:`evaluate`) — first match wins, everything fails closed:

1. ``CHIP_JOB_POLICY`` off → deny.
2. Request shape: unknown fields, a non-PM actor, a PM/operator requester → deny.
3. Authority store hash chain broken (edited, removed, forged rows) → deny.
4. Declarative policy file changed since the operator pinned it → deny.
5. **Hard denies** (built in + plugin hooks): secret/credential access or exfiltration, deletes outside
   the declared target, inline privilege escalation (``sudo``/``su``/``doas`` in the argv), touching
   YOLO / rules / policy / enforcement from inside a job. Nothing overrides these (not YOLO, not a
   rule, not a confirm).
6. Job-type policy: command allow/deny, target allow/deny globs, hash-bound scripts, spend/time caps.
7. Not sudo → ``allow`` (PM approves within policy).
8. Sudo: job type ``sudo: never`` → deny; a saved operator rule that matches exactly → ``allow``;
   YOLO on → ``allow`` with a logged grant (reason, scope, expiry); otherwise → ``ask``: a pending
   operator confirm request (what, why, scope). Confirm runs the job *and* saves a durable rule so
   matching requests are auto-approved later. Decline and expiry are final for that request.

Storage (under ``CHIP_CHAT_DATA_DIR/policy/``):

* ``authority.jsonl`` — tamper-evident, hash-chained rows (rules added/revoked, policy
  pins, confirms/declines), Ed25519-signed. Only :class:`OperatorKey` holders can write a valid row.
* ``requests.jsonl`` — pending/decided operator confirm requests (event log).
* ``grants.jsonl`` — YOLO grants (reason, scope, expiry).
* ``audit.jsonl`` — one receipt per evaluation (command hash only, never raw argv).

Default off: ``CHIP_JOB_POLICY=1`` enables evaluation. ``CHIP_PM_YOLO`` (env) overrides the system
pack value ``values.pm_yolo``; both default false. This module decides; execution of ``allow``
decisions lives in :mod:`chip.policy_exec` (slice 2).
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import importlib
import json
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from chip import ed25519_ref
from chip.config import data_dir

FLAG = "CHIP_JOB_POLICY"
YOLO_ENV = "CHIP_PM_YOLO"
YOLO_PACK_KEY = "pm_yolo"
KEY_FILE_ENV = "CHIP_POLICY_OPERATOR_KEY_FILE"
PUBKEY_ENV = "CHIP_POLICY_OPERATOR_PUBKEY"
POLICY_FILE_ENV = "CHIP_POLICY_FILE"
HOOKS_ENV = "CHIP_POLICY_DENY_HOOKS"
REQUEST_TTL_ENV = "CHIP_POLICY_REQUEST_TTL_S"
GRANT_TTL_ENV = "CHIP_PM_YOLO_GRANT_TTL_S"
# Markers that say "this process is a job / bot, not the operator shell".
IN_SYSTEM_MARKERS = ("CHIP_BGJOB_ID", "CHIP_JOB_ACTOR")
PM = "pm"
OPERATOR = "operator"
DEV_PREFIX = "dev:"
HARD_MAX_SECONDS = 3600
GENESIS = "0" * 64
VERDICTS = ("allow", "ask", "deny")

_lock = threading.Lock()
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,95}$", re.IGNORECASE)


class PolicyError(RuntimeError):
    """Refused operator action or malformed input (message is the reason)."""


class AuthorityTampered(PolicyError):
    """The authority store hash chain does not verify."""


# ------------------------------------------------------------------ env / settings


def _env(env: Mapping[str, str] | None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return _truthy(_env(env).get(FLAG, "0"))


def _pack_values() -> dict[str, Any]:
    try:
        from chip import pack_values

        return pack_values._values()
    except Exception:  # pragma: no cover - pack loading problems mean "no pack value"
        return {}


def yolo(env: Mapping[str, str] | None = None, pack: Mapping[str, Any] | None = None) -> bool:
    """YOLO mode: env ``CHIP_PM_YOLO`` (when set) beats pack ``values.pm_yolo``; default False."""
    raw = str(_env(env).get(YOLO_ENV, "")).strip()
    if raw:
        return _truthy(raw)
    values = _pack_values() if pack is None else pack
    return values.get(YOLO_PACK_KEY) is True


def _int_env(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    try:
        return min(max(int(str(env.get(name, "")).strip()), lo), hi)
    except ValueError:
        return default


def _now(now: datetime | None) -> datetime:
    n = now or datetime.now(timezone.utc)
    if n.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return n.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def policy_dir() -> Path:
    return data_dir() / "policy"


def authority_path() -> Path:
    return policy_dir() / "authority.jsonl"


def requests_path() -> Path:
    return policy_dir() / "requests.jsonl"


def grants_path() -> Path:
    return policy_dir() / "grants.jsonl"


def audit_path() -> Path:
    return policy_dir() / "audit.jsonl"


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


# ------------------------------------------------------------------ operator key


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def operator_pubkey(env: Mapping[str, str] | None = None) -> bytes | None:
    """Runtime trust root: the operator's Ed25519 public key (hex) from ``CHIP_POLICY_OPERATOR_PUBKEY``."""
    raw = str(_env(env).get(PUBKEY_ENV, "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", raw):
        return None
    return bytes.fromhex(raw)


class OperatorKey:
    """Proof of operator presence: the operator's Ed25519 seed (64 hex chars in a key file).

    Load with :meth:`load`; it refuses inside a job/bot process, a malformed key, a key file stored
    inside the engine data dir (which jobs can write), and a key whose public half is not the
    configured runtime trust root.
    """

    __slots__ = ("_seed", "public", "key_id")

    def __init__(self, seed: bytes) -> None:
        if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
            raise PolicyError("operator key must be a 32-byte Ed25519 seed")
        self._seed = bytes(seed)
        self.public = ed25519_ref.public_key(self._seed)
        self.key_id = self.public.hex()[:12]

    @classmethod
    def generate(cls) -> "OperatorKey":
        return cls(os.urandom(32))

    def seed_hex(self) -> str:
        return self._seed.hex()

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> "OperatorKey":
        e = _env(env)
        for marker in IN_SYSTEM_MARKERS:
            if str(e.get(marker, "")).strip():
                raise PolicyError(f"operator actions are refused inside a job or bot process ({marker} set)")
        raw = str(e.get(KEY_FILE_ENV, "")).strip()
        if not raw:
            raise PolicyError(f"{KEY_FILE_ENV} is not set (operator shell only)")
        path = Path(raw).expanduser()
        if _inside(path, data_dir()):
            raise PolicyError("operator key file must live outside the engine data dir")
        if not path.is_file():
            raise PolicyError("operator key file not found")
        text = path.read_text(encoding="utf-8", errors="replace").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", text):
            raise PolicyError("operator key file must hold 64 hex chars")
        key = cls(bytes.fromhex(text))
        pub = operator_pubkey(e)
        if pub is None or not hmac.compare_digest(pub, key.public):
            raise PolicyError(f"operator key does not match {PUBKEY_ENV} (the runtime trust root)")
        return key

    def sign(self, body: str) -> str:
        return ed25519_ref.sign(self._seed, body.encode("utf-8")).hex()


# ------------------------------------------------------------------ authority store


def _row_body(row: Mapping[str, Any]) -> str:
    return canon({k: v for k, v in row.items() if k != "sig"})


def verify_chain(env: Mapping[str, str] | None = None, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Verify every row's link and Ed25519 signature; raise :class:`AuthorityTampered` on any break.

    Verification needs only the operator public key, so the runtime (PM, relay) can check the store
    but can never write a row that verifies. An empty store verifies without a key; a non-empty store
    without a configured public key does not (fail closed).
    """
    rows = _jsonl(authority_path()) if rows is None else rows
    if not rows:
        return rows
    pub = operator_pubkey(env)
    if pub is None:
        raise AuthorityTampered(f"authority store present but {PUBKEY_ENV} is not configured")
    prev = GENESIS
    for i, row in enumerate(rows):
        if row.get("seq") != i or row.get("prev") != prev:
            raise AuthorityTampered(f"authority chain broken at row {i}")
        sig = str(row.get("sig") or "")
        if not re.fullmatch(r"[0-9a-f]{128}", sig) or not ed25519_ref.verify(
            pub, _row_body(row).encode("utf-8"), bytes.fromhex(sig)
        ):
            raise AuthorityTampered(f"authority row {i} signature does not verify")
        prev = sha256_text(canon(row))
    return rows


def _authority_append(key: OperatorKey, event: str, payload: Mapping[str, Any], now: datetime,
                      env: Mapping[str, str] | None = None) -> dict[str, Any]:
    if not isinstance(key, OperatorKey):
        raise PolicyError("authority writes need the operator key")
    pub = operator_pubkey(env)
    if pub is None or not hmac.compare_digest(pub, key.public):
        raise PolicyError(f"operator key does not match {PUBKEY_ENV}")
    with _lock:
        rows = verify_chain(env)
        row: dict[str, Any] = {
            "seq": len(rows),
            "prev": sha256_text(canon(rows[-1])) if rows else GENESIS,
            "ts": _iso(now),
            "event": event,
            "by": f"{OPERATOR}:{key.key_id}",
            "payload": dict(payload),
        }
        row["sig"] = key.sign(_row_body(row))
        _append(authority_path(), row)
    return row


def active_rules(rows: list[dict[str, Any]] | None = None, now: datetime | None = None,
                 env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Active rules from rows that already passed :func:`verify_chain` (or verify now)."""
    rows = verify_chain(env) if rows is None else rows
    n = _now(now)
    rules: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = r.get("payload") or {}
        if r.get("event") == "rule_added":
            rules[p["id"]] = {**p, "added_at": r["ts"], "authority_seq": r["seq"]}
        elif r.get("event") == "rule_revoked":
            rules.pop(p.get("id"), None)
    out = []
    for rule in rules.values():
        exp = rule.get("expires_at")
        if exp and _parse(exp) <= n:
            continue
        out.append(rule)
    return sorted(out, key=lambda x: x["added_at"])


def pinned_policy_sha(rows: list[dict[str, Any]]) -> str | None:
    sha = None
    for r in rows:
        if r.get("event") == "policy_pinned":
            sha = (r.get("payload") or {}).get("sha256")
    return sha


# ------------------------------------------------------------------ requests


REQUEST_FIELDS = {
    "job_id", "requested_by", "job_type", "action", "command", "target", "context", "sudo",
    "reason", "script_path", "script_sha256", "max_spend_usd", "max_seconds",
}


@dataclass(frozen=True)
class JobRequest:
    job_id: str
    requested_by: str
    job_type: str
    action: str
    command: tuple[str, ...]
    target: str = ""
    context: str = ""
    sudo: bool = False
    reason: str = ""
    script_path: str = ""
    script_sha256: str = ""
    max_spend_usd: float = 0.0
    max_seconds: int = 0

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "JobRequest":
        if not isinstance(raw, Mapping):
            raise PolicyError("request must be an object")
        unknown = sorted(set(raw) - REQUEST_FIELDS)
        if unknown:
            raise PolicyError(f"unknown request field(s): {', '.join(unknown)}")
        cmd = raw.get("command")
        if not isinstance(cmd, (list, tuple)) or not cmd or not all(isinstance(x, str) and x for x in cmd):
            raise PolicyError("command must be a non-empty list of strings")
        if not isinstance(raw.get("sudo", False), bool):
            raise PolicyError("sudo must be a boolean")
        for name in ("job_id", "requested_by", "job_type", "action"):
            if not isinstance(raw.get(name), str) or not raw.get(name):
                raise PolicyError(f"{name} is required")
        try:
            spend = float(raw.get("max_spend_usd") or 0.0)
            secs = int(raw.get("max_seconds") or 0)
        except (TypeError, ValueError) as exc:
            raise PolicyError("caps must be numbers") from exc
        return cls(
            job_id=str(raw["job_id"])[:96],
            requested_by=str(raw["requested_by"])[:96],
            job_type=str(raw["job_type"])[:64],
            action=str(raw["action"])[:96],
            command=tuple(str(x)[:2000] for x in cmd[:64]),
            target=str(raw.get("target") or "")[:500],
            context=str(raw.get("context") or "")[:200],
            sudo=bool(raw.get("sudo", False)),
            reason=str(raw.get("reason") or "")[:500],
            script_path=str(raw.get("script_path") or "")[:500],
            script_sha256=str(raw.get("script_sha256") or "").lower()[:64],
            max_spend_usd=spend,
            max_seconds=secs,
        )

    def command_sha256(self) -> str:
        return sha256_text(canon(list(self.command)))

    def scope(self) -> dict[str, Any]:
        """The exact scope a confirmed rule binds (no wildcards: confirm never widens)."""
        return {
            "job_type": self.job_type,
            "action": self.action,
            "target": self.target,
            "context": self.context,
            "command_sha256": self.command_sha256(),
            "script_path": self.script_path,
            "script_sha256": self.script_sha256,
            "sudo": self.sudo,
        }

    def digest(self) -> str:
        return sha256_text(canon({**self.scope(), "requested_by": self.requested_by}))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["command"] = list(self.command)
        return d


def _request_state() -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for row in _jsonl(requests_path()):
        rid = row.get("id")
        if row.get("event") == "created" and rid and rid not in state:
            state[rid] = {**row, "status": "pending"}
        elif rid in state and row.get("event") in {"confirmed", "declined", "expired"}:
            if state[rid]["status"] == "pending":
                state[rid]["status"] = row["event"]
                state[rid]["decided_at"] = row.get("ts")
    return state


def pending_requests(now: datetime | None = None) -> list[dict[str, Any]]:
    """Pending confirm requests; expired ones are closed (final) as a side effect."""
    n = _now(now)
    out = []
    for rid, st in _request_state().items():
        if st["status"] != "pending":
            continue
        if _parse(st["expires_at"]) <= n:
            _append(requests_path(), {"event": "expired", "id": rid, "ts": _iso(n)})
            continue
        out.append(st)
    return out


def _open_request(req: JobRequest, pm_reason: str, env: Mapping[str, str], now: datetime) -> tuple[str, str]:
    """Return (request_id, status) for an identical pending/declined request or a new one."""
    digest = req.digest()
    for st in pending_requests(now):
        if st.get("digest") == digest:
            return st["id"], "pending"
    for st in _request_state().values():
        if st.get("digest") == digest and st["status"] == "declined":
            if _parse(st.get("decided_at") or st["ts"]) + timedelta(hours=24) > now:
                return st["id"], "declined"
    ttl = _int_env(env, REQUEST_TTL_ENV, 86400, 60, 7 * 86400)
    rid = "pr-" + uuid.uuid4().hex[:12]
    _append(
        requests_path(),
        {
            "event": "created",
            "id": rid,
            "ts": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=ttl)),
            "digest": digest,
            "request": req.to_dict(),
            "scope": req.scope(),
            "pm_reason": pm_reason[:500],
        },
    )
    return rid, "pending"


# ------------------------------------------------------------------ declarative policy


def policy_file(env: Mapping[str, str] | None = None) -> Path | None:
    raw = str(_env(env).get(POLICY_FILE_ENV, "")).strip()
    return Path(raw).expanduser() if raw else None


def load_policy(env: Mapping[str, str] | None = None) -> tuple[dict[str, Any], str | None]:
    """(policy dict, sha256 of file bytes). No file configured → empty policy (nothing allowed)."""
    path = policy_file(env)
    if path is None:
        return {"job_types": {}}, None
    if not path.is_file():
        raise PolicyError("policy file not found")
    raw = path.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"policy file is not JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("job_types", {}), dict):
        raise PolicyError("policy file must be an object with a job_types object")
    return doc, hashlib.sha256(raw).hexdigest()


def _globs(spec: Mapping[str, Any], key: str) -> list[str]:
    raw = spec.get(key) or []
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _match_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, p) for p in patterns)


# ------------------------------------------------------------------ hard denies

_SECRET_PATH = re.compile(
    r"(^|[/\\~])(\.ssh|\.gnupg|\.aws|\.kube|\.docker[/\\]config\.json|\.netrc|\.git-credentials|\.pgpass"
    r"|id_(rsa|dsa|ecdsa|ed25519)\b|shadow$|gshadow$|sudoers)|(^|[/\\])\.env(\.[\w-]+)?$|\.(pem|key|p12|pfx|kdbx)$",
    re.IGNORECASE,
)
_SECRET_VAR = re.compile(r"\$\{?\w*(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|CREDENTIAL)\w*", re.IGNORECASE)
_ENV_DUMP = {"printenv", "env", "export", "declare", "set", "compgen"}
_EXFIL_TOOLS = {"nc", "ncat", "netcat", "socat", "telnet", "ftp", "tftp"}
_UPLOAD_FLAGS = ("-T", "--upload-file", "-F", "--form", "--data-binary", "--post-file", "--body-file")
_ESCALATE = {"sudo", "su", "doas", "pkexec", "runas", "gsudo", "setcap", "chroot"}
_DELETE = {"rm", "rmdir", "unlink", "shred", "del", "erase", "rd", "remove-item", "truncate", "wipe"}
_POLICY_TOKENS = re.compile(
    r"CHIP_PM_YOLO|\bpm_yolo\b|CHIP_JOB_POLICY|CHIP_POLICY_|job_policy|authority\.jsonl|\bchip\s+policy\b",
    re.IGNORECASE,
)
_SHELLS = {"sh", "bash", "zsh", "dash", "fish", "pwsh", "powershell", "cmd", "python", "python3", "perl", "ruby", "node"}

HardDeny = Callable[[JobRequest], "str | None"]
_HOOKS: list[tuple[str, HardDeny]] = []


def register_hard_deny(name: str, fn: HardDeny) -> None:
    """Plugins add hard denies here (they can only add refusals, never allow anything)."""
    if not callable(fn):
        raise TypeError("hard deny hook must be callable")
    _HOOKS.append((str(name), fn))


def clear_hooks() -> None:
    """Test helper."""
    _HOOKS.clear()


def _env_hooks(env: Mapping[str, str]) -> list[tuple[str, HardDeny]]:
    out = []
    for spec in [s.strip() for s in str(env.get(HOOKS_ENV, "")).split(",") if s.strip()]:
        mod, _, attr = spec.partition(":")
        try:
            fn = getattr(importlib.import_module(mod), attr or "hard_deny")
        except Exception:
            out.append((spec, lambda _r, s=spec: f"hook {s} failed to load"))
            continue
        out.append((spec, fn))
    return out


def _base(token: str) -> str:
    name = re.split(r"[/\\]", token)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _protected_paths(env: Mapping[str, str]) -> list[Path]:
    paths = [policy_dir(), Path(__file__), Path(__file__).with_name("policy_exec.py"), Path(ed25519_ref.__file__)]
    for name in (KEY_FILE_ENV, POLICY_FILE_ENV):
        raw = str(env.get(name, "")).strip()
        if raw:
            paths.append(Path(raw).expanduser())
    return paths


def _touches(token: str, protected: list[Path]) -> bool:
    if not token or token.startswith("-"):
        return False
    try:
        p = Path(token).expanduser()
    except (OSError, ValueError):  # pragma: no cover
        return False
    return any(_inside(p, prot) for prot in protected)


def _contains_protected(token: str, protected: list[Path]) -> bool:
    try:
        p = Path(token).expanduser()
    except (OSError, ValueError):  # pragma: no cover
        return False
    return any(_inside(prot, p) for prot in protected)


def builtin_hard_deny(req: JobRequest, env: Mapping[str, str] | None = None) -> str | None:
    """Generic hard denies. Return a reason or None."""
    e = _env(env)
    argv = list(req.command)
    exe = _base(argv[0])
    blob = " ".join(argv)
    everything = [*argv, req.target, req.script_path, req.context, req.action]
    # 1) inline privilege escalation: sudo only via request.sudo + PM/operator decision.
    for tok in argv:
        if _base(tok) in _ESCALATE:
            return "inline privilege escalation (sudo/su/doas in command); request sudo through policy"
    # 2) YOLO / rules / policy / enforcement edits from inside a job.
    for tok in [*everything, blob]:
        if _POLICY_TOKENS.search(tok or ""):
            return "touches YOLO / rules / policy settings from inside a job"
    protected = _protected_paths(e)
    for tok in [*argv[1:], req.target, req.script_path]:
        if _touches(tok, protected):
            return "touches the policy store, operator key, policy file or enforcement code"
    # 3) credential / secret access and exfiltration.
    for tok in everything:
        if tok and (_SECRET_PATH.search(tok) or _SECRET_VAR.search(tok)):
            return "credential/secret access"
    if exe in _ENV_DUMP and len(argv) <= 2:
        return "environment dump (secret exfiltration risk)"
    if exe in _EXFIL_TOOLS:
        return "raw network tool (exfiltration risk)"
    if exe in {"curl", "wget", "scp", "rsync", "sftp"}:
        if any(t in _UPLOAD_FLAGS or t.startswith(_UPLOAD_FLAGS) for t in argv) or re.search(r"(^|\s)-d\s*@|--data\S*\s+@", blob):
            return "file upload from a job (exfiltration risk)"
        if exe in {"scp", "rsync", "sftp"} and any(":" in t and not t.startswith("-") for t in argv[1:]):
            return "remote copy from a job (exfiltration risk)"
    if exe in _SHELLS and any(t in {"-c", "-e", "/c", "-Command", "-command"} for t in argv[1:]):
        return "inline shell/interpreter code (use a hash-bound script instead)"
    # 4) deletes outside the declared target.
    deleting = exe in _DELETE or (exe == "find" and "-delete" in argv) or (exe == "git" and "clean" in argv[1:3])
    if deleting:
        tgt = req.target.strip()
        if not tgt or not (tgt.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", tgt)):
            return "delete without an absolute target path"
        tparts = PurePosixPath(tgt.replace("\\", "/")).parts
        if len(tparts) < 3:
            return "delete target too broad"
        paths = [t for t in argv[1:] if not t.startswith("-") and t != "clean"]
        if exe == "find":
            paths = paths[:1]
        if exe == "git" and not paths:
            return "git clean outside an explicit target path"
        for t in paths:
            if "*" in t or "?" in t or t in {"~", "/", "."}:
                return "wildcard or root delete"
            parts = PurePosixPath(t.replace("\\", "/")).parts
            if ".." in parts or parts[: len(tparts)] != tparts:
                return f"delete outside the declared target: {t[:120]}"
            if _contains_protected(t, protected):
                return "delete would remove the policy store, operator key, policy file or enforcement code"
        if _contains_protected(tgt, protected):
            return "delete target contains the policy store, operator key, policy file or enforcement code"
    # 5) plugin hooks (fail closed on error).
    for name, fn in [*_HOOKS, *_env_hooks(e)]:
        try:
            why = fn(req)
        except Exception as exc:
            return f"hard deny hook {name} errored ({type(exc).__name__})"
        if why:
            return f"{name}: {str(why)[:200]}"
    return None


# ------------------------------------------------------------------ evaluation


@dataclass
class Decision:
    verdict: str
    reason: str
    job_id: str = ""
    rule_id: str = ""
    request_id: str = ""
    grant_id: str = ""
    yolo: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", {}, None)} | {"verdict": self.verdict, "yolo": self.yolo}


def _audit(dec: Decision, req: JobRequest | None, actor: str, now: datetime) -> None:
    row: dict[str, Any] = {"ts": _iso(now), "actor": actor[:96], **dec.to_dict()}
    if req is not None:
        row.update(
            requested_by=req.requested_by,
            job_type=req.job_type,
            action=req.action,
            target=req.target,
            context=req.context,
            sudo=req.sudo,
            command_sha256=req.command_sha256(),
        )
    _append(audit_path(), row)


def _script_ok(req: JobRequest, spec: Mapping[str, Any]) -> str | None:
    if not req.script_path:
        return None
    path = Path(req.script_path).expanduser()
    if not path.is_file():
        return "script not found"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if not req.script_sha256 or not hmac.compare_digest(actual, req.script_sha256):
        return "script hash mismatch"
    pinned = spec.get("scripts")
    if isinstance(pinned, dict) and pinned:
        want = str(pinned.get(req.script_path) or "").lower()
        if not want or not hmac.compare_digest(want, actual):
            return "script not pinned in policy for this job type"
    if req.command[0] != req.script_path and req.script_path not in req.command:
        return "command does not run the declared script"
    return None


def _job_type_check(req: JobRequest, policy: Mapping[str, Any], env: Mapping[str, str]) -> tuple[str | None, dict[str, Any]]:
    spec = (policy.get("job_types") or {}).get(req.job_type)
    if not isinstance(spec, dict):
        return f"job type {req.job_type!r} not in policy", {}
    exe = _base(req.command[0])
    if _match_any(exe, _globs(spec, "deny_commands")):
        return f"command {exe!r} denied for {req.job_type}", spec
    allow_cmds = _globs(spec, "allow_commands")
    if not req.script_path and not _match_any(exe, allow_cmds):
        return f"command {exe!r} not allowed for {req.job_type}", spec
    if req.target and _match_any(req.target, _globs(spec, "deny_targets")):
        return "target denied by policy", spec
    allow_t = _globs(spec, "allow_targets")
    if allow_t and not _match_any(req.target, allow_t):
        return "target not allowed for this job type", spec
    if allow_t == [] and req.target and spec.get("require_target_allowlist", True):
        return "target not allowed for this job type", spec
    why = _script_ok(req, spec)
    if why:
        return why, spec
    from chip import bg_jobs

    caps = bg_jobs.caps(env)
    max_spend = min(float(spec.get("max_spend_usd", caps["max_spend_usd"])), caps["max_spend_usd"])
    max_secs = min(int(spec.get("max_seconds", 600)), HARD_MAX_SECONDS)
    if req.max_spend_usd < 0 or req.max_spend_usd > max_spend:
        return f"spend cap {req.max_spend_usd} above policy ceiling {max_spend}", spec
    if req.max_seconds < 0 or req.max_seconds > max_secs:
        return f"time cap {req.max_seconds}s above policy ceiling {max_secs}s", spec
    return None, spec


def _rule_matches(rule: Mapping[str, Any], req: JobRequest) -> bool:
    scope = req.scope()
    for k, v in scope.items():
        if rule.get(k) != v:
            return False
    if req.script_path:
        # the file on disk must still hash to the bound value (re-hashed by _script_ok already)
        return bool(rule.get("script_sha256")) and rule.get("script_sha256") == req.script_sha256
    return True


def evaluate(
    raw: Mapping[str, Any] | JobRequest,
    *,
    actor: str,
    pm_reason: str = "",
    env: Mapping[str, str] | None = None,
    pack: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> Decision:
    """PM decision for one job request (see module docstring). Always audited."""
    e = _env(env)
    n = _now(now)
    req: JobRequest | None = None
    yo = False

    def done(verdict: str, reason: str, **kw: Any) -> Decision:
        d = Decision(verdict, reason, job_id=req.job_id if req else "", yolo=yo, **kw)
        _audit(d, req, actor, n)
        return d

    if not enabled(e):
        return done("deny", "job policy disabled")
    try:
        req = raw if isinstance(raw, JobRequest) else JobRequest.from_dict(raw)
    except PolicyError as exc:
        return done("deny", f"malformed request: {exc}")
    if actor != PM:
        return done("deny", "only the PM (team lead) decides jobs; requesters cannot approve")
    who = req.requested_by.strip().lower()
    if who in {PM, OPERATOR} or who.startswith(OPERATOR) or not who.startswith(DEV_PREFIX) or len(who) <= len(DEV_PREFIX):
        return done("deny", "requester must be a dev bot (dev:<name>); PM/operator identities cannot request jobs")
    try:
        rows = verify_chain(e)
    except AuthorityTampered as exc:
        return done("deny", f"authority store tampered: {exc}")
    try:
        policy, sha = load_policy(e)
    except PolicyError as exc:
        return done("deny", f"policy unavailable: {exc}")
    if sha is not None and pinned_policy_sha(rows) != sha:
        return done("deny", "policy file changed or not pinned by the operator")
    why = builtin_hard_deny(req, e)
    if why:
        return done("deny", f"hard deny: {why}")
    why, spec = _job_type_check(req, policy, e)
    if why:
        return done("deny", why)
    if not req.sudo:
        return done("allow", "within policy (PM approved)")
    if str(spec.get("sudo", "ask")).lower() == "never":
        return done("deny", f"sudo never allowed for {req.job_type}")
    for rule in active_rules(rows, n):
        if _rule_matches(rule, req):
            return done("allow", "sudo: matches operator-confirmed rule", rule_id=rule["id"])
    yo = yolo(e, pack)
    if yo:
        ttl = _int_env(e, GRANT_TTL_ENV, 3600, 60, 86400)
        gid = "yg-" + uuid.uuid4().hex[:12]
        _append(
            grants_path(),
            {
                "id": gid,
                "ts": _iso(n),
                "expires_at": _iso(n + timedelta(seconds=ttl)),
                "job_id": req.job_id,
                "requested_by": req.requested_by,
                "reason": (pm_reason or req.reason or "no reason given")[:500],
                "scope": req.scope(),
            },
        )
        return done("allow", "sudo: YOLO grant by PM", grant_id=gid)
    rid, status = _open_request(req, pm_reason or req.reason, e, n)
    if status == "declined":
        return done("deny", "operator declined this exact request (final)", request_id=rid)
    return done("ask", "sudo needs operator confirm", request_id=rid)


def recheck(
    req: JobRequest, *, env: Mapping[str, str] | None = None, now: datetime | None = None
) -> tuple[str | None, dict[str, Any], list[dict[str, Any]]]:
    """Side-effect-free re-run of decision steps 1-6 at execution time (no audit, no request, no grant).

    Returns ``(refusal reason or None, job-type spec, verified authority rows)``. The executor calls
    this right before running so a revoked policy pin, a new hard deny, an edited script or a flag
    flipped off between decision and run still refuses.
    """
    e = _env(env)
    if not enabled(e):
        return "job policy disabled", {}, []
    try:
        rows = verify_chain(e)
    except AuthorityTampered as exc:
        return f"authority store tampered: {exc}", {}, []
    try:
        policy, sha = load_policy(e)
    except PolicyError as exc:
        return f"policy unavailable: {exc}", {}, rows
    if sha is not None and pinned_policy_sha(rows) != sha:
        return "policy file changed or not pinned by the operator", {}, rows
    why = builtin_hard_deny(req, e)
    if why:
        return f"hard deny: {why}", {}, rows
    why, spec = _job_type_check(req, policy, e)
    if why:
        return why, spec, rows
    return None, spec, rows


def sudo_basis_ok(
    req: JobRequest, *, rule_id: str = "", grant_id: str = "", rows: list[dict[str, Any]] | None = None,
    env: Mapping[str, str] | None = None, now: datetime | None = None,
) -> str | None:
    """Re-check that a sudo decision's basis still holds: an active matching operator rule, or an
    unexpired YOLO grant whose scope equals this request. Returns a refusal reason or None."""
    n = _now(now)
    if rule_id:
        for rule in active_rules(rows if rows is not None else verify_chain(env), n):
            if rule.get("id") == rule_id:
                return None if _rule_matches(rule, req) else "operator rule no longer matches this request"
        return "operator rule revoked or expired"
    if grant_id:
        for g in _jsonl(grants_path()):
            if g.get("id") != grant_id:
                continue
            if g.get("scope") != req.scope() or g.get("job_id") != req.job_id:
                return "YOLO grant scope does not match this request"
            if _parse(str(g.get("expires_at"))) <= n:
                return "YOLO grant expired"
            return None
        return "no such YOLO grant"
    return "sudo without an operator rule or YOLO grant"


# ------------------------------------------------------------------ operator actions


def _get_request(rid: str) -> dict[str, Any]:
    st = _request_state().get(rid)
    if st is None:
        raise PolicyError(f"no request: {rid}")
    return st


def confirm(request_id: str, key: OperatorKey, *, env: Mapping[str, str] | None = None,
            now: datetime | None = None, rule_ttl_s: int | None = None) -> dict[str, Any]:
    """Operator confirm: run-once approval for the job and a durable exact-scope rule."""
    if not isinstance(key, OperatorKey):
        raise PolicyError("confirm needs the operator key")
    e = _env(env)
    n = _now(now)
    pending_requests(n)  # closes expired requests first
    st = _get_request(request_id)
    if st["status"] != "pending":
        raise PolicyError(f"request {request_id} is {st['status']} (final)")
    req = JobRequest.from_dict(st["request"])
    if req.digest() != st.get("digest") or req.scope() != st.get("scope"):
        raise PolicyError("request record was altered after it was created")
    why = builtin_hard_deny(req, e)
    if why:
        raise PolicyError(f"hard deny cannot be confirmed: {why}")
    rule = {
        "id": "rule-" + uuid.uuid4().hex[:12],
        **req.scope(),
        "from_request": request_id,
        "requested_by": req.requested_by,
        "why": str(st.get("pm_reason") or "")[:500],
    }
    if rule_ttl_s:
        rule["expires_at"] = _iso(n + timedelta(seconds=int(rule_ttl_s)))
    _authority_append(key, "rule_added", rule, n, e)
    _authority_append(key, "confirmed", {"request_id": request_id, "digest": st["digest"], "job_id": req.job_id}, n, e)
    _append(requests_path(), {"event": "confirmed", "id": request_id, "ts": _iso(n), "rule_id": rule["id"]})
    return {"request_id": request_id, "job_id": req.job_id, "rule": rule}


def decline(request_id: str, key: OperatorKey, *, env: Mapping[str, str] | None = None,
            now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(key, OperatorKey):
        raise PolicyError("decline needs the operator key")
    n = _now(now)
    pending_requests(n)
    st = _get_request(request_id)
    if st["status"] != "pending":
        raise PolicyError(f"request {request_id} is {st['status']} (final)")
    _authority_append(key, "declined", {"request_id": request_id, "digest": st["digest"]}, n, env)
    _append(requests_path(), {"event": "declined", "id": request_id, "ts": _iso(n)})
    return {"request_id": request_id, "status": "declined"}


def revoke(rule_id: str, key: OperatorKey, *, env: Mapping[str, str] | None = None,
           now: datetime | None = None) -> dict[str, Any]:
    n = _now(now)
    if rule_id not in {r["id"] for r in active_rules(verify_chain(env), n)}:
        raise PolicyError(f"no active rule: {rule_id}")
    _authority_append(key, "rule_revoked", {"id": rule_id}, n, env)
    return {"rule_id": rule_id, "status": "revoked"}


def pin_policy(key: OperatorKey, *, env: Mapping[str, str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    _policy, sha = load_policy(env)
    if sha is None:
        raise PolicyError(f"{POLICY_FILE_ENV} is not set")
    _authority_append(key, "policy_pinned", {"sha256": sha, "path_name": policy_file(env).name}, _now(now), env)
    return {"sha256": sha}


def was_confirmed(request_id: str, env: Mapping[str, str] | None = None) -> bool:
    """True when the authority chain holds an operator confirm for this request (run-once proof)."""
    rows = verify_chain(env)
    return any(r.get("event") == "confirmed" and (r.get("payload") or {}).get("request_id") == request_id for r in rows)


# ------------------------------------------------------------------ job env + reporting

_SECRET_ENV_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE|CREDENTIAL|WEBHOOK", re.IGNORECASE)


def job_env(env: Mapping[str, str] | None = None, *, job_id: str = "", actor: str = "") -> dict[str, str]:
    """Environment for a job/dev-bot subprocess: no policy controls, no operator key, no secrets."""
    out = {}
    for k, v in _env(env).items():
        if k == YOLO_ENV or k.startswith(("CHIP_POLICY_", "CHIP_JOB_POLICY")) or _SECRET_ENV_NAME.search(k):
            continue
        out[k] = v
    out["CHIP_BGJOB_ID"] = job_id or "unknown"
    out["CHIP_JOB_ACTOR"] = actor or "job"
    return out


def grants(start_iso: str | None = None, end_iso: str | None = None) -> list[dict[str, Any]]:
    rows = _jsonl(grants_path())
    return [g for g in rows if (start_iso is None or g.get("ts", "") >= start_iso) and (end_iso is None or g.get("ts", "") < end_iso)]


def read_audit(limit: int = 50) -> list[dict[str, Any]]:
    return _jsonl(audit_path())[-max(1, int(limit)):]


def day_counts(start_iso: str, end_iso: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in _jsonl(audit_path()):
        if start_iso <= str(r.get("ts", "")) < end_iso:
            out[r.get("verdict", "?")] = out.get(r.get("verdict", "?"), 0) + 1
    return out


def pending_waiting(now: datetime | None = None) -> list[str]:
    """One "waiting on you" line per pending sudo confirm (for the PM summary)."""
    out = []
    for st in pending_requests(now):
        r = st.get("request") or {}
        out.append(
            f"confirm sudo for {r.get('requested_by')} job {r.get('job_id')}: {r.get('action')} on "
            f"{r.get('target') or '-'} ({str(st.get('pm_reason') or '')[:80]}) — `chip policy confirm {st['id']}`"
        )
    return out


def status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    e = _env(env)
    out: dict[str, Any] = {"enabled": enabled(e), "yolo": yolo(e), "policy_file": bool(policy_file(e))}
    try:
        rows = verify_chain(e)
        out.update(authority="ok", rows=len(rows), rules=len(active_rules(rows)), pinned=bool(pinned_policy_sha(rows)))
    except AuthorityTampered as exc:
        out.update(authority=f"tampered: {exc}")
    out["pending"] = len(pending_requests())
    return out


__all__ = [
    "AuthorityTampered", "Decision", "JobRequest", "OperatorKey", "PolicyError", "active_rules",
    "builtin_hard_deny", "confirm", "recheck", "sudo_basis_ok", "decline", "enabled", "evaluate", "grants", "job_env", "pending_requests",
    "pending_waiting", "pin_policy", "register_hard_deny", "revoke", "status", "verify_chain", "was_confirmed",
    "yolo",
]
