"""§16 — the static half of the look-ahead guard.

:mod:`app.research.opportunity.mechanisms` makes look-ahead structurally
impossible by handing an admission function a window that ends at the decision
bar. This module is the second lock: it parses the admission functions and
refuses names that can only refer to something not yet known at that instant —
an excursion, a giveback, an outcome, an exit price, a future session.

Two locks rather than one because they fail differently. The structural one is
airtight for the mechanisms as written but says nothing about a future mechanism
that takes an extra argument; the static one catches the careless name in that
new argument but cannot see through an alias. Together they cover the way this
error actually arrives, which is a plausible parameter added months later by
someone who was not thinking about it at all.

It parses rather than greps: a banned word inside a docstring or a comment is
prose about look-ahead, not look-ahead, and a checker that cries wolf over its
own documentation is a checker people switch off.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Callable

# Names that cannot be known at the decision instant. Each is a quantity that
# only exists once the trade is over.
FORBIDDEN_NAMES: frozenset[str] = frozenset({
    "mfe", "mae", "max_favourable", "max_adverse", "giveback", "outcome",
    "exit_price", "exit_ts", "result", "pnl", "net_pnl", "realised",
    "realized", "future", "future_high", "future_low", "future_close",
    "next_bar", "next_session", "tomorrow", "final", "peak", "trough",
    "resolved", "won", "lost", "hit_t1", "hit_sl",
})

# Attribute or subscript access that reaches past the decision bar. A window
# ends at that bar, so a positive forward index is the only way to ask for a
# later one, and these are the shapes it takes.
FORBIDDEN_ATTRS: frozenset[str] = frozenset({
    "forward", "lookahead", "look_ahead", "ahead", "after",
})

CLEAN = "NO_LOOK_AHEAD_FOUND"
FOUND = "LOOK_AHEAD_SUSPECTED"


def _normalise(name: str) -> str:
    return name.strip().lower().lstrip("_")


def _tree(fn: Callable) -> ast.AST:
    # dedent rather than cleandoc: a nested or indented definition must still
    # parse, and cleandoc reflows the body's first line into the signature.
    return ast.parse(textwrap.dedent(inspect.getsource(fn)))


def inspect_function(fn: Callable) -> dict:
    """Findings for one admission function. Empty findings is a pass."""
    findings: list[dict] = []
    try:
        tree = _tree(fn)
    except (OSError, SyntaxError) as exc:
        # Cannot read the source: report it rather than passing silently. A
        # guard that returns "clean" when it could not look is worse than none.
        return {"function": getattr(fn, "__name__", "?"),
                "status": FOUND,
                "findings": [{"kind": "UNREADABLE_SOURCE", "detail": str(exc)}]}

    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and _normalise(node.arg) in FORBIDDEN_NAMES:
            findings.append({"kind": "FORBIDDEN_PARAMETER", "name": node.arg})
        elif isinstance(node, ast.Name) and _normalise(node.id) in FORBIDDEN_NAMES:
            findings.append({"kind": "FORBIDDEN_NAME", "name": node.id})
        elif isinstance(node, ast.Attribute):
            attr = _normalise(node.attr)
            if attr in FORBIDDEN_NAMES or attr in FORBIDDEN_ATTRS:
                findings.append({"kind": "FORBIDDEN_ATTRIBUTE", "name": node.attr})

    return {
        "function": getattr(fn, "__name__", "?"),
        "status": FOUND if findings else CLEAN,
        "findings": findings,
    }


def audit(functions) -> dict:
    """Audit every admission function. Used by the screen and by the smoke."""
    rows = [inspect_function(fn) for fn in functions]
    bad = [r for r in rows if r["status"] != CLEAN]
    return {
        "checked": len(rows),
        "clean": len(rows) - len(bad),
        "suspected": len(bad),
        "status": FOUND if bad else CLEAN,
        "rows": rows,
        "note": (
            "static half only. The structural guarantee is that an admission "
            "function receives a window ending at the decision bar, so a later "
            "bar is not present in the data it holds."
        ),
    }
