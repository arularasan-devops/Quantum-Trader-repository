# Quantum Trader AI Lab Rules

## Mission

Quantum Trader is a research and decision-support system for Indian equities and derivatives.

The AI agents are replaceable workers.

The repository, research registrations, evidence and governance files are the source of truth.

## Agent roles

OpenHands:
Primary autonomous engineer.

Cline:
Independent code/research auditor.

Gemini:
Second independent reviewer.

## Research integrity

Never:

- use future information
- introduce look-ahead bias
- fabricate data
- fabricate bid/ask
- use theoretical midpoint as an executable fill
- hide missing observations
- modify preregistered thresholds after seeing results
- promote in-sample results
- use survivorship-biased data
- override matched-control failures
- bypass cost models

A negative result is valid.

## Trading safety

The AI agents must not:

- receive broker credentials
- receive Angel One JWT/session tokens
- place broker orders
- modify production execution credentials
- enable live trading
- commit secrets

All research remains separate from live execution.

## Git safety

Never:

- force push
- rewrite shared history
- push directly to main
- delete historical research
- overwrite unknown project copies

All autonomous work must use an isolated branch.

## Promotion

Research -> validation -> holdout -> paper/shadow -> human approval -> possible live deployment.

No agent may bypass the promotion gate.

## Current research state

Phases 51-60 have produced no authorised active strategy.

Phase 60 fundamentals:
ROBUST_CANDIDATES = 0.

The next distinct research branch is executable derivatives / volatility-premium work associated with Phase 50.

Do not start unrestricted technical strategy mining.
