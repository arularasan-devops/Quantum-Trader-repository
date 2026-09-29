"""Phase 7 — real-chain entry/exit intelligence. RESEARCH ONLY.

Nothing in this package is imported by the production decision path, and nothing
here can place an order. It reads recorded history (``history.db`` candles and
chain snapshots) and reports; the production engine, its gates, stops, targets,
strike selector and the source-level real-money guard are untouched by design.

The package is deliberately split by *question*, not by convenience:

``dataset``      loading + provenance separation (REAL_BROKER / SIMULATOR / UNKNOWN)
``quality``      Part 1-2: is the recorded data fit to answer anything at all?
``paths``        Part 3-5, 7, 21: production BUYs, real premium paths, chase, MFE capture
``policies``     Part 6, 8, 14, 16: research-only entry/exit policies on those paths
``attribution``  Part 22-23: signal quality vs execution quality; manual vs bot
``gates``        Part 24-25: what the data is allowed to conclude
"""
