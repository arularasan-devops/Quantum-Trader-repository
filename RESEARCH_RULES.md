# Quantum Trader Research Governance

## Evidence hierarchy

A profitable backtest is not sufficient.

A candidate should survive, where applicable:

- point-in-time validation
- chronological discovery
- validation
- untouched holdout
- realistic transaction costs
- matched random controls
- time-shift controls
- sample-size requirements
- robustness across periods
- execution realism
- no leakage
- no survivorship bias

## Status values

INSUFFICIENT_SAMPLE
NO_EDGE
COST_BLOCKED
CONTROL_FAILED
OVERFIT_RISK
EXECUTION_UNAVAILABLE
RESEARCH_VALIDATED
NOT_AUTHORIZED

## Promotion

RESEARCH_VALIDATED does not mean LIVE.

The sequence is:

research
→ validation
→ paper/shadow
→ human review
→ possible live deployment

No AI agent may bypass this sequence.
