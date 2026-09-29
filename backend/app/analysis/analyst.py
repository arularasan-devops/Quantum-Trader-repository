"""Local "AI Analyst" — a deterministic, plain-English commentary layer.

It turns the numbers the engines ALREADY compute (regime, bias, confidence,
exit reason, P&L) into a short human-readable note for each trade/decision, so
the journal records *why* — not just the numbers.

IMPORTANT: this is fully local and rule-based. It makes NO external API calls
and sends NO data off the machine (compliant with the data-protection policy),
and it NEVER places or sizes an order — it only describes. It is intentionally
NOT a black-box "genius": every sentence is derived directly from a real field.
"""
from __future__ import annotations


def _pct(x: object) -> str:
    try:
        return f"{float(x):.0f}%"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"


def note_for_trade(trade: dict) -> str:
    """One-line plain-English summary of a closed trade, from its own fields."""
    parts: list[str] = []
    opt = trade.get("option") or "position"
    otype = trade.get("option_type") or ""
    lots = trade.get("lots") or 0
    auto = bool(trade.get("auto"))
    mode = (trade.get("mode") or "paper").upper()
    if (trade.get("engine") or "").lower() == "flow":
        opener = "Candle-Flow"
    else:
        opener = "Auto-Buy" if auto else "Manual"
    parts.append(f"{opener} {mode} · {opt} {otype} x{lots}")

    ctx = trade.get("context") if isinstance(trade.get("context"), dict) else {}
    conf = trade.get("confidence")
    if conf is None and isinstance(ctx, dict):
        conf = ctx.get("confidence")
    if conf is not None:
        parts.append(f"entered at {_pct(conf)} confidence")
    if isinstance(ctx, dict):
        regime = ctx.get("regime") or ctx.get("market_regime")
        bias = ctx.get("bias") or ctx.get("bias_15m")
        if regime:
            parts.append(f"regime {regime}")
        if bias:
            parts.append(f"15m bias {bias}")

    entry = trade.get("entry")
    exit_ = trade.get("exit")
    pnl = trade.get("net_pnl")
    reason = trade.get("exit_reason") or "closed"
    hold = trade.get("holding_minutes")
    move = ""
    try:
        if entry is not None and exit_ is not None:
            move = f" {float(entry):.1f}→{float(exit_):.1f}"
    except (TypeError, ValueError):
        move = ""
    outcome = "profit" if (isinstance(pnl, (int, float)) and pnl >= 0) else "loss"
    tail = f"exited on {reason}{move} → {outcome}"
    if isinstance(pnl, (int, float)):
        tail += f" ₹{pnl:,.0f}"
    if hold is not None:
        tail += f" ({hold}m hold)"
    parts.append(tail)
    return " · ".join(parts)
