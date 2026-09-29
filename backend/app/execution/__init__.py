"""Phase 3.6 Execution Intelligence Layer.

ADVISORY-ONLY, feature-flagged (``QT_EXECUTION_INTELLIGENCE_ENABLED``). These
modules run AFTER the frozen Baseline engine and Risk Management v2 and NEVER
change the BUY/WAIT direction, confidence, or strategy — they only assess
*execution quality* (enter now / wait / change strike / skip) and always explain
WHY. Everything here reads values the engine already produced; it places no
orders and writes no engine state.
"""
