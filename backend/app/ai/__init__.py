"""Phase 6 AI engine — LIVE PAPER ONLY.

A second decision engine that runs beside the existing production engine. It
never touches production trading logic and, by construction, cannot place a real
order: every broker order path goes through :mod:`app.ai.safety`, which refuses
while paper mode is on.

    scanner (attention)  →  regime  →  direction  →  entry quality
        →  probability  →  risk  →  orchestrator  →  paper executor

The production engine remains the baseline and is evaluated in shadow against
this one on the same live market.
"""
