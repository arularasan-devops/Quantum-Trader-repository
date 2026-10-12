"""Session / signal / trade-event / sizing journal (v2), persisted in the
research database (PostgreSQL when ``QT_DB_URL`` is set, else SQLite).

Observational only: nothing in the trading path reads it back to decide,
size, route or block an order.
"""
