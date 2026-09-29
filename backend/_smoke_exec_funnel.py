"""Smoke test: the execution funnel (signal → fill) recorder.

The point of this recorder is that "BUY but no execution" always has an answer,
so the test checks the two ways that promise can break: a stage label that
doesn't exist, and a refusal path in state.py that reports nothing at all.
"""
from __future__ import annotations

import ast
import pathlib

from app.analysis import exec_funnel

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


def main() -> None:
    exec_funnel.reset()
    s = exec_funnel.summary()
    ok(s["reached"]["SIGNAL"] == 0, "an untouched funnel is empty, not fabricated")
    ok(s["executable_pct"] == 0.0, "no signals means no executable percentage")

    exec_funnel.reached("SIGNAL")
    exec_funnel.blocked(
        "RISK", "CONFIDENCE_FLOOR", instrument="NIFTY", option="NIFTY24500CE",
        value=71.0, threshold=80.0, where="state.py:_auto_trade", reason="below floor",
    )
    exec_funnel.reached("SIGNAL")
    exec_funnel.reached("RISK")
    exec_funnel.reached("VALIDATION")
    exec_funnel.reached("EXECUTION")
    exec_funnel.reached("BROKER")
    exec_funnel.reached("ACCEPTED")
    exec_funnel.reached("FILLED")

    s = exec_funnel.summary()
    ok(s["reached"]["SIGNAL"] == 2, "both candidates counted at the top of the funnel")
    ok(s["executable_pct"] == 50.0, "one of two BUYs became a position")
    ok(s["blocked"][0]["blocker"] == "CONFIDENCE_FLOOR", "the blocker is named")
    ev = s["recent"][0]
    ok(ev["value"] == 71.0 and ev["threshold"] == 80.0,
       "value and threshold are both recorded — a blocker without them is unactionable")
    ok(ev["where"].startswith("state.py"), "the code location is recorded")

    # --- every stage used in the live path must be a declared stage ---
    src = pathlib.Path("app/state.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    used: set[str] = set()
    calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        val = node.func.value
        if not isinstance(val, ast.Name) or val.id != "exec_funnel":
            continue
        calls += 1
        if node.args and isinstance(node.args[0], ast.Constant):
            used.add(str(node.args[0].value))
    ok(calls >= 20, f"the auto-entry path must be instrumented throughout (got {calls})")
    unknown = used - set(exec_funnel.STAGES)
    ok(not unknown, f"unknown funnel stages in state.py: {unknown}")

    # --- and no early return in the auto-entry path may be silent ---
    # A refusal that returns without recording is exactly the bug this module
    # exists to kill, so it is asserted structurally rather than by eye.
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_auto_trade"
    )
    entry_part = [n for n in fn.body if n.lineno > _entry_line(fn)]
    silent = 0
    for node in entry_part:
        for sub in ast.walk(node):
            if not isinstance(sub, ast.If):
                continue
            body = sub.body
            if not body or not isinstance(body[-1], ast.Return):
                continue
            recorded = any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id == "exec_funnel"
                for st in body
                for inner in ast.walk(st)
            )
            if not recorded:
                silent += 1
    ok(silent == 0, f"{silent} refusal path(s) in the auto-entry still return silently")

    exec_funnel.reset()
    ok(exec_funnel.summary()["reached"]["SIGNAL"] == 0, "reset clears the counters")
    print(f"OK execution funnel — {checks} checks")


def _entry_line(fn: ast.FunctionDef) -> int:
    """Line where the fresh-entry section starts (everything before it manages an
    open position and legitimately returns without a funnel record)."""
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "actionable"
        ):
            return node.lineno
    raise AssertionError("could not find the fresh-entry section of _auto_trade")


if __name__ == "__main__":
    main()
