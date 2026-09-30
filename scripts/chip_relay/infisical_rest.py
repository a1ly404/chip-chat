"""Infisical REST client. Every request sends Cloudflare Access headers."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BOOT_CREDS = (
    "INFISICAL_CLIENT_ID",
    "INFISICAL_CLIENT_SECRET",
    "CF_ACCESS_CLIENT_ID",
    "CF_ACCESS_CLIENT_SECRET",
)
RUNTIME_SECRETS = (
    "DISCORD_CHIP_RELAY_TOKEN",
    "CHIP_WEBHOOK_SELFTEST",
    "CHIP_WEBHOOK_DEV",
    "OPENROUTER_CC_API_KEY",
)
OPTIONAL_SECRETS = ("DISCORD_V1_TOKEN",)
DEFAULT_API_URL = "https://infisical.razzormail.com"
DEFAULT_PROJECT_ID = "cc1062f8-f99b-408b-abbf-2236548d63af"


class InfisicalAccessError(RuntimeError):
    """Login did not return JSON. Caller must stop."""


def missing_boot(environ: dict[str, str] | None = None) -> list[str]:
    source = os.environ if environ is None else environ
    return [name for name in BOOT_CREDS if not source.get(name, "").strip()]


def _cf_headers(environ: dict[str, str]) -> dict[str, str]:
    return {
        "CF-Access-Client-Id": environ["CF_ACCESS_CLIENT_ID"],
        "CF-Access-Client-Secret": environ["CF_ACCESS_CLIENT_SECRET"],
    }


def _request(
    url: str,
    *,
    headers: dict[str, str],
    method: str = "GET",
    body: dict | None = None,
) -> tuple[int, str, str]:
    data = None if body is None else json.dumps(body).encode()
    headers = {**headers, "User-Agent": "chip-relay"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, resp.headers.get("Content-Type", ""), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", raw


def fetch_runtime_secrets(environ: dict[str, str] | None = None) -> dict[str, str]:
    source = dict(os.environ if environ is None else environ)
    missing = missing_boot(source)
    if missing:
        raise InfisicalAccessError("missing boot creds: " + ", ".join(missing))
    host = (source.get("INFISICAL_API_URL") or DEFAULT_API_URL).rstrip("/")
    headers = _cf_headers(source)
    headers["Content-Type"] = "application/json"
    status, ctype, raw = _request(
        host + "/api/v1/auth/universal-auth/login",
        headers=headers,
        method="POST",
        body={
            "clientId": source["INFISICAL_CLIENT_ID"],
            "clientSecret": source["INFISICAL_CLIENT_SECRET"],
        },
    )
    if "json" not in ctype.lower() or not raw.lstrip().startswith("{"):
        raise InfisicalAccessError(
            f"Infisical login returned non-JSON (HTTP {status}); stopping"
        )
    token = json.loads(raw).get("accessToken") or ""
    if not token:
        raise InfisicalAccessError(f"Infisical login HTTP {status} had no access token")
    project = source.get("INFISICAL_PROJECT_ID") or DEFAULT_PROJECT_ID
    read_headers = _cf_headers(source)
    read_headers["Authorization"] = "Bearer " + token
    query = (
        f"?workspaceId={project}&environment=prod&secretPath=/&viewSecretValue=true"
    )
    status, ctype, raw = _request(host + "/api/v3/secrets/raw" + query, headers=read_headers)
    if "json" not in ctype.lower() or not raw.lstrip().startswith("{"):
        raise InfisicalAccessError(f"Infisical secrets returned non-JSON (HTTP {status}); stopping")
    if status != 200:
        raise InfisicalAccessError(f"Infisical secrets HTTP {status}")
    found: dict[str, str] = {}
    for item in json.loads(raw).get("secrets") or []:
        key = item.get("secretKey")
        if key in RUNTIME_SECRETS or key in OPTIONAL_SECRETS:
            found[key] = str(item.get("secretValue") or "")
    absent = [name for name in RUNTIME_SECRETS if not found.get(name, "").strip()]
    if absent:
        raise InfisicalAccessError("missing runtime secrets: " + ", ".join(absent))
    return found


STANDUP_KEYS = (
    "CHIP_STANDUP_GITHUB_TOKEN",
    "LINEAR_API_KEY",
    "CHIP_ALERT_INGEST_TOKEN",
)


def fetch_keys(names: tuple[str, ...], environ: dict[str, str] | None = None) -> dict[str, str]:
    """Return present values for ``names``. Missing names are omitted. Login failure returns {}."""
    source = dict(os.environ if environ is None else environ)
    if missing_boot(source):
        return {}
    try:
        host = (source.get("INFISICAL_API_URL") or DEFAULT_API_URL).rstrip("/")
        headers = _cf_headers(source)
        headers["Content-Type"] = "application/json"
        status, ctype, raw = _request(
            host + "/api/v1/auth/universal-auth/login",
            headers=headers,
            method="POST",
            body={
                "clientId": source["INFISICAL_CLIENT_ID"],
                "clientSecret": source["INFISICAL_CLIENT_SECRET"],
            },
        )
        if "json" not in ctype.lower() or not raw.lstrip().startswith("{"):
            return {}
        token = json.loads(raw).get("accessToken") or ""
        if not token or status != 200:
            return {}
        project = source.get("INFISICAL_PROJECT_ID") or DEFAULT_PROJECT_ID
        read_headers = _cf_headers(source)
        read_headers["Authorization"] = "Bearer " + token
        query = (
            f"?workspaceId={project}&environment=prod&secretPath=/&viewSecretValue=true"
        )
        status, ctype, raw = _request(host + "/api/v3/secrets/raw" + query, headers=read_headers)
        if status != 200 or "json" not in ctype.lower():
            return {}
        wanted = set(names)
        found: dict[str, str] = {}
        for item in json.loads(raw).get("secrets") or []:
            key = item.get("secretKey")
            value = str(item.get("secretValue") or "")
            if key in wanted and value.strip():
                found[str(key)] = value
        return found
    except (InfisicalAccessError, OSError, ValueError, json.JSONDecodeError):
        return {}
