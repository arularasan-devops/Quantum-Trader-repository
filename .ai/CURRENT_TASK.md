# Current Autonomous Task

DO NOT start another generic strategy-search phase.

First:

1. Identify the canonical Quantum Trader project.
2. Verify its current state.
3. Verify Phase 50 capture.
4. Verify Phase 50's 1-second fast tier.
5. Verify Phase 50's 15-second slow tier.
6. Verify executable bid/ask quality.
7. Prepare volatility-premium research.
8. Keep all work PAPER/SHADOW ONLY.
9. Never access broker order credentials.

If the canonical project has not yet been assembled, do not modify an arbitrary existing Quantum Trader directory.


---

## Update (2026-10-12, human-directed): journal + sizing before further research

The task above is preserved. The project owner has explicitly prioritised the
"Autonomous Research, Dynamic Position Sizing, Complete Journal and Reports"
specification, in this order:

- [x] Phase B/C increment 1: journal v2 tables (sessions, signals, trade
      events, sizing decisions) in PostgreSQL, observational sizing comparison,
      read-only journal API. See `docs/JOURNAL_V2.md`. Synthetic tests only.
- [ ] Owner runs the local checks against real data (commands in the PR).
- [ ] Phase D: matched-session replay of sizing scenarios (no look-ahead).
- [ ] Phase E: MFE/MAE + staged exit-policy comparison where intratrade paths exist.
- [ ] Phase F: Journal/Reports UI, exports.
- [ ] Phase G/H: fixtures, reproducibility checks, paper/shadow cycle.

Phase 55 status (from its own artefacts): PROMOTED = 0, nothing survived
universe-wide FDR, and only 4 of 20 declared instruments were graded. No
Phase 53/54 mechanism is authorised as a strategy. Do not start another
unrestricted strategy search. The pullback work validates the EXISTING
trend+pullback strategy and its exits only.

PAPER/SHADOW ONLY. No broker credentials. No live orders.
