"""Manual watchlist — user-managed enable/disable of instruments.

Lets the user pick which of the registered instruments are actively watched
(dropdown + screener + warm loop) directly from the dashboard, instead of
editing ``QT_INSTRUMENTS`` in ``.env``. Persisted as a tiny JSON file under
``data_dir`` so the choice survives restarts.

Precedence: a static ``QT_INSTRUMENTS`` env limit (if set) always wins — the
dashboard cannot override an explicit ops-level lock. Otherwise, if the user has
saved a manual watchlist it wins over the daily auto-pick. Paper-safe: selecting
instruments never places, modifies, or cancels any order.
"""
from __future__ import annotations

import json
import os
import threading

from app.config import settings
from app.market.instruments import REGISTRY, set_universe

_LOCK = threading.Lock()


def env_locked() -> bool:
    """True when QT_INSTRUMENTS pins the universe at the ops level (env wins)."""
    return bool(os.environ.get("QT_INSTRUMENTS", "").strip())


def _path() -> str:
    d = settings.data_dir
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "watchlist.json")


def load() -> list[str]:
    """Return the saved manual watchlist (validated against the master), or []."""
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [s.upper() for s in data if isinstance(s, str) and s.upper() in REGISTRY]


def is_active() -> bool:
    """A manual watchlist governs the universe only when saved AND not env-locked."""
    return not env_locked() and bool(load())


def save(symbols: list[str]) -> list[str]:
    """Persist the enabled set and apply it to the active universe.

    An empty selection clears the manual watchlist (reverts to auto-pick / full
    master). Env-locked deployments ignore writes. Returns the resulting active
    universe.
    """
    if env_locked():
        return load()
    kept = [s.upper() for s in symbols if s.upper() in REGISTRY]
    with _LOCK:
        if not kept:
            try:
                os.remove(_path())
            except FileNotFoundError:
                pass
            return set_universe([])  # restore full master
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(kept, f)
        os.replace(tmp, _path())
    return set_universe(kept)


def apply_startup() -> None:
    """On boot, re-apply a saved manual watchlist so it survives restarts."""
    if is_active():
        set_universe(load())
