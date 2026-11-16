"""Stops the bot re-sending entries Angel has already refused.

* A name whose auto buy Angel rejected waits ``live_reject_cooldown_sec``.
* An RMS "limit set: 0" reject blocks that exchange for the rest of the IST day.
* A buy whose premium x quantity (+ buffer) exceeds Angel's available cash is
  never sent.

In-memory only: a restart allows at most one fresh attempt per name.
"""
from __future__ import annotations

import re
import threading

from app.config import settings
from app.engine.gapup import _today

_ZERO_LIMIT = re.compile(r"limit\s*set\s*:\s*0(?![\d.])", re.I)
_FUNDS = re.compile(r"insufficient|require[sd]?\s.*funds|margin\s+shortfall", re.I)

_lock = threading.Lock()
_name_until: dict[str, float] = {}
_exch_day: dict[str, str] = {}


def block_reason(instrument: str, exchange: str, now: float) -> str | None:
    inst, exch = (instrument or "").upper(), (exchange or "").upper()
    with _lock:
        if exch and _exch_day.get(exch) == _today(now):
            return (f"Angel RMS position limit is 0 for {exch} today — "
                    f"{exch} entries not sent until tomorrow.")
        until = _name_until.get(inst, 0.0)
    if now < until:
        return (f"Angel rejected the last {inst} buy — not re-sending for "
                f"{int((until - now) // 60) + 1} min.")
    return None


def record_reject(instrument: str, exchange: str, message: str, now: float) -> None:
    inst, exch = (instrument or "").upper(), (exchange or "").upper()
    with _lock:
        _name_until[inst] = float(now) + float(settings.live_reject_cooldown_sec)
        if exch and _ZERO_LIMIT.search(message or ""):
            _exch_day[exch] = _today(now)


def funds_shortfall(premium: float | None, quantity: int,
                    available: float | None) -> str | None:
    """None when affordable or when the balance/price is unknown (Angel decides)."""
    if not premium or premium <= 0 or quantity <= 0 or not available or available <= 0:
        return None
    need = premium * quantity * (1.0 + settings.live_funds_buffer_pct / 100.0) \
        + settings.live_funds_buffer_inr
    if need > available:
        return (f"Insufficient Angel funds: need ≈₹{need:,.0f}, "
                f"available ₹{available:,.0f} — order not sent.")
    return None


def is_funds_reject(message: str) -> bool:
    return bool(_FUNDS.search(message or ""))


def reset() -> None:
    with _lock:
        _name_until.clear()
        _exch_day.clear()
