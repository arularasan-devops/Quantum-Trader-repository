"""Smoke test: real-money execution is unreachable from the Phase 6 AI stack.

This is the test that matters most in Phase 6. It asserts four separate things,
because a single check on a config flag would be worth very little:

1. The guard refuses at the concrete Angel adapter BEFORE the SDK is touched — the
   SDK is replaced with a mock that fails the test if it is called at all.
2. The guard fails CLOSED: if settings cannot be read, it refuses rather than
   assumes it is allowed to trade.
3. No AI, paper or MCP module has an import path to a broker order API, checked by
   parsing the source rather than by trusting a docstring.
4. The MCP tool namespace contains no real-order tool, and unknown names are
   rejected before any code runs.
"""
from __future__ import annotations

import ast
import pathlib

from app.ai import paper, safety
from app.mcp import tools

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


class _ExplodingSDK:
    """Any call is a test failure: the guard must refuse before this is reached."""

    def placeOrder(self, params: dict) -> str:  # noqa: N802 - SDK name
        raise AssertionError("SDK placeOrder() was reached — the guard did not hold")

    def searchScrip(self, exchange: str, symbol: str) -> dict:  # noqa: N802
        raise AssertionError("SDK searchScrip() was reached — the guard did not hold")


def test_guard_blocks_before_sdk() -> None:
    from app.market.angelone import AngelOneProvider

    # Bypass __init__ (it would log in) — we are testing the method, not the ctor.
    prov = AngelOneProvider.__new__(AngelOneProvider)
    prov._options = []  # type: ignore[attr-defined]
    prov._smart = _ExplodingSDK()  # type: ignore[attr-defined]
    try:
        prov.place_order("NIFTY25000CE", "BUY", 1)
    except safety.RealMoneyBlocked as exc:
        ok("paper" in str(exc).lower() or "disabled" in str(exc).lower(),
           "refusal must say why")
    else:
        raise AssertionError("angelone.place_order did not refuse in paper mode")


def test_base_provider_blocks() -> None:
    from app.market.provider import MarketDataProvider

    # Called unbound: MarketDataProvider is abstract, and the guard must not
    # depend on having a live provider instance.
    try:
        MarketDataProvider.place_order(None, "X", "BUY", 1)  # type: ignore[arg-type]
    except safety.RealMoneyBlocked:
        ok(True, "base provider refuses")
    except NotImplementedError:
        raise AssertionError(
            "base provider raised NotImplementedError — the guard must run first"
        ) from None
    else:
        raise AssertionError("base provider did not refuse")


def test_fails_closed() -> None:
    """An unreadable/absent config must read as paper mode, never as live."""
    ok(safety.paper_mode() is True, "paper mode must be on in this build")
    original = safety._read_paper_mode
    try:
        def _boom() -> bool:
            raise RuntimeError("config unavailable")

        safety._read_paper_mode = _boom  # type: ignore[assignment]
        ok(safety.paper_mode() is True, "unreadable config must fail CLOSED")
    finally:
        safety._read_paper_mode = original  # type: ignore[assignment]


def test_refusals_recorded() -> None:
    before = len(safety.order_refusals(500))
    try:
        safety.assert_order_allowed("_smoke", "TESTSYMBOL", "BUY", 1)
    except safety.RealMoneyBlocked:
        pass
    after = safety.order_refusals(500)
    ok(len(after) > before, "refusals must be recorded for audit")
    ok(all("credential" not in str(r).lower() for r in after),
       "refusal records must not carry credentials")


_BROKER_ORDER_NAMES = ("place_order", "placeOrder", "modifyOrder", "cancelOrder")


def test_no_order_path_in_ai_sources() -> None:
    """Parse every AI/paper/MCP module: none may reference a broker order call."""
    root = pathlib.Path(__file__).resolve().parent / "app"
    files = sorted((root / "ai").glob("*.py")) + sorted((root / "mcp").glob("*.py"))
    ok(len(files) >= 10, f"expected the AI+MCP package, found {len(files)} files")
    for path in files:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _BROKER_ORDER_NAMES:
                # app/ai/safety.py names the guarded method in its own docs/status
                # payload; a *call* to it is what must not exist.
                raise AssertionError(
                    f"{path.name} references {node.attr} — AI code must have no "
                    "broker order path")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                ok(node.func.id not in _BROKER_ORDER_NAMES,
                   f"{path.name} calls {node.func.id}")
    ok(True, "no AI/MCP module has a broker order path")


def test_paper_module_cannot_reach_provider_orders() -> None:
    src = (pathlib.Path(__file__).resolve().parent / "app/ai/paper.py").read_text()
    for name in _BROKER_ORDER_NAMES:
        ok(f".{name}(" not in src, f"paper.py must not call {name}")
    ok(paper.NO_PAPER_TRADE == "NO_PAPER_TRADE", "paper refusal vocabulary intact")


def test_mcp_namespace_has_no_real_order_tool() -> None:
    for name in tools.FORBIDDEN:
        ok(name not in tools.TOOLS, f"MCP must not expose '{name}'")
    for name in tools.TOOLS:
        low = name.lower()
        ok(not (low.startswith("real_") and ("buy" in low or "sell" in low
                                             or "modify" in low)),
           f"suspicious tool name '{name}'")
    try:
        tools.call("real_buy", {})
    except tools.ToolError:
        ok(True, "unknown tool rejected by name")
    else:
        raise AssertionError("MCP dispatched an unknown tool")
    # Paper tools must be the only execution surface.
    exec_tools = [n for n in tools.TOOLS if "buy" in n or "sell" in n
                  or "close" in n or "modify" in n]
    ok(exec_tools and all(n.startswith("paper_") for n in exec_tools),
       f"non-paper execution tool exposed: {exec_tools}")


def test_ai_status_reports_disabled() -> None:
    st = safety.status()
    ok(st["real_money_execution"] == "DISABLED", "status must report DISABLED")
    ok(st["paper_mode"] is True, "status must report paper mode on")
    blob = str(st).lower()
    for leak in ("pin", "totp", "secret", "password", "/home/"):
        ok(leak not in blob, f"safety status leaks '{leak}'")


def main() -> None:
    test_guard_blocks_before_sdk()
    test_base_provider_blocks()
    test_fails_closed()
    test_refusals_recorded()
    test_no_order_path_in_ai_sources()
    test_paper_module_cannot_reach_provider_orders()
    test_mcp_namespace_has_no_real_order_tool()
    test_ai_status_reports_disabled()
    print(f"AI SAFETY SMOKE PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
