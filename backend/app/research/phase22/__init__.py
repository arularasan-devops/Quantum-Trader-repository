"""Phase 22 — core profitable setup validation.

RESEARCH AND PAPER ONLY. One candidate strategy, measured end to end:

    SELECTIVE PULLBACK + CONTINUATION + VEHICLE SELECTION

Nothing in this package writes an order, changes the production Option Signal,
its gates, confidence, Signal Score, strike selector, premium floor, stops,
targets, exits or the broker order path. It adds no score and no gate to the
live engine. It exists to answer one question with a verdict that is allowed to
be negative:

    can a selective pullback/continuation setup produce positive NET expectancy
    after actual option/futures costs?
"""
