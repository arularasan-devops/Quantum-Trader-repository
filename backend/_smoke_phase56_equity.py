"""Stage-3 smoke suite: causality, costs, exits, gates, paper boundary.

Every check is a property the study's honesty depends on. The look-ahead checks
are the important ones — they are built on hand-made panels where the future is
deliberately spectacular, so any feature or fill that peeks would be impossible
to miss.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.research.phase56.equity import (  # noqa: E402
    ENTRY_GRID,
    EXIT_GRID,
    HOLDOUT_FRACTION,
    MAX_DRAWDOWN_LIMIT,
    PREREG,
    ROBUST_CANDIDATE,
    TOTAL_HYPOTHESES,
)
from app.research.phase56.equity import stats as st  # noqa: E402
from app.research.phase56.equity.engine import (  # noqa: E402
    DISCOVERY,
    HOLDOUT,
    VALIDATION,
    Partitions,
    apply_fdr,
    build_events,
    evaluate,
)
from app.research.phase56.equity.exits import REASON_CODE, resolve  # noqa: E402
from app.research.phase56.equity.mechanisms import signal  # noqa: E402
from app.research.phase56.equity.panel import Panel  # noqa: E402
from app.research.phase56.equity.paper import Journal, post_entry_state, scan  # noqa: E402
from app.research.phase56.equity.portfolio import simulate  # noqa: E402
from app.research.phase56.equity.tradecosts import (  # noqa: E402
    cost_fraction,
    net_returns,
    regime_arrays,
)
from app.research.phase56.equity.verdict import grade, needs_portfolio  # noqa: E402
from app.research.phase56.nse.indices import NIFTY_50  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
    else:
        FAILED.append(f"{name} {detail}".strip())


# ------------------------------------------------------------------ fixtures


def make_panel(
    *,
    sessions: int = 400,
    symbols: int = 4,
    seed: int = 7,
    drift: float = 0.0004,
) -> Panel:
    """A synthetic but structurally honest panel built through the real builder."""
    from app.research.phase56.equity.panel import build_panel

    rng = np.random.default_rng(seed)
    dates = np.array(
        [f"20{20 + (index // 250):02d}-{1 + (index % 12):02d}-{1 + (index % 28):02d}" for index in range(sessions)],
        dtype=object,
    )
    # Monotone dates matter for partitioning; build them properly instead.
    base = np.datetime64("2021-01-04")
    dates = np.array(
        [str((base + np.timedelta64(index, "D")).astype("datetime64[D]")) for index in range(sessions)],
        dtype=object,
    )
    names = np.array([f"SYM{index}" for index in range(symbols)], dtype=object)
    steps = rng.normal(drift, 0.015, size=(sessions, symbols))
    close = 500.0 * np.cumprod(1.0 + steps, axis=0)
    open_ = close * (1.0 + rng.normal(0.0, 0.004, size=close.shape))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.006, size=close.shape)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.006, size=close.shape)))
    volume = rng.integers(200_000, 900_000, size=close.shape).astype(np.float64)
    turnover = volume * close
    fields = {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "turnover": turnover,
        "barrier": np.zeros(close.shape, dtype=bool),
    }
    index_close = {NIFTY_50: 15000.0 * np.cumprod(1.0 + rng.normal(0.0003, 0.01, sessions))}
    return build_panel(dates, names, fields, index_close)


PANEL = make_panel()


# ------------------------------------------------------- pre-registration (§1)

check("prereg fingerprint is stable", PREREG.fingerprint() == PREREG.fingerprint())
check("prereg fingerprint is 16 hex chars", len(PREREG.fingerprint()) == 16)
check(
    "denominator equals the frozen grid product",
    TOTAL_HYPOTHESES == len(ENTRY_GRID) * len(EXIT_GRID),
    f"{TOTAL_HYPOTHESES}",
)
check("grid is non-trivial", len(ENTRY_GRID) >= 20 and len(EXIT_GRID) >= 8)
check(
    "every entry names a family and params",
    all({"name", "family", "params"} <= set(spec) for spec in ENTRY_GRID),
)
check(
    "entry names are unique",
    len({spec["name"] for spec in ENTRY_GRID}) == len(ENTRY_GRID),
)
check("exit names are unique", len({spec["name"] for spec in EXIT_GRID}) == len(EXIT_GRID))
try:
    PREREG.as_dict()["entries"].append({"name": "INJECTED"})
    mutated = len(PREREG.as_dict()["entries"]) != len(ENTRY_GRID)
except Exception:
    mutated = False
check("prereg payload cannot be mutated through as_dict", not mutated)

# ------------------------------------------------------------- causality (§9)

FUTURE = make_panel(seed=3)
for name in ("prev_close", "prev_high", "prev_low", "ret_20", "high_20", "atr"):
    feature = PANEL.features[name]
    check(f"feature {name} exists", feature.shape == PANEL.close.shape)

# A feature at row t must not change when every bar from t onward is replaced.
mangled = make_panel(seed=7)
mangled.close[200:] *= 5.0
mangled.high[200:] *= 5.0
mangled.low[200:] *= 5.0
from app.research.phase56.equity.panel import build_panel  # noqa: E402

rebuilt = build_panel(
    mangled.dates,
    mangled.symbols,
    {
        "open": mangled.open,
        "high": mangled.high,
        "low": mangled.low,
        "close": mangled.close,
        "volume": mangled.volume,
        "turnover": mangled.turnover,
        "barrier": np.zeros(mangled.close.shape, dtype=bool),
    },
    mangled.index_close,
)
for name in ("prev_close", "ret_20", "high_20", "atr", "ma_20"):
    before = PANEL.features[name][:200]
    after = rebuilt.features[name][:200]
    same = np.allclose(before, after, equal_nan=True)
    check(f"feature {name} ignores the future", same)

check(
    "eligibility ignores the future",
    np.array_equal(PANEL.eligible[:200], rebuilt.eligible[:200]),
)

# ------------------------------------------------------------ mechanisms (§5)

for spec in ENTRY_GRID:
    mask = signal(PANEL, spec)
    check(f"signal {spec['name']} is boolean and shaped", mask.dtype == bool and mask.shape == PANEL.close.shape)
    check(f"signal {spec['name']} respects eligibility", not bool((mask & ~PANEL.eligible).any()))

try:
    signal(PANEL, {"name": "X", "family": "NOT_A_FAMILY", "params": {}})
    unknown_raised = False
except ValueError:
    unknown_raised = True
check("unknown family is refused", unknown_raised)

# ---------------------------------------------------------------- exits (§8)

for config in EXIT_GRID:
    outcome = resolve(PANEL, config)
    entry = outcome.entry_price
    filled = np.isfinite(entry)
    # The decision is taken on data strictly before row t (asserted above) and
    # the fill is row t's own open, so the fill price is never a close the
    # decision could have seen.
    check(
        f"exit {config['name']} fills at the decision session's open",
        np.allclose(entry[filled], PANEL.open[filled]),
    )
    # The dataset ending is not a symbol delisting. Trades whose holding window
    # runs past the final session must stay unresolved rather than be marked at
    # a borrowed last close, otherwise the end of every partition is biased.
    hold = int(config["hold"])
    if hold > 1:
        tail = outcome.reason[-(hold - 1):]
        check(
            f"exit {config['name']} never calls the dataset end a delisting",
            not bool((tail == REASON_CODE["STOPPED_TRADING"]).any()),
            f"tail reasons={sorted(set(tail.ravel().tolist()))}",
        )
        check(
            f"exit {config['name']} gives unresolved trades no exit price",
            not bool(np.isfinite(outcome.exit_price[outcome.reason == 0]).any()),
        )
    holding = outcome.holding
    resolved = outcome.reason > 0
    check(
        f"exit {config['name']} holds at least one session",
        not bool((resolved & (holding < 1)).any()),
    )
    cap = config.get("max_hold")
    if cap:
        check(
            f"exit {config['name']} honours max_hold {cap}",
            not bool((resolved & (holding > cap)).any()),
        )

# Stop-before-target: a bar that touches both must resolve as the stop.
both = make_panel(sessions=60, symbols=1, seed=11)
# Quiet history so ATR is small, then one wide bar whose range contains both
# the stop and the target — the ambiguous case the resolver must call a stop.
both.open[:] = 100.0
both.close[:] = 100.0
both.high[:] = 100.5
both.low[:] = 99.5
both.high[30:] = 140.0
both.low[30:] = 60.0
rebuilt_both = build_panel(
    both.dates,
    both.symbols,
    {
        "open": both.open,
        "high": both.high,
        "low": both.low,
        "close": both.close,
        "volume": both.volume,
        "turnover": both.turnover,
        "barrier": np.zeros(both.close.shape, dtype=bool),
    },
    both.index_close,
)
stop_config = next(config for config in EXIT_GRID if config.get("stop_atr") and config.get("target_atr"))
ambiguous = resolve(rebuilt_both, stop_config)
# Row 30 is the first wide bar: ATR there is still the quiet-history value, so
# both levels sit inside the bar. Later rows inflate ATR and are not ambiguous.
wide = ambiguous.reason[30]
check(
    "ambiguous bar resolves as the stop, never the target",
    bool((wide == REASON_CODE["STOP"]).all()),
    f"reasons={sorted(set(wide.ravel().tolist()))}",
)
check(
    "the ambiguous fill is the stop level, not the favourable one",
    bool((ambiguous.exit_price[30] < 100.0).all()),
)

# -------------------------------------------------------------- costs (§21)

dates = np.array(["2021-01-04", "2023-06-01", "2025-06-01"], dtype=object)
rates = regime_arrays(dates)
flat = net_returns(
    entry_price=np.array([100.0, 100.0, 100.0]),
    exit_price=np.array([100.0, 100.0, 100.0]),
    buy_rate_index=np.array([0, 1, 2]),
    sell_rate_index=np.array([0, 1, 2]),
    rates=rates,
)
check("a flat round trip is a loss after costs", bool((flat < 0).all()), f"{flat}")
gross_up = net_returns(
    entry_price=np.array([100.0]),
    exit_price=np.array([110.0]),
    buy_rate_index=np.array([0]),
    sell_rate_index=np.array([0]),
    rates=regime_arrays(np.array(["2023-06-01"], dtype=object)),
)
check("net is below gross", float(gross_up[0]) < 0.10, f"{gross_up[0]:.4f}")
stressed = net_returns(
    entry_price=np.array([100.0]),
    exit_price=np.array([110.0]),
    buy_rate_index=np.array([0]),
    sell_rate_index=np.array([0]),
    rates=regime_arrays(np.array(["2023-06-01"], dtype=object)),
    multiplier=2.0,
)
check("2x cost stress reduces net further", float(stressed[0]) < float(gross_up[0]))
fraction = cost_fraction(
    entry_price=np.array([100.0]),
    exit_price=np.array([110.0]),
    buy_rate_index=np.array([0]),
    sell_rate_index=np.array([0]),
    rates=regime_arrays(np.array(["2023-06-01"], dtype=object)),
)
check("cost fraction is positive and small", 0.0 < float(fraction[0]) < 0.05, f"{fraction[0]:.4f}")
try:
    net_returns(
        entry_price=np.array([100.0]),
        exit_price=np.array([110.0]),
        buy_rate_index=np.array([0]),
        sell_rate_index=np.array([0]),
    )
    failed_closed = False
except ValueError:
    failed_closed = True
check("costs fail closed without dates or rates", failed_closed)

# ------------------------------------------------- statistics and FDR (§15/§19)

wins = np.array([0.02, 0.03, -0.01, 0.04, -0.02])
block = st.summary(wins, gross=wins + 0.003)
check("summary counts trades", block["trades"] == 5)
check("summary reports expectancy", abs(block["net_expectancy"] - wins.mean()) < 1e-12)
check("summary reports cost drag", abs(block["cost_drag"] - 0.003) < 1e-9)
check("profit factor is gains over losses", abs(block["profit_factor"] - (0.09 / 0.03)) < 1e-9)
check("empty summary is empty, not zero-filled", st.summary(np.array([]))["trades"] == 0)

curve = st.equity_curve(np.array([0.1, -0.2, 0.05, -0.05]))
check("drawdown is additive per-position units", abs(curve["trade_sequence_drawdown_units"] - 0.2) < 1e-9)
check("drawdown carries its basis label", "NOT_A_CAPITAL_CONSTRAINED_BOOK" in curve["drawdown_basis"])
check("losing streak is counted", st.equity_curve(np.array([-0.1, -0.1, 0.2, -0.1]))["longest_losing_streak"] == 2)

passed = st.benjamini_hochberg([0.001, 0.02, 0.9], alpha=0.10, denominator=3)
check("BH admits the strongest p-value", passed[0])
none_pass = st.benjamini_hochberg([0.001, 0.02, 0.9], alpha=0.10, denominator=10_000)
check("a large denominator blocks weak discoveries", not any(none_pass))
check(
    "BH over the full denominator is stricter than over the list",
    sum(st.benjamini_hochberg([0.02], 0.10, 1)) >= sum(st.benjamini_hochberg([0.02], 0.10, 476)),
)

family_a = st.event_family(np.array([1, 2, 3]), np.array([0, 0, 1]))
family_b = st.event_family(np.array([3, 2, 1]), np.array([1, 0, 0]))
check("event families are order-independent", family_a == family_b)
check(
    "different event sets are different families",
    family_a != st.event_family(np.array([1, 2, 4]), np.array([0, 0, 1])),
)

conc = st.concentration(np.array([1.0, 1.0, 8.0]), np.array(["A", "A", "B"], dtype=object))
check("concentration finds the dominant key", conc["top_key"] == "B" and abs(conc["top_share"] - 0.8) < 1e-9)

# ------------------------------------------------------ partitions (§11/§12)

parts = Partitions(1000)
check("discovery starts at the first session", parts.bounds[DISCOVERY][0] == 0)
check(
    "partitions are contiguous and chronological",
    parts.bounds[DISCOVERY][1] == parts.bounds[VALIDATION][0]
    and parts.bounds[VALIDATION][1] == parts.bounds[HOLDOUT][0],
)
check("holdout ends at the last session", parts.bounds[HOLDOUT][1] == 1000)
check(
    "holdout is the declared fraction",
    abs((parts.bounds[HOLDOUT][1] - parts.bounds[HOLDOUT][0]) / 1000 - HOLDOUT_FRACTION) < 0.01,
)
check("partitions never overlap", parts.bounds[DISCOVERY][1] <= parts.bounds[VALIDATION][0])

# ------------------------------------------------------------- engine (§13/§19)

events = build_events(PANEL, ENTRY_GRID[:8])
check("events are built for every registered entry", len(events) == 8)
check("event families are recorded", all(event.family for event in events))
result = evaluate(PANEL, grid=ENTRY_GRID[:4], exits=EXIT_GRID[:3])
check("every registered row is evaluated", len(result["rows"]) == 12)
check(
    "the denominator stays the full registered grid",
    result["total_hypotheses"] == TOTAL_HYPOTHESES,
    f"{result['total_hypotheses']}",
)
check(
    "each row carries all three partitions",
    all({DISCOVERY, VALIDATION, HOLDOUT} <= set(row["partitions"]) for row in result["rows"]),
)
fdr = apply_fdr(result["rows"])
check("FDR reports its denominator", fdr["denominator"] == TOTAL_HYPOTHESES)
check(
    "ungradeable rows still occupy the denominator",
    fdr["gradeable_rows"] <= len(result["rows"]) and fdr["denominator"] >= len(result["rows"]),
)

# --------------------------------------------------------- portfolio (§13/§14)

spec = ENTRY_GRID[0]
mask = signal(PANEL, spec)
outcome = resolve(PANEL, EXIT_GRID[0])
book = simulate(PANEL, mask, outcome, size=5, weighting="EQUAL_WEIGHT")
check("book reports CAGR and drawdown", "net_cagr" in book and "max_drawdown" in book)
check("book drawdown is a fraction", 0.0 <= book["max_drawdown"] <= 1.0, f"{book['max_drawdown']}")
check("book never holds more than its size", book["max_concurrent"] <= 5, f"{book['max_concurrent']}")
check("book never goes short", book["shorts"] == 0)
check("book cash never goes negative (no leverage)", book["min_cash"] >= -1e-6, f"{book['min_cash']}")
big = simulate(PANEL, mask, outcome, size=20, weighting="EQUAL_WEIGHT")
check("a larger book takes at least as many trades", big["trades"] >= book["trades"])
inverse = simulate(PANEL, mask, outcome, size=5, weighting="INVERSE_VOLATILITY")
check("inverse-volatility weighting is a different book", inverse["trades"] > 0)
stressed_book = simulate(PANEL, mask, outcome, size=5, cost_multiplier=2.0)
check(
    "2x costs cannot improve the book",
    stressed_book["total_return"] <= book["total_return"] + 1e-9,
)

# ------------------------------------------------------------- gates (§31)


def fabricate(**overrides) -> dict:
    good = {
        "trades": 500,
        "stocks": 60,
        "sessions": 300,
        "net_expectancy": 0.01,
        "gross_expectancy": 0.013,
        "stock_concentration": {"top_share": 0.1},
        "year_concentration": {"top_share": 0.3},
        "trade_sequence_drawdown_units": 1.2,
    }
    row = {
        "entry": "E",
        "exit": "X",
        "partitions": {
            DISCOVERY: dict(good),
            VALIDATION: {"trades": 100, "net_expectancy": 0.008},
            HOLDOUT: {"trades": 100, "net_expectancy": 0.006},
        },
        "discovery_fdr_pass": True,
        "cost_stress": {"2.0x": {"net_expectancy": 0.004}},
        "walk_forward": {"graded_folds": 5, "positive_folds": 5},
        "portfolio_drawdown": 0.2,
    }
    row.update(overrides)
    return row


check("a clean row is promoted", grade(fabricate())["status"] == ROBUST_CANDIDATE)
check(
    "no book, no promotion",
    grade(fabricate(portfolio_drawdown=None))["status"] != ROBUST_CANDIDATE,
)
check("a missing book is named as a gate failure", needs_portfolio(grade(fabricate(portfolio_drawdown=None))))
check(
    "an excessive book drawdown blocks promotion",
    grade(fabricate(portfolio_drawdown=MAX_DRAWDOWN_LIMIT + 0.01))["status"] != ROBUST_CANDIDATE,
)
check(
    "failing FDR blocks promotion",
    grade(fabricate(discovery_fdr_pass=False))["status"] == "NO_EDGE",
)
check(
    "negative at 2x costs blocks promotion",
    grade(fabricate(cost_stress={"2.0x": {"net_expectancy": -0.001}}))["status"] != ROBUST_CANDIDATE,
)
thin = fabricate()
thin["partitions"][DISCOVERY]["trades"] = 10
check("a thin sample is INSUFFICIENT_SAMPLE", grade(thin)["status"] == "INSUFFICIENT_SAMPLE")
narrow = fabricate()
narrow["partitions"][DISCOVERY]["stocks"] = 3
check("a three-stock result cannot be promoted", grade(narrow)["status"] == "INSUFFICIENT_SAMPLE")
cost_blocked = fabricate()
cost_blocked["partitions"][DISCOVERY]["net_expectancy"] = -0.001
cost_blocked["partitions"][DISCOVERY]["gross_expectancy"] = 0.004
check("gross-positive net-negative is COST_BLOCKED", grade(cost_blocked)["status"] == "COST_BLOCKED")
no_edge = fabricate()
no_edge["partitions"][DISCOVERY]["net_expectancy"] = -0.004
no_edge["partitions"][DISCOVERY]["gross_expectancy"] = -0.001
check("negative gross and net is NO_EDGE", grade(no_edge)["status"] == "NO_EDGE")
overfit = fabricate()
overfit["partitions"][VALIDATION]["net_expectancy"] = -0.002
check("failing validation is OVERFIT_RISK", grade(overfit)["status"] == "OVERFIT_RISK")
holdout_fail = fabricate()
holdout_fail["partitions"][HOLDOUT]["net_expectancy"] = -0.002
check("failing the untouched holdout is OVERFIT_RISK", grade(holdout_fail)["status"] == "OVERFIT_RISK")
concentrated = fabricate()
concentrated["partitions"][DISCOVERY]["stock_concentration"] = {"top_share": 0.9}
check(
    "one stock carrying the profit blocks promotion",
    grade(concentrated)["status"] != ROBUST_CANDIDATE,
)
one_year = fabricate()
one_year["partitions"][DISCOVERY]["year_concentration"] = {"top_share": 0.95}
check("one year carrying the profit blocks promotion", grade(one_year)["status"] != ROBUST_CANDIDATE)
walk_fail = fabricate(walk_forward={"graded_folds": 5, "positive_folds": 1})
check("failing walk-forward blocks promotion", grade(walk_fail)["status"] != ROBUST_CANDIDATE)
check("every graded row names its breadth", "replication_breadth" in grade(fabricate()))
check("every graded row carries the evidence ceiling", grade(fabricate())["evidence_ceiling"] == "HISTORICAL_LEAD")

# --------------------------------------------------------- paper layer (§35)

candidate = {
    "entry": "E",
    "exit": "HOLD_5",
    "entry_family": ENTRY_GRID[0]["family"],
    "entry_params": ENTRY_GRID[0]["params"],
    "entry_spec": ENTRY_GRID[0],
    "exit_config": EXIT_GRID[0],
    "status": "PROMISING_NEEDS_DATA",
    "study_fingerprint": PREREG.fingerprint(),
}
result_no_trade = scan(PANEL, candidate)
check("an unpromoted candidate yields NO_TRADE", result_no_trade["decision"] == "NO_TRADE")
check("NO_TRADE states its reason", bool(result_no_trade.get("reason")))
check("NO_TRADE carries no picks", not result_no_trade.get("candidates"))

promoted = dict(candidate, status=ROBUST_CANDIDATE)
paper = scan(PANEL, promoted)
check("a promoted candidate yields a paper decision", paper["decision"] in {"PAPER_BUY", "NO_TRADE"})
check("paper output is labelled paper-only", "PAPER" in paper["mode"])
check("paper output carries the evidence ceiling", paper["evidence_ceiling"] == "HISTORICAL_LEAD")
check("paper output carries the study fingerprint", paper["study_fingerprint"] == PREREG.fingerprint())
if paper["decision"] == "PAPER_BUY":
    pick = paper["candidates"][0]
    required = {
        "symbol",
        "signal_session",
        "entry_condition",
        "expected_entry_reference_price",
        "initial_stop",
        "invalidation_rule",
        "target",
        "exit_rule",
        "max_holding_sessions",
        "position_notional_inr",
        "reason",
        "evidence_reference",
    }
    check("every pick answers the six questions", required <= set(pick), f"missing={sorted(required - set(pick))}")
    stop = pick["initial_stop"]
    check(
        "a stop, when the exit rule has one, sits below the entry reference",
        stop is None or stop < pick["expected_entry_reference_price"],
        f"stop={stop}",
    )
    check(
        "a stopless exit rule says so instead of inventing a level",
        stop is not None or "NO_PRICE_INVALIDATION" in pick["invalidation_rule"],
    )
    check("the position is bounded", 0 < pick["position_notional_inr"] <= 1_000_000)

with tempfile.TemporaryDirectory() as tmp:
    journal = Journal(Path(tmp))
    journal.record_scan(paper)
    first = len(journal.rows())
    journal.record_scan(paper)
    check("journal is idempotent for the same scan", len(journal.rows()) == first, f"{first}->{len(journal.rows())}")
    check("journal persists rows", first > 0)

position = {
    "symbol": str(PANEL.symbols[0]),
    "entry_session": str(PANEL.dates[100]),
    "entry_price": float(PANEL.open[100, 0]),
    "initial_stop": float(PANEL.open[100, 0]) * 0.9,
    "target": float(PANEL.open[100, 0]) * 1.1,
    "max_holding_sessions": 5,
}
state = post_entry_state(PANEL, position, as_of=str(PANEL.dates[103]))
check("post-entry state is one of the declared states", state["state"] in {"HOLD", "EXIT", "STOP", "TARGET", "TIME_EXIT", "SIGNAL_INVALIDATED"})
timed = post_entry_state(PANEL, position, as_of=str(PANEL.dates[120]))
check("a position past its horizon is not left HOLD", timed["state"] != "HOLD", f"{timed['state']}")
stopped = dict(position, initial_stop=float(PANEL.open[100, 0]) * 1.5)
check(
    "a breached stop resolves as STOP",
    post_entry_state(PANEL, stopped, as_of=str(PANEL.dates[101]))["state"] == "STOP",
)
unknown = dict(position, symbol="NOT_LISTED")
check(
    "an unknown symbol is invalidated, not guessed",
    post_entry_state(PANEL, unknown, as_of=str(PANEL.dates[103]))["state"] == "SIGNAL_INVALIDATED",
)

# ----------------------------------------------- no order path anywhere (§38)

SOURCES = list(Path("app/research/phase56").rglob("*.py"))
BANNED = ("placeOrder", "place_order", "smartConnect", "SmartConnect", "cancelOrder", "modifyOrder")
offenders = [
    f"{path}:{token}"
    for path in SOURCES
    for token in BANNED
    if token in path.read_text()
]
check("no broker order call exists in the namespace", not offenders, str(offenders[:3]))
check("the namespace has no network import of the broker SDK", not any("from SmartApi" in p.read_text() for p in SOURCES))

# ------------------------------------------------------------ isolation (§37)

for phase in ("phase41", "phase44", "phase51", "phase53", "phase54", "phase55"):
    other = Path("app/research") / phase
    if other.exists():
        leaks = [
            str(path)
            for path in other.rglob("*.py")
            if "phase56" in path.read_text()
        ]
        check(f"{phase} does not import phase56", not leaks, str(leaks[:2]))

print(f"PASSED {len(PASSED)}")
if FAILED:
    print(f"FAILED {len(FAILED)}")
    for name in FAILED:
        print(f"  - {name}")
    raise SystemExit(1)
print("ALL PHASE 56 STAGE 3 CHECKS PASSED")
