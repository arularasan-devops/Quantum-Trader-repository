"""Frozen BANKNIFTY/NIFTY relative-value capture. Research only.

This package captures evidence for ONE pre-registered relationship and for the
near/next futures basis. It contains no search, no parameter fitting and no
order path: the configuration is frozen in :mod:`app.research.pairs.spec` and
fingerprinted, so a later retest is a retest and not a new hypothesis.
"""
from __future__ import annotations

from app.research.pairs.capture import health, observe_leg  # noqa: F401
from app.research.pairs.spec import FROZEN, fingerprint  # noqa: F401
