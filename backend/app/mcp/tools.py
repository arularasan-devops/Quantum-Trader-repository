"""MCP tool registry — the complete surface an AI client may reach.

The security model is an allowlist, not a filter. A tool is callable only if it
appears in :data:`TOOLS`; anything else is rejected by name before any code runs.
There is no tool that places, modifies or cancels a real broker order, and none
can be added by configuration — ``real_buy``, ``real_sell`` and ``real_modify``
do not exist in this process's tool namespace at all. Broker access is limited to
reading funds and positions.

Every tool argument is validated here (type, range, allowlisted instrument name)
before it reaches application code, and errors are returned as short messages
rather than tracebacks so internal paths never leave the process.
"""
from __future__ import annotations

import time
from typing import Callable, TypedDict

from app.ai import compare, journal as aij, orchestrator, paper, probability, safety
from app.ai import service as ai_service
from app.analysis import scan_service
from app.config import settings
from app.market.tick_quality import feed_quality

# Tool names that must never exist. Asserted by the smoke test so a future edit
# that adds one fails the build rather than shipping.
FORBIDDEN = ("real_buy", "real_sell", "real_modify", "place_order", "buy", "sell",
             "modify_order", "cancel_order", "square_off")

_MAX_LIMIT = 500


class ToolError(Exception):
    """A validation or availability error safe to return to the client."""


def _instrument(args: dict, key: str = "instrument", required: bool = True) -> str:
    from app.market import instruments as inst_mod

    raw = args.get(key)
    if raw is None or raw == "":
        if required:
            raise ToolError(f"'{key}' is required")
        return inst_mod.DEFAULT_INSTRUMENT
    if not isinstance(raw, str) or len(raw) > 40:
        raise ToolError(f"'{key}' must be a short instrument name")
    name = raw.strip().upper()
    if name not in inst_mod.REGISTRY:
        raise ToolError(f"unknown instrument '{name}'")
    return name


def _limit(args: dict, default: int = 50) -> int:
    raw = args.get("limit", default)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ToolError("'limit' must be an integer") from None
    return max(1, min(_MAX_LIMIT, n))


def _side(args: dict) -> str:
    raw = str(args.get("side") or "").upper()
    if raw not in ("CE", "PE"):
        raise ToolError("'side' must be CE or PE")
    return raw


def _trade_id(args: dict) -> str:
    raw = args.get("trade_id")
    if not isinstance(raw, str) or not raw or len(raw) > 64:
        raise ToolError("'trade_id' must be a paper-trade id")
    return raw


# ------------------------------------------------------------------ market
def get_live_market(args: dict) -> dict:
    rows = scan_service.sweep()
    return {"as_of": int(time.time()), "instruments": len(rows),
            "rows": [r.as_dict() for r in rows[:_limit(args, 25)]]}


def get_ltp(args: dict) -> dict:
    inst = _instrument(args)
    st = ai_service.resolve_state(inst)
    try:
        ltp = st.provider.futures_price()
    except Exception:
        ltp = None
    fq = get_feed_quality({"instrument": inst})
    return {"instrument": inst, "ltp": ltp, "as_of": int(time.time()),
            "feed": {k: fq.get(k) for k in ("state", "last_tick_age_ms")}}


def get_market_snapshot(args: dict) -> dict:
    inst = _instrument(args)
    from app.main import hub

    snap = hub.latest.get(inst)
    if snap is None:
        return {"instrument": inst, "available": False,
                "reason": "instrument not warm yet"}
    d = snap.model_dump(mode="json")
    return {"instrument": inst, "available": True, "snapshot": d}


def get_top_movers(args: dict) -> dict:
    rows = scan_service.sweep()
    ranked = [r.as_dict() for r in rows if r.opportunity_score is not None]
    return {"as_of": int(time.time()), "rows": ranked[:_limit(args, 10)]}


def get_market_opportunities(args: dict) -> dict:
    rows = scan_service.sweep()
    return {
        "as_of": int(time.time()),
        "note": ("Attention allocation only. The Phase 4 causal backtest measured "
                 "OPPORTUNITY bars resolving WORSE than NO_OPPORTUNITY on both "
                 "instruments, so this ranking must not be used as a trade signal."),
        "rows": [r.as_dict() for r in rows[:_limit(args, 10)]],
    }


def get_feed_quality(args: dict) -> dict:
    inst = _instrument(args, required=False)
    from app.market.instruments import get_spec

    try:
        root = get_spec(inst).symbol
    except Exception:
        root = inst
    return {"instrument": inst, **feed_quality.snapshot(root)}


def get_option_chain(args: dict) -> dict:
    inst = _instrument(args)
    st = ai_service.resolve_state(inst)
    try:
        chain = st.provider.option_chain()
        spot = st.provider.futures_price()
    except Exception as exc:
        raise ToolError(f"chain unavailable ({type(exc).__name__})") from None
    real = settings.data_provider.lower() not in ("simulated", "sim", "demo")
    return {
        "instrument": inst, "spot": spot,
        "provenance": "REAL_BROKER" if real else "SIMULATOR",
        "warning": None if real else ("Simulated chain: IV, OI, volume and spread "
                                      "are model output, not market data."),
        "rows": [q.model_dump(mode="json") for q in chain[:_limit(args, 40)]],
    }


# ---------------------------------------------------------------------- AI
def _decision(inst: str):
    st, candles, feed_state, age = ai_service.cached_inputs(inst)
    scan = None
    try:
        res = scan_service.scan_instrument(st)
        scan = {"state": res.classification, "verdict": res.verdict,
                "score": res.opportunity_score}
    except Exception:
        scan = None
    dec = orchestrator.evaluate(inst, candles, feed_state, age,
                                (scan or {}).get("state"), (scan or {}).get("verdict"))
    return dec, scan


def get_regime(args: dict) -> dict:
    inst = _instrument(args)
    dec, scan = _decision(inst)
    return {"instrument": inst, "regime": dec.regime, "scan": scan}


def get_direction_probability(args: dict) -> dict:
    inst = _instrument(args)
    dec, _ = _decision(inst)
    return {"instrument": inst, "direction": dec.direction}


def get_entry_quality(args: dict) -> dict:
    inst = _instrument(args)
    dec, _ = _decision(inst)
    return {"instrument": inst, "entry_quality": dec.entry, "side": dec.side}


def get_trade_probability(args: dict) -> dict:
    inst = _instrument(args)
    dec, _ = _decision(inst)
    return {"instrument": inst, "side": dec.side,
            "probability": dec.prob,
            "basis": "UNDERLYING move, before option costs"}


def get_ai_decision(args: dict) -> dict:
    inst = _instrument(args)
    dec, scan = _decision(inst)
    out = dec.as_dict()
    out["scan"] = scan
    out["baseline"] = ai_service.baseline_verdict(inst)
    return out


def get_position_analysis(args: dict) -> dict:
    from app.ai import exit_ai
    from app.ai import features as F
    from app.ai import regime as reg_engine

    j = aij.journal()
    out = []
    for t in j.open_trades():
        inst = str(t["instrument"])
        _st, candles, feed_state, _age = ai_service.cached_inputs(inst)
        feats = F.compute(list(candles)[-F.WINDOW:]) if candles else None
        advice = None
        if feats is not None:
            reg = reg_engine.classify(feats, None)
            advice = exit_ai.advise(
                dict(t), feats, reg.state, reg.direction_bias,
                float(t.get("current_premium") or t.get("entry_premium") or 0.0)
            ).as_dict()
        out.append({"trade": dict(t), "feed_state": feed_state, "advice": advice})
    return {"positions": out, "note": "Paper positions only."}


# ---------------------------------------------------------------- research
def get_gate_trace(args: dict) -> dict:
    inst = _instrument(args)
    from app.main import hub

    snap = hub.latest.get(inst)
    gates = snap.decision.gates if snap is not None else None
    return {
        "instrument": inst,
        "baseline_gates": gates.model_dump(mode="json") if gates is not None else None,
        "note": "Production engine gate trace, read from cache. Read-only.",
    }


def get_missed_opportunities(args: dict) -> dict:
    """Decisions the AI declined where the market subsequently hit target."""
    rows = aij.journal().dataset(10000)
    missed = []
    for r in rows:
        if (r.get("ai_decision") or "") == "BUY_NOW":
            continue
        for side in ("ce", "pe"):
            if r.get(f"{side}_result") == "TARGET":
                missed.append({"ts": r["ts"], "instrument": r["instrument"],
                               "side": side.upper(), "blocked_by": r.get("blocked_by"),
                               "ai_decision": r.get("ai_decision"),
                               "r": r.get(f"{side}_r")})
                break
    return {"resolved_decisions": len(rows), "missed": missed[-_limit(args, 50):],
            "note": ("A declined bar where one side hit target is not necessarily "
                     "an error: the other side often stopped out on the same bar.")}


def run_backtest(args: dict) -> dict:
    raise ToolError(
        "Backtests are offline scripts, deliberately not exposed as a live tool: "
        "they take minutes to hours and write research artefacts. Run "
        "phase6_dataset.py / phase6_train.py / phase6_pullback.py on the host.")


def run_walk_forward(args: dict) -> dict:
    art = probability.status()
    if not art.get("available"):
        raise ToolError(art.get("reason") or "no model artefact")
    return {"walk_forward": (art.get("metrics") or {}),
            "note": "Reported from the loaded artefact; retraining is offline and manual."}


def compare_baseline_vs_ai(args: dict) -> dict:
    return compare.compare()


def get_model_metrics(args: dict) -> dict:
    return probability.status()


def get_calibration(args: dict) -> dict:
    st = probability.status()
    if not st.get("available"):
        raise ToolError(st.get("reason") or "no model artefact")
    return {"calibration": st.get("calibration"),
            "oos": (st.get("metrics") or {}).get("oos")}


# -------------------------------------------------------------------- paper
def get_paper_positions(args: dict) -> dict:
    j = aij.journal()
    return {"open": j.open_trades(), "recent": j.trades(_limit(args, 50))}


def get_paper_orders(args: dict) -> dict:
    """Paper entries and exits. There is no real order book here by design."""
    j = aij.journal()
    rows = j.trades(_limit(args, 100))
    orders = []
    for t in rows:
        orders.append({"trade_id": t["id"], "ts": t["entry_ts"], "type": "PAPER_ENTRY",
                       "symbol": t["symbol"], "side": t["side"],
                       "price": t["entry_premium"], "lots": t["lots"]})
        if t.get("exit_ts"):
            orders.append({"trade_id": t["id"], "ts": t["exit_ts"], "type": "PAPER_EXIT",
                           "symbol": t["symbol"], "side": t["side"],
                           "price": t["exit_premium"], "lots": t["lots"],
                           "reason": t.get("exit_reason")})
    orders.sort(key=lambda o: o["ts"], reverse=True)
    return {"orders": orders[:_limit(args, 100)], "mode": "PAPER"}


def get_paper_pnl(args: dict) -> dict:
    return paper.pnl_summary()


def paper_buy(args: dict) -> dict:
    """Open a PAPER position, subject to the same checks the AI engine passes.

    The checks are not relaxed for a manual call: a stale feed, a closed market, a
    duplicate position or a failed risk limit refuses here exactly as it does in
    the automated cycle.
    """
    inst = _instrument(args)
    side = _side(args)
    from app.ai import features as F

    st, candles, feed_state, age = ai_service.cached_inputs(inst)
    feats = F.compute(list(candles)[-F.WINDOW:]) if candles else None
    if feats is None:
        raise ToolError("not enough cached candles to size a paper trade")
    try:
        chain = st.provider.option_chain()
        spot = st.provider.futures_price()
    except Exception:
        raise ToolError("option chain unavailable") from None
    quote = paper.select_contract(chain, spot or 0.0, side)
    prob = probability.score(feats, side)
    return paper.open_paper(inst, side, quote, feats, feed_state, age,
                            prob.get("p_target_before_stop"), "MANUAL_MCP",
                            side, "MANUAL_MCP")


def paper_sell(args: dict) -> dict:
    """Close a PAPER position (alias of paper_close; long-only paper book)."""
    return paper_close(args)


def paper_close(args: dict) -> dict:
    return paper.close_paper(_trade_id(args), "AI_EXIT", ai_service.resolve_state)


def _modify(args: dict, key: str) -> dict:
    tid = _trade_id(args)
    try:
        value = float(args.get(key))
    except (TypeError, ValueError):
        raise ToolError(f"'{key}' must be a number") from None
    if value <= 0:
        raise ToolError(f"'{key}' must be positive")
    j = aij.journal()
    t = j.get_trade(tid)
    if not t or t.get("status") != "OPEN":
        raise ToolError("no such open paper trade")
    j.set_levels(tid, stop=value if key == "stop" else None,
                 target1=value if key == "target1" else None)
    return {"ok": True, "trade": j.get_trade(tid)}


def paper_modify_stop(args: dict) -> dict:
    return _modify(args, "stop")


def paper_modify_target(args: dict) -> dict:
    return _modify(args, "target1")


# --------------------------------------------------------- broker (READ ONLY)
def _angel_account() -> dict:
    if settings.data_provider.lower() != "angelone":
        raise ToolError("no Angel session (QT_DATA_PROVIDER is not 'angelone')")
    from app.market import angelone

    try:
        return angelone.account_info()
    except Exception as exc:
        raise ToolError(f"broker read failed ({type(exc).__name__})") from None


def get_account(args: dict) -> dict:
    info = _angel_account()
    return {"totals": info.get("totals"), "accounts": info.get("accounts"),
            "access": "READ_ONLY"}


def get_real_positions(args: dict) -> dict:
    info = _angel_account()
    return {"positions": info.get("positions"), "access": "READ_ONLY",
            "note": "Real positions are read-only; they cannot be modified from here."}


def get_real_orders(args: dict) -> dict:
    raise ToolError(
        "Real order book is not exposed. This build has real-money execution "
        "disabled, so there is no order flow to report and no tool that could "
        "create any.")


def get_real_margin(args: dict) -> dict:
    info = _angel_account()
    totals = info.get("totals") or {}
    return {"available_cash": totals.get("available_cash"),
            "used_margin": totals.get("used_margin"),
            "net_balance": totals.get("net_balance"), "access": "READ_ONLY"}


# ------------------------------------------------------------------ safety
def get_safety_status(args: dict) -> dict:
    return {**safety.status(), "refusals": safety.order_refusals(20)}


class ToolSpec(TypedDict):
    fn: Callable[[dict], dict]
    desc: str
    args: dict[str, str]


TOOLS: dict[str, ToolSpec] = {
    # market
    "get_live_market": {"fn": get_live_market, "desc": "Ranked live scan of every warm instrument.", "args": {"limit": "int"}},
    "get_ltp": {"fn": get_ltp, "desc": "Last traded price plus feed freshness.", "args": {"instrument": "str"}},
    "get_market_snapshot": {"fn": get_market_snapshot, "desc": "Full cached snapshot for one instrument.", "args": {"instrument": "str"}},
    "get_top_movers": {"fn": get_top_movers, "desc": "Instruments ranked by current movement.", "args": {"limit": "int"}},
    "get_market_opportunities": {"fn": get_market_opportunities, "desc": "Scanner opportunities (attention only, not a signal).", "args": {"limit": "int"}},
    "get_feed_quality": {"fn": get_feed_quality, "desc": "Tick freshness/quality for one instrument.", "args": {"instrument": "str"}},
    "get_option_chain": {"fn": get_option_chain, "desc": "Option chain with provenance (REAL_BROKER vs SIMULATOR).", "args": {"instrument": "str", "limit": "int"}},
    # AI
    "get_regime": {"fn": get_regime, "desc": "Market regime and confidence.", "args": {"instrument": "str"}},
    "get_direction_probability": {"fn": get_direction_probability, "desc": "P(CE)/P(PE) for the current bar.", "args": {"instrument": "str"}},
    "get_entry_quality": {"fn": get_entry_quality, "desc": "ENTRY_NOW / WAIT_PULLBACK / TOO_EXTENDED / NO_ENTRY.", "args": {"instrument": "str"}},
    "get_trade_probability": {"fn": get_trade_probability, "desc": "Calibrated P(target before stop) and expected R.", "args": {"instrument": "str"}},
    "get_ai_decision": {"fn": get_ai_decision, "desc": "Full AI decision: BUY_NOW / WAIT_PULLBACK / WATCH / NO_TRADE (paper only).", "args": {"instrument": "str"}},
    "get_position_analysis": {"fn": get_position_analysis, "desc": "Exit advice for open PAPER positions.", "args": {}},
    # research
    "get_gate_trace": {"fn": get_gate_trace, "desc": "Production engine gate trace (read-only).", "args": {"instrument": "str"}},
    "get_missed_opportunities": {"fn": get_missed_opportunities, "desc": "Declined bars where a side later hit target.", "args": {"limit": "int"}},
    "run_backtest": {"fn": run_backtest, "desc": "Not exposed live — offline script (explains how to run it).", "args": {}},
    "run_walk_forward": {"fn": run_walk_forward, "desc": "Walk-forward metrics from the loaded artefact.", "args": {}},
    "compare_baseline_vs_ai": {"fn": compare_baseline_vs_ai, "desc": "Baseline vs AI on identical bars + paper book.", "args": {}},
    "get_model_metrics": {"fn": get_model_metrics, "desc": "Loaded model, OOS metrics and limitations.", "args": {}},
    "get_calibration": {"fn": get_calibration, "desc": "OOS calibration table.", "args": {}},
    # paper
    "get_paper_positions": {"fn": get_paper_positions, "desc": "Open and recent PAPER positions.", "args": {"limit": "int"}},
    "get_paper_orders": {"fn": get_paper_orders, "desc": "PAPER entry/exit log.", "args": {"limit": "int"}},
    "get_paper_pnl": {"fn": get_paper_pnl, "desc": "PAPER book P&L including costs.", "args": {}},
    "paper_buy": {"fn": paper_buy, "desc": "Open a PAPER position (all pre-trade checks apply).", "args": {"instrument": "str", "side": "CE|PE"}},
    "paper_sell": {"fn": paper_sell, "desc": "Close a PAPER position.", "args": {"trade_id": "str"}},
    "paper_close": {"fn": paper_close, "desc": "Close a PAPER position.", "args": {"trade_id": "str"}},
    "paper_modify_stop": {"fn": paper_modify_stop, "desc": "Change a PAPER position's stop.", "args": {"trade_id": "str", "stop": "float"}},
    "paper_modify_target": {"fn": paper_modify_target, "desc": "Change a PAPER position's target.", "args": {"trade_id": "str", "target1": "float"}},
    # broker — READ ONLY
    "get_account": {"fn": get_account, "desc": "READ-ONLY broker account totals.", "args": {}},
    "get_real_positions": {"fn": get_real_positions, "desc": "READ-ONLY broker positions.", "args": {}},
    "get_real_orders": {"fn": get_real_orders, "desc": "Not exposed (no real order flow exists in this build).", "args": {}},
    "get_real_margin": {"fn": get_real_margin, "desc": "READ-ONLY broker margin/cash.", "args": {}},
    # safety
    "get_safety_status": {"fn": get_safety_status, "desc": "Real-money kill-switch state and refused attempts.", "args": {}},
}


def call(name: str, args: dict | None) -> dict:
    """Dispatch an allowlisted tool. Unknown names never reach application code."""
    entry = TOOLS.get(name)
    if entry is None:
        raise ToolError(f"unknown tool '{name}'")
    return entry["fn"](args or {})


def descriptors() -> list[dict]:
    """MCP ``tools/list`` payload."""
    out = []
    for name, meta in TOOLS.items():
        props: dict[str, dict[str, str]] = {}
        for arg, kind in meta["args"].items():
            props[arg] = {
                "type": "integer" if kind == "int" else
                        "number" if kind == "float" else "string",
                "description": f"{arg} ({kind})",
            }
        out.append({
            "name": name,
            "description": meta["desc"],
            "inputSchema": {"type": "object", "properties": props,
                            "additionalProperties": False},
        })
    return out
