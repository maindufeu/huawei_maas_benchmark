"""Local credential persistence so you don't re-paste keys every session.

Stores the configured providers (including API keys) as PLAINTEXT JSON in
`.secrets.json` at the project root. This file is gitignored. Single-user, local
convenience only — do not commit it, and don't use on a shared machine.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict

from .providers import Provider

_PATH = os.path.join(os.path.dirname(__file__), "..", ".secrets.json")


def _to_dict(p: Provider) -> dict:
    return asdict(p)


def save(providers: list[Provider], judge: Provider | None = None) -> str:
    payload = {
        "providers": [_to_dict(p) for p in providers],
        "judge": _to_dict(judge) if judge else None,
    }
    with open(_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    try:
        os.chmod(_PATH, 0o600)  # restrict to owner
    except OSError:
        pass
    return os.path.abspath(_PATH)


def load() -> tuple[list[Provider], Provider | None]:
    if not os.path.exists(_PATH):
        return [], None
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return [], None
    providers = [Provider(**d) for d in payload.get("providers", [])]
    judge = Provider(**payload["judge"]) if payload.get("judge") else None
    return providers, judge


def clear() -> None:
    if os.path.exists(_PATH):
        os.remove(_PATH)


def exists() -> bool:
    return os.path.exists(_PATH)
