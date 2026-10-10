"""Live-trading controls: arming authorization, kill switch, capital ceiling.

- Arming anything that can reach a broker requires ``X-QT-Live-Token`` matching
  ``QT_LIVE_ARM_TOKEN``. With no token configured, live arming is refused.
- The kill switch is persisted under ``data_dir`` so it survives restarts.
  Engaging it needs no token (anyone may stop trading); releasing it does.
  It blocks NEW entries only; exits keep working.
- ``settings.capital`` (QT_CAPITAL, default ₹5,00,000) is the single strategy
  capital source of truth. Runtime changes may lower it, never raise it above
  the value configured at startup, and broker balance never increases it.
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time

from fastapi import HTTPException

from app.config import settings

CONFIGURED_CAPITAL = float(settings.capital)
LIVE_TOKEN_HEADER = "X-QT-Live-Token"

_lock = threading.Lock()
_state: dict | None = None


def require_live_auth(token: str | None) -> None:
    expected = settings.live_arm_token or ""
    if not expected:
        raise HTTPException(403, "Live arming disabled: QT_LIVE_ARM_TOKEN is not configured.")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, f"Live arming requires a valid {LIVE_TOKEN_HEADER} header.")


def clamp_capital(value: float) -> float:
    return round(min(max(1000.0, float(value)), CONFIGURED_CAPITAL), 2)


def _path() -> str:
    return os.path.join(settings.data_dir, "kill_switch.json")


def _load() -> dict:
    global _state
    if _state is None:
        try:
            with open(_path(), encoding="utf-8") as fh:
                _state = json.load(fh)
        except FileNotFoundError:
            _state = {"engaged": False, "reason": "", "ts": None}
        except Exception:
            # Unreadable state fails closed.
            _state = {"engaged": True, "reason": "kill_switch.json unreadable", "ts": time.time()}
    return _state


def kill_switch_engaged() -> bool:
    with _lock:
        return bool(_load().get("engaged"))


def kill_switch_status() -> dict:
    with _lock:
        return dict(_load())


def set_kill_switch(engaged: bool, reason: str = "") -> dict:
    global _state
    with _lock:
        _state = {"engaged": bool(engaged), "reason": reason[:200], "ts": time.time()}
        os.makedirs(settings.data_dir, exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_state, fh)
        os.replace(tmp, _path())
        if engaged:
            settings.auto_trade_enabled = False
            settings.auto_trade_allow_live = False
            settings.auto_trade_use_live_balance = False
        return dict(_state)


def _reset_for_tests() -> None:
    global _state
    with _lock:
        _state = None
