"""Real-money kill switch. One choke point, no bypass.

Every real broker order in this application passes through
:func:`assert_order_allowed` before the broker SDK is touched, and in this build
that function ALWAYS raises. Real-money execution is disabled, and it is disabled
by a source-level constant — not by a setting, an environment variable, a request
parameter or a runtime toggle:

    REAL_MONEY_EXECUTION_ENABLED = False

Re-enabling it therefore requires editing this file, which means a diff, a review
and a deliberate human decision. There is deliberately no ``QT_PAPER_MODE=false``
style escape hatch: a flag that can be flipped by a typo in an environment file,
by a stale shell export or by a copy-pasted deploy command is not a safety
control. ``settings.paper_mode`` exists for reporting only and cannot weaken this
function — if it were somehow set to false, orders are still refused.

Design notes, because the shape matters more than the code:

* It fails **closed**. A missing or unreadable setting refuses, and an unexpected
  exception anywhere in the check refuses.
* It is enforced at the **broker adapter**, below every caller. A new engine, a
  new endpoint or a new MCP tool cannot bypass it by forgetting to ask.
* It only ever **removes** capability. The pre-existing ``trade_mode`` /
  ``auto_trade_allow_live`` / explicit-confirm requirements are untouched and
  still apply above this; no decision, gate, stop, target or sizing rule is
  affected by anything in this module.
* Refusals are recorded here (in memory, bounded, no credentials) so an attempted
  real order is visible on the dashboard instead of vanishing into a log file.

The Phase 6 AI engine, paper bot and MCP server additionally have no import path
to a broker order API at all, which ``_smoke_ai_safety.py`` verifies by parsing
their source. This guard is the second line, not the only one.
"""
from __future__ import annotations

import threading
import time

# The single switch. Not configurable at runtime, on purpose. Changing it is a
# code change that has to be reviewed and merged.
REAL_MONEY_EXECUTION_ENABLED = False

_LOG_MAX = 200
_lock = threading.Lock()
_refusals: list[dict] = []


class RealMoneyBlocked(RuntimeError):
    """Raised instead of placing a real order. Real execution is disabled."""


def _read_paper_mode() -> bool:
    """Report ``settings.paper_mode``. Reporting only — never a permission."""
    from app.config import settings

    return bool(settings.paper_mode)


def paper_mode() -> bool:
    """True when real-money execution is disabled. Unreadable config reads True."""
    if not REAL_MONEY_EXECUTION_ENABLED:
        return True
    try:
        return _read_paper_mode()
    except Exception:
        return True


def _record(caller: str, symbol: str, side: str, lots: int, reason: str) -> None:
    with _lock:
        _refusals.append({
            "ts": int(time.time()),
            "caller": caller,
            "option_symbol": symbol,
            "side": side,
            "lots": lots,
            "reason": reason,
        })
        del _refusals[:-_LOG_MAX]


def assert_order_allowed(caller: str, symbol: str = "", side: str = "",
                         lots: int = 0) -> None:
    """Gate a real broker order. Always raises :class:`RealMoneyBlocked` here.

    ``caller`` identifies the code path for the audit trail (e.g.
    ``"angelone.place_order"``), so a refusal names what tried to trade.
    """
    reason = (
        "Real-money order execution is disabled in this build "
        "(app.ai.safety.REAL_MONEY_EXECUTION_ENABLED is False). Paper trading is "
        "unaffected. Enabling real execution requires a reviewed code change, not "
        "a setting."
    )
    if not REAL_MONEY_EXECUTION_ENABLED:
        _record(caller, symbol, side, lots, reason)
        raise RealMoneyBlocked(reason)
    # Unreachable in this build; kept so that if the constant is ever changed the
    # reporting flag still refuses by default rather than silently permitting.
    try:
        blocked = _read_paper_mode()
    except Exception:
        blocked = True
    if blocked:
        _record(caller, symbol, side, lots, reason)
        raise RealMoneyBlocked(reason)


def order_refusals(limit: int = 50) -> list[dict]:
    """Most recent blocked real-order attempts, newest last."""
    with _lock:
        return list(_refusals[-max(1, limit):])


def status() -> dict:
    """Kill-switch state, safe to expose on the dashboard (no credentials)."""
    try:
        from app.config import settings

        trade_mode = str(settings.trade_mode)
        ai_paper = bool(settings.ai_paper_enabled)
    except Exception:
        trade_mode = "unknown"
        ai_paper = False
    with _lock:
        blocked = len(_refusals)
    return {
        "paper_mode": paper_mode(),
        "real_money_execution": (
            "ENABLED" if REAL_MONEY_EXECUTION_ENABLED else "DISABLED"),
        "trade_mode": trade_mode,
        "ai_paper_enabled": ai_paper,
        "guard": "app.ai.safety.assert_order_allowed",
        "enforced_at": [
            "app.market.angelone.AngelOneProvider.place_order",
            "app.market.provider.MarketDataProvider.place_order",
        ],
        "blocked_attempts": blocked,
        "bypass": "none — controlled by a source constant, not by configuration",
        "note": (
            "Real-money execution is off by construction: no setting, environment "
            "variable, request or tool can turn it on. The AI, paper bot and MCP "
            "server additionally have no broker order path at all."
        ),
    }
