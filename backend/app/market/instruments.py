"""Tradable instrument registry.

Defines the instruments the dashboard can analyse and trade. The simulated feed
uses the price/vol/strike parameters to synthesise a realistic series; the live
Angel One feed uses ``symbol`` / ``exchange`` to resolve the scrip master. Prices
here are only seeds for the simulated feed and are not real quotes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str          # scrip-master name / option root, e.g. "CRUDEOIL"
    display: str         # human label
    exchange: str        # "MCX" / "NFO"
    tv_symbol: str       # TradingView symbol for the chart
    start_price: float   # simulated seed price
    annual_vol: float    # simulated annualised volatility
    strike_step: float   # option strike spacing
    lot_size: int        # contract lot size
    days_to_expiry: int  # simulated days-to-expiry


REGISTRY: dict[str, InstrumentSpec] = {
    "CRUDEOIL": InstrumentSpec("CRUDEOIL", "Crude Oil", "MCX", "MCX:CRUDEOIL", 6900.0, 0.35, 50, 100, 7),
    "NATURALGAS": InstrumentSpec("NATURALGAS", "Natural Gas", "MCX", "MCX:NATURALGAS", 250.0, 0.55, 5, 1250, 7),
    "GOLD": InstrumentSpec("GOLD", "Gold", "MCX", "MCX:GOLD", 71000.0, 0.14, 100, 100, 30),
    "SILVER": InstrumentSpec("SILVER", "Silver", "MCX", "MCX:SILVER", 85000.0, 0.24, 100, 30, 30),
    "COPPER": InstrumentSpec("COPPER", "Copper", "MCX", "MCX:COPPER", 800.0, 0.20, 5, 2500, 30),
    # NSE / BSE index options. Live Angel One uses NFO (Nifty) and BFO (Sensex).
    "NIFTY": InstrumentSpec("NIFTY", "Nifty", "NFO", "NSE:NIFTY", 24000.0, 0.12, 50, 65, 7),
    "BANKNIFTY": InstrumentSpec("BANKNIFTY", "Bank Nifty", "NFO", "NSE:BANKNIFTY", 51000.0, 0.14, 100, 15, 7),
    "SENSEX": InstrumentSpec("SENSEX", "Sensex", "BFO", "BSE:SENSEX", 79000.0, 0.12, 100, 20, 7),
    # NSE single-stock options (OPTSTK, monthly expiry). Live Angel One reads the
    # real lot size from the scrip master; the lot_size below is the simulated-feed
    # seed / fallback and MUST be verified against your broker (SEBI revises them).
    "RELIANCE": InstrumentSpec("RELIANCE", "Reliance", "NFO", "NSE:RELIANCE", 1300.0, 0.22, 10, 500, 30),
    "HDFCBANK": InstrumentSpec("HDFCBANK", "HDFC Bank", "NFO", "NSE:HDFCBANK", 1700.0, 0.20, 10, 550, 30),
    "ICICIBANK": InstrumentSpec("ICICIBANK", "ICICI Bank", "NFO", "NSE:ICICIBANK", 1300.0, 0.22, 10, 700, 30),
    "INFY": InstrumentSpec("INFY", "Infosys", "NFO", "NSE:INFY", 1600.0, 0.24, 10, 400, 30),
    "TCS": InstrumentSpec("TCS", "TCS", "NFO", "NSE:TCS", 3900.0, 0.20, 20, 175, 30),
    "SBIN": InstrumentSpec("SBIN", "SBI", "NFO", "NSE:SBIN", 800.0, 0.26, 5, 750, 30),
    # More liquid F&O single-stock options. lot_size below is a seed/fallback for
    # the simulated feed; the LIVE Angel feed reads the real lot from the scrip
    # master. ``symbol`` MUST equal the exchange scrip-master name (e.g. "LT",
    # "BAJAJ-AUTO"), because live matching compares row["name"] to it.
    "AXISBANK": InstrumentSpec("AXISBANK", "Axis Bank", "NFO", "NSE:AXISBANK", 1150.0, 0.24, 10, 625, 30),
    "KOTAKBANK": InstrumentSpec("KOTAKBANK", "Kotak Bank", "NFO", "NSE:KOTAKBANK", 1800.0, 0.22, 20, 400, 30),
    "LT": InstrumentSpec("LT", "L&T", "NFO", "NSE:LT", 3600.0, 0.22, 20, 175, 30),
    "TATAMOTORS": InstrumentSpec("TATAMOTORS", "Tata Motors", "NFO", "NSE:TATAMOTORS", 700.0, 0.30, 10, 550, 30),
    "TATASTEEL": InstrumentSpec("TATASTEEL", "Tata Steel", "NFO", "NSE:TATASTEEL", 150.0, 0.30, 1, 5500, 30),
    "ITC": InstrumentSpec("ITC", "ITC", "NFO", "NSE:ITC", 450.0, 0.20, 5, 1600, 30),
    "BHARTIARTL": InstrumentSpec("BHARTIARTL", "Bharti Airtel", "NFO", "NSE:BHARTIARTL", 1650.0, 0.24, 20, 475, 30),
    "HCLTECH": InstrumentSpec("HCLTECH", "HCL Tech", "NFO", "NSE:HCLTECH", 1750.0, 0.24, 20, 350, 30),
    "MARUTI": InstrumentSpec("MARUTI", "Maruti", "NFO", "NSE:MARUTI", 12500.0, 0.22, 100, 50, 30),
    "SUNPHARMA": InstrumentSpec("SUNPHARMA", "Sun Pharma", "NFO", "NSE:SUNPHARMA", 1750.0, 0.24, 20, 350, 30),
    "ADANIENT": InstrumentSpec("ADANIENT", "Adani Enterprises", "NFO", "NSE:ADANIENT", 2400.0, 0.34, 20, 300, 30),
    "BAJAJAUTO": InstrumentSpec("BAJAJ-AUTO", "Bajaj Auto", "NFO", "NSE:BAJAJ-AUTO", 11000.0, 0.24, 100, 75, 30),
    # --- wider F&O universe, so the opportunity scan has somewhere to look ---
    # Ranked and filtered like every other name: a thin premium or a quiet tape
    # is skipped by the score, it is not traded just because it is listed. The
    # LIVE feed reads the real lot size and strike grid from the scrip master;
    # the seeds below only drive the simulated feed and the ATM window before
    # the master resolves. ``symbol`` MUST equal the scrip-master name.
    "MIDCPNIFTY": InstrumentSpec("MIDCPNIFTY", "Midcap Nifty", "NFO", "NSE:CNXMIDCAP", 12500.0, 0.16, 25, 120, 7),
    "FINNIFTY": InstrumentSpec("FINNIFTY", "Fin Nifty", "NFO", "NSE:CNXFINANCE", 23500.0, 0.14, 50, 65, 7),
    "BANKEX": InstrumentSpec("BANKEX", "Bankex", "BFO", "BSE:BANKEX", 58000.0, 0.14, 100, 30, 7),
    "ZINC": InstrumentSpec("ZINC", "Zinc", "MCX", "MCX:ZINC", 270.0, 0.20, 5, 5000, 30),
    "ALUMINIUM": InstrumentSpec("ALUMINIUM", "Aluminium", "MCX", "MCX:ALUMINIUM", 240.0, 0.20, 5, 5000, 30),
    "BAJFINANCE": InstrumentSpec("BAJFINANCE", "Bajaj Finance", "NFO", "NSE:BAJFINANCE", 7200.0, 0.26, 100, 125, 30),
    "BAJAJFINSV": InstrumentSpec("BAJAJFINSV", "Bajaj Finserv", "NFO", "NSE:BAJAJFINSV", 1700.0, 0.26, 20, 500, 30),
    "INDUSINDBK": InstrumentSpec("INDUSINDBK", "IndusInd Bank", "NFO", "NSE:INDUSINDBK", 1000.0, 0.30, 10, 700, 30),
    "HINDUNILVR": InstrumentSpec("HINDUNILVR", "HUL", "NFO", "NSE:HINDUNILVR", 2450.0, 0.18, 20, 300, 30),
    "WIPRO": InstrumentSpec("WIPRO", "Wipro", "NFO", "NSE:WIPRO", 280.0, 0.26, 5, 3000, 30),
    "TECHM": InstrumentSpec("TECHM", "Tech Mahindra", "NFO", "NSE:TECHM", 1600.0, 0.26, 20, 600, 30),
    "ULTRACEMCO": InstrumentSpec("ULTRACEMCO", "UltraTech Cement", "NFO", "NSE:ULTRACEMCO", 11500.0, 0.20, 100, 50, 30),
    "TITAN": InstrumentSpec("TITAN", "Titan", "NFO", "NSE:TITAN", 3400.0, 0.24, 20, 175, 30),
    "ASIANPAINT": InstrumentSpec("ASIANPAINT", "Asian Paints", "NFO", "NSE:ASIANPAINT", 2400.0, 0.22, 20, 250, 30),
    "NTPC": InstrumentSpec("NTPC", "NTPC", "NFO", "NSE:NTPC", 350.0, 0.24, 5, 1500, 30),
    "POWERGRID": InstrumentSpec("POWERGRID", "Power Grid", "NFO", "NSE:POWERGRID", 300.0, 0.22, 5, 1800, 30),
    "ONGC": InstrumentSpec("ONGC", "ONGC", "NFO", "NSE:ONGC", 250.0, 0.26, 5, 3850, 30),
    "COALINDIA": InstrumentSpec("COALINDIA", "Coal India", "NFO", "NSE:COALINDIA", 400.0, 0.26, 5, 2100, 30),
    "JSWSTEEL": InstrumentSpec("JSWSTEEL", "JSW Steel", "NFO", "NSE:JSWSTEEL", 950.0, 0.26, 10, 675, 30),
    "HINDALCO": InstrumentSpec("HINDALCO", "Hindalco", "NFO", "NSE:HINDALCO", 650.0, 0.28, 10, 1400, 30),
    "ADANIPORTS": InstrumentSpec("ADANIPORTS", "Adani Ports", "NFO", "NSE:ADANIPORTS", 1350.0, 0.30, 20, 400, 30),
    "DLF": InstrumentSpec("DLF", "DLF", "NFO", "NSE:DLF", 750.0, 0.30, 10, 825, 30),
    "CIPLA": InstrumentSpec("CIPLA", "Cipla", "NFO", "NSE:CIPLA", 1500.0, 0.22, 20, 650, 30),
    "DRREDDY": InstrumentSpec("DRREDDY", "Dr Reddy's", "NFO", "NSE:DRREDDY", 1250.0, 0.22, 10, 625, 30),
    "GRASIM": InstrumentSpec("GRASIM", "Grasim", "NFO", "NSE:GRASIM", 2650.0, 0.22, 20, 250, 30),
    "EICHERMOT": InstrumentSpec("EICHERMOT", "Eicher Motors", "NFO", "NSE:EICHERMOT", 5500.0, 0.24, 50, 175, 30),
    "MM": InstrumentSpec("M&M", "Mahindra & Mahindra", "NFO", "NSE:M_M", 3000.0, 0.26, 20, 200, 30),
    "HEROMOTOCO": InstrumentSpec("HEROMOTOCO", "Hero MotoCorp", "NFO", "NSE:HEROMOTOCO", 4500.0, 0.26, 50, 150, 30),
    "BANKBARODA": InstrumentSpec("BANKBARODA", "Bank of Baroda", "NFO", "NSE:BANKBARODA", 250.0, 0.30, 5, 2925, 30),
    "PNB": InstrumentSpec("PNB", "Punjab National Bank", "NFO", "NSE:PNB", 110.0, 0.32, 2.5, 8000, 30),
    "BPCL": InstrumentSpec("BPCL", "BPCL", "NFO", "NSE:BPCL", 320.0, 0.28, 5, 1800, 30),
    "IOC": InstrumentSpec("IOC", "Indian Oil", "NFO", "NSE:IOC", 145.0, 0.28, 2.5, 4875, 30),
    "GAIL": InstrumentSpec("GAIL", "GAIL", "NFO", "NSE:GAIL", 190.0, 0.28, 2.5, 3150, 30),
    "SAIL": InstrumentSpec("SAIL", "SAIL", "NFO", "NSE:SAIL", 125.0, 0.32, 2.5, 8000, 30),
    "VEDL": InstrumentSpec("VEDL", "Vedanta", "NFO", "NSE:VEDL", 450.0, 0.32, 5, 2300, 30),
    "TATACONSUM": InstrumentSpec("TATACONSUM", "Tata Consumer", "NFO", "NSE:TATACONSUM", 1100.0, 0.24, 10, 550, 30),
    "TRENT": InstrumentSpec("TRENT", "Trent", "NFO", "NSE:TRENT", 5500.0, 0.32, 50, 100, 30),
    "IRCTC": InstrumentSpec("IRCTC", "IRCTC", "NFO", "NSE:IRCTC", 780.0, 0.30, 10, 875, 30),
    "LICI": InstrumentSpec("LICI", "LIC of India", "NFO", "NSE:LICI", 900.0, 0.24, 10, 800, 30),
    # Mid-cap: real F&O name, but its option premiums are thinner than the index
    # contracts — expect the premium floor and the opportunity score to skip it
    # often. That is the gate working, not the instrument missing.
    "KALYANKJIL": InstrumentSpec("KALYANKJIL", "Kalyan Jewellers", "NFO", "NSE:KALYANKJIL", 500.0, 0.40, 10, 1175, 30),
}

DEFAULT_INSTRUMENT = "CRUDEOIL"

# ``REGISTRY`` above is the FULL master (all specs — get_spec always resolves).
# ``UNIVERSE`` is the ACTIVE subset the app actually warms / streams / scans and
# shows in the dropdown. It can be narrowed statically (QT_INSTRUMENTS) or set at
# runtime by the daily auto-pick (set_universe). Empty/unset = the full master.
FNO_STOCKS: tuple[str, ...] = tuple(
    k for k, v in REGISTRY.items() if v.exchange in ("NFO", "BFO") and k not in ("NIFTY", "BANKNIFTY", "SENSEX")
)

# How many instruments may be ACTIVE at once. The WebSocket carries far more
# than this comfortably, but each active instrument also warms 1-min candles
# from Angel's historical API, which is strictly rate-limited (AB1021) — so the
# cap bounds warm-up time, not feed capacity.
MAX_ACTIVE = 50

# Names the ``MAX_ACTIVE`` cap must never be the reason for dropping. Without
# this the master's tail is silently truncated, so a specifically requested
# instrument can be present in the registry, resolve in ``get_spec`` and still
# never be scanned — which reads as a missing instrument rather than a capacity
# limit. A caller that deliberately asks for a shorter list still gets exactly
# what it asked for; pinning only decides WHERE the cap cuts.
PINNED: tuple[str, ...] = (
    "CRUDEOIL",
    "NATURALGAS",
    "GOLD",
    "SILVER",
    "NIFTY",
    "BANKNIFTY",
    "SENSEX",
    "KALYANKJIL",
)


def _apply_cap(names: list[str]) -> list[str]:
    """Trim to ``MAX_ACTIVE``, cutting the discretionary tail rather than a
    pinned name. Lists that already fit are returned untouched."""
    if len(names) <= MAX_ACTIVE:
        return list(names)
    pinned = [s for s in PINNED if s in names]
    rest = [s for s in names if s not in pinned]
    return (pinned + rest)[:MAX_ACTIVE]


UNIVERSE: list[str] = _apply_cap(list(REGISTRY.keys()))


def set_universe(symbols: list[str]) -> list[str]:
    """Set the active instrument universe (the auto-pick uses this daily). Only
    names present in the full master are kept, order preserved; if nothing valid
    is given the full master is restored. Returns the resulting universe."""
    global UNIVERSE, DEFAULT_INSTRUMENT
    kept = [s.upper() for s in symbols if s.upper() in REGISTRY]
    UNIVERSE = _apply_cap(kept or list(REGISTRY.keys()))
    if DEFAULT_INSTRUMENT not in UNIVERSE:
        DEFAULT_INSTRUMENT = UNIVERSE[0]
    return list(UNIVERSE)


def universe_specs() -> list[InstrumentSpec]:
    return [REGISTRY[s] for s in UNIVERSE if s in REGISTRY]


# Optional static limit for focused testing, e.g. QT_INSTRUMENTS=CRUDEOIL,NATURALGAS
# — fewer instruments = far faster warm-up and no Angel history throttling
# (AB1021). Unset = load everything. Takes precedence over the auto-pick.
_only = os.environ.get("QT_INSTRUMENTS", "").strip()
if _only:
    set_universe([s for s in _only.replace(";", ",").split(",") if s.strip()])


def get_spec(symbol: str) -> InstrumentSpec:
    return REGISTRY.get((symbol or "").upper(), REGISTRY[DEFAULT_INSTRUMENT])
