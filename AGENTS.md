# Quantum Trader - AI Agent Instructions

The repository is the source of truth.

AI agents are replaceable workers. Do not rely on conversation memory.

## Mission

Quantum Trader is a research and decision-support platform for Indian equities and derivatives.

The objective is to discover repeatable, executable, cost-adjusted opportunities and implement them safely.

## Current local evidence

The locally recovered research tree contains work through Phase 56.

Phase 57-61 code/artifacts have NOT been found in the local WSL project tree.

Do not invent or recreate missing later-phase code unless explicitly assigned.

## Research rules

Never:

- use future information
- introduce look-ahead bias
- fabricate market data
- fabricate bid/ask
- use theoretical midpoint as an executable fill
- silently fill missing observations
- use survivorship-biased universes
- change registered thresholds after seeing results
- promote in-sample results
- bypass matched controls
- bypass transaction costs

A negative result is a valid result.

## Development

Before changing code:

1. inspect the existing architecture
2. identify affected files
3. explain the intended change
4. run relevant tests

After changing code:

1. run tests
2. inspect git diff
3. produce a report
4. commit only to a dedicated branch

Never force-push.

Never modify main automatically.

## Broker safety

AI agents must not:

- receive Angel One credentials
- receive broker JWT/session tokens
- place broker orders
- enable live execution
- change live broker configuration

Research and execution must remain separate.

## Current research direction

Complete existing Phase 50 executable derivatives capture and prepare the volatility-premium investigation.

Do not start another unrestricted technical strategy search.

