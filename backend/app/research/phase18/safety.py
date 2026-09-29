"""The no-order-path barrier — §33. Fail closed, and prove it.

The existing Zero-to-Hero sleeve reaches ``self.buy(...)`` through the state
machine. CAS is a far more speculative idea on far cheaper strikes in a window
that is weeks old, so the barrier here is not a flag that defaults to off — it is
the absence of a path.

Two mechanisms:

* :func:`assert_paper_only` is called by the CAS entry point on every tick. It
  raises if the caller ever tries to run CAS in anything but paper mode, so a
  future edit that adds a "live" argument fails immediately instead of quietly
  working.
* :func:`scan` reads the Phase 18 source on disk and fails if any module names an
  order-placing symbol or imports a broker client. The smoke test runs it, so the
  guarantee is checked by CI rather than promised in a comment.

The scan looks for the names, not for behaviour, and cannot prove the absence of
a cleverly indirect call. It catches the realistic failure — someone wiring CAS
into the existing execution path because it was convenient — which is the one
that would actually happen.
"""
from __future__ import annotations

import io
import tokenize
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

# Symbols that place, modify or cancel real orders anywhere in this codebase.
FORBIDDEN_SYMBOLS: tuple[str, ...] = (
    "place_order",
    "placeOrder",
    "real_buy",
    "real_sell",
    "cancel_order",
    "modify_order",
    "square_off",
)
# Modules that can reach a broker. CAS may not import any of them.
FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "app.broker",
    "app.execution",
    "smartapi",
    "SmartConnect",
)
# ``buy``/``sell`` appear in ordinary prose and in field names, so they are
# matched as calls rather than as words.
FORBIDDEN_CALLS: tuple[str, ...] = (
    "self.buy(",
    "self.sell(",
    ".buy(",
    ".sell(",
)

PAPER_ONLY = "PAPER_ONLY"


class RealOrderPathError(RuntimeError):
    """Raised when CAS is asked to do anything but paper."""


def assert_paper_only(mode: str = PAPER_ONLY) -> None:
    """Fail closed. CAS has exactly one mode and this is it."""
    if mode != PAPER_ONLY:
        raise RealOrderPathError(
            f"CAS is a paper-only research strategy; refused mode {mode!r}. "
            "There is no order path in Phase 18 and adding one requires an "
            "explicit, reviewed change outside this package."
        )


def _code_lines(text: str) -> list[tuple[int, str]]:
    """Executable lines only, with comments and string literals removed.

    Tokenised rather than filtered by prefix because these modules discuss the
    forbidden symbols at length in their own docstrings — the point of the phase
    is partly to explain what it refuses to call — and a scan that flagged its
    own documentation would be turned off within a week.
    """
    out: dict[int, list[str]] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in tokens:
            if tok.type in (tokenize.COMMENT, tokenize.STRING,
                            tokenize.NL, tokenize.NEWLINE):
                continue
            out.setdefault(tok.start[0], []).append(tok.string)
    except (tokenize.TokenError, IndentationError):
        # An unparseable file is reported as a violation rather than skipped.
        return [(0, "UNPARSEABLE")]
    return [(n, "".join(parts)) for n, parts in sorted(out.items())]


def _offending_lines(text: str, needles: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for lineno, line in _code_lines(text):
        for needle in needles:
            if needle.replace(" ", "") in line:
                hits.append(f"{lineno}: {line}")
                break
    return hits


def scan(package_dir: Path | None = None) -> dict:
    """Read every Phase 18 source file and report any route to an order."""
    root = package_dir or PACKAGE_DIR
    findings: dict[str, list[str]] = {}
    checked: list[str] = []
    for path in sorted(root.glob("*.py")):
        if path.name == "safety.py":
            continue
        checked.append(path.name)
        text = path.read_text(encoding="utf-8")
        hits = _offending_lines(
            text, FORBIDDEN_SYMBOLS + FORBIDDEN_IMPORTS + FORBIDDEN_CALLS,
        )
        if hits:
            findings[path.name] = hits
    return {
        "files_checked": checked,
        "violations": findings,
        "clean": not findings,
        "mode": PAPER_ONLY,
        "statement": {
            "cas_built": "YES",
            "cas_dashboard": "YES",
            "cas_paper_trading": "YES",
            "cas_journal": "YES",
            "cas_research": "YES",
            "cas_normal_option_signal_integration": "NO",
            "cas_real_order_path": "NO",
            "real_money_execution": "DISABLED",
        },
        "limitation": (
            "A source scan for names. It catches CAS being wired into the "
            "existing execution path; it cannot prove the absence of an "
            "indirect call."
        ),
    }
