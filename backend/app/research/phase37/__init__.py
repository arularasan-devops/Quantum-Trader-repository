"""Phase 37 — nightly session evidence snapshot across the research tabs.

The problem this solves is bookkeeping, not analysis. One-a-day, A+ Paper, CAS,
Vehicle Evidence and Production Readiness each hold a different slice of the same
session, and each is rebuilt from live state every time the tab is opened. A
session that is never written down is a session that cannot be counted later, so
by the time the floors matter — 20 sessions to look, 50 sessions and 100 trades
to speak generally, 60 sessions and 200 trades for a relative-value claim — the
evidence to count has already been overwritten by the next day's state.

So this package writes, once per session, what those tabs said, and then counts
snapshots. That is the whole of it.

Three properties it must have, and the reasons they are not optional:

**Append-only.** A snapshot is dated evidence. Re-collecting the same session
writes a new file beside the old one rather than replacing it, so a re-run after
a fix can never quietly rewrite what was observed before the fix. The rollup
reads the latest per tab per session and says how many attempts there were.

**A failed fetch is recorded, not skipped.** If the backend was down, or a tab
raised, the snapshot stores the error and the tab counts as MISSING for that
session. Silently omitting it would make a partial session look complete, and a
count of complete sessions is the one number the floors depend on.

**It reads and it counts. It does not conclude.** Nothing here re-derives a
metric, re-prices a fill or ranks a strategy — each tab already owns its own
arithmetic, and a second implementation would eventually disagree with the first
and the disagreement would look like a finding. The rollup's verdicts are about
sample size only: whether a question is *answerable* yet, never what the answer
is. There is no VALIDATED in the vocabulary, because collecting evidence is not
validating it.

    python -m app.research.phase37.cli collect
    python -m app.research.phase37.cli rollup
"""
from __future__ import annotations

from app.research.phase35 import (
    CHECKPOINT1_SESSIONS,
    GENERAL_MIN_SESSIONS,
    GENERAL_MIN_TRADES,
    PAIR_MIN_SESSIONS,
    PAIR_MIN_TRADES,
)

VERSION = "37.0"

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_S = 30.0

# Some tabs rebuild themselves from the whole journal on every request, so their
# response time grows with the store rather than being a property of the route.
# One timeout for all of them means the slowest tab decides whether a session is
# countable, and it gets slower every night. A tab may therefore declare its own
# budget. This is a budget, not a gate: the session is complete when the tab
# ANSWERS, and how long it took is recorded either way.
SLOW_TAB_TIMEOUT_S = 240.0

# A required tab that fails is retried on its own, without re-fetching the tabs
# that already answered. A whole-collection retry costs another pass over every
# store and was, in practice, the reason all three nightly attempts hit the same
# wall inside the same minute.
TAB_ATTEMPTS = 3
TAB_RETRY_BACKOFF_S = 20.0

# ---------------------------------------------------------------------------
# The tabs, and what each one contributes to a countable session.
#
# `count_paths` name where that tab's own population count lives, verified
# against the live payloads rather than assumed. They are read with a tolerant
# walk: a tab that changes shape must make the count UNKNOWN, not crash the
# collector and lose the whole session. The first path that resolves to a number
# wins, which is also the order of preference.
#
# These counts are CUMULATIVE — every one of these endpoints reports over the
# whole store it can see, not over the collection day. So the rollup reports the
# latest value and never sums them across sessions: adding a running total to
# itself once a night would turn one book of 12 trades into a fictional 240.
# ---------------------------------------------------------------------------
TABS: tuple[dict, ...] = (
    {
        "tab": "ONE_A_DAY",
        "label": "One-a-day best card history",
        "path": "/api/daily-best",
        "params": {"limit_days": 120},
        "contributes": "one scheduled trade per session and whether it reached T1",
        "count_paths": ("every_signal.graded", "summary.sessions"),
        "counts": "graded best-card signals",
    },
    {
        "tab": "A_PLUS_PAPER",
        "label": "A+ paper board and costed book",
        "path": "/api/phase17/paper",
        "contributes": "resolved option paper episodes with measured costs",
        "count_paths": ("report.options.entries", "resolved", "report.n"),
        "counts": "resolved paper trades",
    },
    {
        "tab": "A_PLUS_SUMMARY",
        "label": "A+ one-screen daily summary",
        "path": "/api/phase17/summary",
        "contributes": "the EXACT-match capture KPI that gates every A+ table",
        "count_paths": ("candidates", "capture.n"),
        "counts": "candidate observations seen",
    },
    {
        "tab": "CAS",
        "label": "CAS evidence (quality gate, paper economics, verdict)",
        "path": "/api/cas-report",
        # Rebuilds every CAS payload from the full observation, paper and
        # coverage journals per request; measured against the 30s default and
        # lost the 2026-09-15 session to a timeout.
        "timeout_s": SLOW_TAB_TIMEOUT_S,
        "contributes": "closing-auction paper economics and its validation state",
        "count_paths": ("paper.legs", "daily.paper.legs"),
        "counts": "priced CAS paper legs",
    },
    {
        "tab": "VEHICLE_EVIDENCE",
        "label": "Vehicle evidence — every eligible candidate, A+ or not",
        "path": "/api/phase17/evidence",
        "params": {"limit": 400},
        "contributes": "CE/PE/futures books at the decision instant",
        "count_paths": ("counts.total", "rows"),
        "counts": "evidence rows returned",
    },
    {
        "tab": "READINESS",
        "label": "Production readiness with blocking reasons",
        "path": "/api/phase19/readiness",
        "contributes": "per-strategy sample, holdout and blocking reasons",
        "count_paths": ("calibration.rows", "rows"),
        "counts": "calibration rows available",
    },
    {
        "tab": "FEED_HEALTH",
        "label": "Feed health at collection time",
        "path": "/api/feed-health",
        "contributes": "whether the session's data was trustworthy when captured",
        "count_paths": (),
        "counts": None,
    },
)

TAB_NAMES: tuple[str, ...] = tuple(t["tab"] for t in TABS)

# The one tab whose count is a number of resolved paper trades, and therefore the
# only one a trade floor may be measured against. Coverage rows and candidate
# counts are far larger numbers describing different things; reading one of them
# as trades would clear a 200-trade floor in a single evening.
TRADE_COUNT_TAB = "A_PLUS_PAPER"

# A session counts as complete only when every tab that carries evidence was
# collected. FEED_HEALTH is context, not evidence, so it is not required — a
# session is not disqualified because the health endpoint was unavailable.
REQUIRED_TABS: tuple[str, ...] = tuple(
    t["tab"] for t in TABS if t["tab"] != "FEED_HEALTH"
)

# ---------------------------------------------------------------------------
# Status and verdict vocabulary. Deliberately about sample size only.
# ---------------------------------------------------------------------------
COLLECTED = "COLLECTED"
MISSING = "MISSING"
FAILED = "FAILED"

SESSION_COMPLETE = "SESSION_COMPLETE"
SESSION_PARTIAL = "SESSION_PARTIAL"

# What the accumulated snapshots permit. None of these is a result.
BELOW_OBSERVATION_CHECKPOINT = "BELOW_OBSERVATION_CHECKPOINT"
OBSERVATION_CHECKPOINT_MET = "OBSERVATION_CHECKPOINT_MET"
GENERAL_QUESTION_ANSWERABLE = "GENERAL_QUESTION_ANSWERABLE"
RELATIVE_VALUE_QUESTION_ANSWERABLE = "RELATIVE_VALUE_QUESTION_ANSWERABLE"

FLOORS: dict[str, dict] = {
    "observation_checkpoint": {
        "min_sessions": CHECKPOINT1_SESSIONS,
        "min_trades": 0,
        "permits": "look at the tables; no claim of any kind",
    },
    "general_question": {
        "min_sessions": GENERAL_MIN_SESSIONS,
        "min_trades": GENERAL_MIN_TRADES,
        "permits": "a general statement about a strategy's own outcomes",
    },
    "relative_value_question": {
        "min_sessions": PAIR_MIN_SESSIONS,
        "min_trades": PAIR_MIN_TRADES,
        "permits": "a claim that one vehicle beats another",
    },
}
