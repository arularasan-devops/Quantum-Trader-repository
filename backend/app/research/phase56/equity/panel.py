"""The panel: date x symbol matrices, every feature causal by construction.

One design rule runs through this module and is worth stating before the code:
**a feature for session ``t`` is computed only from sessions strictly before
``t``.** It is implemented by shifting every input series by one session *once*,
at the point the matrices are assembled, rather than by remembering to shift in
each mechanism. A mechanism therefore cannot see session ``t``'s own bar even if
its author forgets, because that bar is not in the matrices it is handed.

Fills happen at ``open[t]``, which is the first price observable after the
decision. `ret_5[t]` means "the five-session return as known at the close of
t-1". There is no code path from `high[t]`, `low[t]` or `close[t]` into any entry
condition, and the smoke suite asserts it by feeding a panel whose final session
is a fabricated +50% bar and checking no signal changes.

Corporate-action barriers are honoured the same way: `bars_since_barrier[t, s]`
counts sessions since the last unresolved adjustment boundary for that symbol,
and a feature with lookback ``k`` is masked out wherever that count is below
``k``. A 20-day high that spans an unexplained split boundary is not a 20-day
high, it is an artefact, so it is withheld rather than used.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import (
    ATR_LOOKBACK,
    HIGH_LOOKBACKS,
    MA_LOOKBACKS,
    RETURN_LOOKBACKS,
    VOL_LOOKBACK,
    VOLUME_LOOKBACK,
)
from ..nse import (
    UNIVERSE_LOOKBACK_SESSIONS,
    UNIVERSE_MIN_MEDIAN_TURNOVER_INR,
    UNIVERSE_MIN_PRICE_INR,
    UNIVERSE_MIN_PRIOR_SESSIONS,
    UNIVERSE_TOP_N_BY_TURNOVER,
)
from ..nse.indices import NIFTY_50, NIFTY_500, IndexStore

CACHE_NAME = "equity_panel.npz"


def _shift_down(matrix: np.ndarray, periods: int = 1) -> np.ndarray:
    """Move every row ``periods`` sessions later, i.e. make it past information."""
    out = np.full_like(matrix, np.nan)
    if periods <= 0:
        return matrix.copy()
    out[periods:] = matrix[:-periods]
    return out


#: Symbols per rolling chunk. A 252-session window over the full panel would
#: materialise several gigabytes inside numpy's NaN-aware reductions (they copy),
#: so the reduction is done in column blocks with bounded peak memory.
ROLLING_CHUNK = 128


def _rolling(matrix: np.ndarray, window: int, func) -> np.ndarray:
    """``func`` over a trailing window, NaN until the window is full."""
    days, symbols = matrix.shape
    out = np.full((days, symbols), np.nan, dtype=np.float32)
    if window <= 0 or days < window:
        return out
    with np.errstate(invalid="ignore", all="ignore"):
        for start in range(0, symbols, ROLLING_CHUNK):
            block = matrix[:, start : start + ROLLING_CHUNK]
            view = np.lib.stride_tricks.sliding_window_view(block, window, axis=0)
            out[window - 1 :, start : start + ROLLING_CHUNK] = func(view, axis=-1)
    return out


def _nanmax(view, axis):
    return np.nanmax(view, axis=axis)


def _nanmin(view, axis):
    return np.nanmin(view, axis=axis)


def _nanmean(view, axis):
    return np.nanmean(view, axis=axis)


def _nanmedian(view, axis):
    return np.nanmedian(view, axis=axis)


def _nanstd(view, axis):
    return np.nanstd(view, axis=axis)


@dataclass
class Panel:
    """Raw-shaped price matrices plus the causal feature set."""

    dates: np.ndarray  # (D,) ISO strings
    symbols: np.ndarray  # (S,)
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    turnover: np.ndarray
    bars_since_barrier: np.ndarray
    features: dict[str, np.ndarray]
    eligible: np.ndarray  # bool (D, S)
    index_close: dict[str, np.ndarray]

    @property
    def shape(self) -> tuple[int, int]:
        return self.close.shape

    def date_index(self) -> dict[str, int]:
        return {day: position for position, day in enumerate(self.dates)}

    def years(self) -> np.ndarray:
        return np.array([day[:4] for day in self.dates])


def _read_symbol(path: Path) -> dict:
    dates: list[str] = []
    rows: list[tuple[float, float, float, float, float, float]] = []
    barriers: list[str] = []
    with path.open(newline="") as handle:
        for raw in csv.DictReader(handle):
            dates.append(raw["trade_date"])
            rows.append(
                (
                    float(raw["open"] or 0.0),
                    float(raw["high"] or 0.0),
                    float(raw["low"] or 0.0),
                    float(raw["close"] or 0.0),
                    float(raw["volume"] or 0.0),
                    float(raw["turnover_inr"] or 0.0),
                )
            )
            barriers.append(raw.get("lookback_valid_from") or "")
    return {"dates": dates, "rows": rows, "barriers": barriers}


def load_panel(
    root: Path,
    *,
    symbols: list[str] | None = None,
    cache: bool = True,
) -> Panel:
    """Assemble the panel from the stage-2 adjusted series on disk.

    Reading 3,143 CSVs takes about a minute, so the assembled matrices are
    cached. The cache is keyed by nothing but the path: delete it after a
    rebuild of the adjusted series, which `cli.py panel --rebuild` does.
    """
    root = Path(root)
    cache_path = root / CACHE_NAME
    if cache and symbols is None and cache_path.exists():
        return _from_cache(cache_path, root)

    adjusted = root / "adjusted"
    paths = sorted(adjusted.glob("*.csv"))
    if symbols is not None:
        wanted = set(symbols)
        paths = [path for path in paths if path.stem in wanted]
    if not paths:
        raise FileNotFoundError(f"no adjusted series under {adjusted}")

    parsed = {path.stem: _read_symbol(path) for path in paths}
    all_dates = sorted({day for item in parsed.values() for day in item["dates"]})
    date_pos = {day: position for position, day in enumerate(all_dates)}
    names = sorted(parsed)
    days, count = len(all_dates), len(names)

    fields = {
        name: np.full((days, count), np.nan, dtype=np.float32)
        for name in ("open", "high", "low", "close", "volume", "turnover")
    }
    barrier_flag = np.zeros((days, count), dtype=bool)
    present = np.zeros((days, count), dtype=bool)

    for column, symbol in enumerate(names):
        item = parsed[symbol]
        positions = np.array([date_pos[day] for day in item["dates"]], dtype=np.int64)
        block = np.asarray(item["rows"], dtype=np.float32)
        for offset, name in enumerate(("open", "high", "low", "close", "volume", "turnover")):
            fields[name][positions, column] = block[:, offset]
        present[positions, column] = True
        marked = [
            date_pos[day]
            for day, barrier in zip(item["dates"], item["barriers"])
            if barrier
        ]
        if marked:
            barrier_flag[np.array(marked, dtype=np.int64), column] = True

    panel = build_panel(
        np.array(all_dates),
        np.array(names),
        {**fields, "barrier": barrier_flag, "present": present},
        _index_series(root, all_dates),
    )
    if cache and symbols is None:
        _to_cache(panel, cache_path)
    return panel


def build_panel(
    dates: np.ndarray,
    symbols: np.ndarray,
    fields: dict[str, np.ndarray],
    index_close: dict[str, np.ndarray],
) -> Panel:
    """Assemble a panel from filled matrices — the single construction path.

    `load_panel` and the tests both come through here, so a causality test on a
    hand-built panel exercises the same feature code that the study runs on.
    `fields` needs open/high/low/close/volume/turnover plus a boolean `barrier`;
    `present` defaults to wherever close is finite.
    """
    close = fields["close"]
    present = fields.get("present")
    if present is None:
        present = np.isfinite(close) & (close > 0)
    barrier_flag = fields.get("barrier")
    if barrier_flag is None:
        barrier_flag = np.zeros(close.shape, dtype=bool)
    core = {name: fields[name] for name in ("open", "high", "low", "close", "volume", "turnover")}

    bars_since = _bars_since_barrier(present, barrier_flag)
    features = _features(core, bars_since, present)
    eligible = _eligibility(core, present)
    features.update(_relative_strength(features, index_close, bars_since))
    return Panel(
        dates=dates,
        symbols=symbols,
        open=core["open"],
        high=core["high"],
        low=core["low"],
        close=core["close"],
        volume=core["volume"],
        turnover=core["turnover"],
        bars_since_barrier=bars_since,
        features=features,
        eligible=eligible,
        index_close=index_close,
    )


def _bars_since_barrier(present: np.ndarray, barrier: np.ndarray) -> np.ndarray:
    """Sessions of clean history behind each cell, reset to 0 at a barrier."""
    days, count = present.shape
    out = np.zeros((days, count), dtype=np.int32)
    running = np.zeros(count, dtype=np.int32)
    for position in range(days):
        running = np.where(present[position], running + 1, running)
        running = np.where(barrier[position], 0, running)
        out[position] = running
    return out


def _features(
    fields: dict[str, np.ndarray], bars_since: np.ndarray, present: np.ndarray
) -> dict[str, np.ndarray]:
    """Every feature, shifted so session ``t``'s own bar is never included."""
    close = fields["close"]
    high = fields["high"]
    low = fields["low"]
    volume = fields["volume"]

    out: dict[str, np.ndarray] = {}

    # Previous-session bar, as known at the decision point.
    out["prev_close"] = _shift_down(close)
    out["prev_high"] = _shift_down(high)
    out["prev_low"] = _shift_down(low)
    out["prev_open"] = _shift_down(fields["open"])
    out["prev2_high"] = _shift_down(high, 2)
    out["prev2_low"] = _shift_down(low, 2)
    out["prev2_close"] = _shift_down(close, 2)

    for lookback in RETURN_LOOKBACKS:
        base = _shift_down(close, lookback + 1)
        out[f"ret_{lookback}"] = np.where(base > 0, out["prev_close"] / base - 1.0, np.nan)

    for lookback in HIGH_LOOKBACKS:
        out[f"high_{lookback}"] = _shift_down(_rolling(high, lookback, _nanmax))
        out[f"low_{lookback}"] = _shift_down(_rolling(low, lookback, _nanmin))

    for lookback in MA_LOOKBACKS:
        out[f"ma_{lookback}"] = _shift_down(_rolling(close, lookback, _nanmean))

    true_range = np.fmax(
        high - low,
        np.fmax(np.abs(high - _shift_down(close)), np.abs(low - _shift_down(close))),
    )
    atr = _rolling(true_range, ATR_LOOKBACK, _nanmean)
    out["atr"] = _shift_down(atr)
    out["atr_slow"] = _shift_down(_rolling(true_range, ATR_LOOKBACK * 4, _nanmean))
    out["atr_pct"] = np.where(out["prev_close"] > 0, out["atr"] / out["prev_close"], np.nan)
    out["prev_range_atr"] = np.where(
        out["atr"] > 0, (out["prev_high"] - out["prev_low"]) / out["atr"], np.nan
    )

    returns = np.where(_shift_down(close) > 0, close / _shift_down(close) - 1.0, np.nan)
    out["vol_20"] = _shift_down(_rolling(returns, VOL_LOOKBACK, _nanstd))
    out["volume_mean"] = _shift_down(_rolling(volume, VOLUME_LOOKBACK, _nanmean), 2)
    out["volume_ratio"] = np.where(
        out["volume_mean"] > 0, _shift_down(volume) / out["volume_mean"], np.nan
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        out["voladj_20"] = np.where(out["vol_20"] > 0, out["ret_20"] / out["vol_20"], np.nan)
    out["dist_ma_20"] = np.where(out["ma_20"] > 0, out["prev_close"] / out["ma_20"] - 1.0, np.nan)
    out["dist_ma_50"] = np.where(out["ma_50"] > 0, out["prev_close"] / out["ma_50"] - 1.0, np.nan)
    out["range_pct_20"] = np.where(
        out["low_20"] > 0, out["high_20"] / out["low_20"] - 1.0, np.nan
    )
    out["atr_ratio"] = np.where(out["atr_slow"] > 0, out["atr"] / out["atr_slow"], np.nan)
    out["drawdown_from_high_20"] = np.where(
        out["high_20"] > 0, out["prev_close"] / out["high_20"] - 1.0, np.nan
    )

    # Barrier masking: a lookback may not cross an unresolved adjustment.
    requirement = {
        **{f"ret_{lookback}": lookback + 1 for lookback in RETURN_LOOKBACKS},
        **{f"high_{lookback}": lookback + 1 for lookback in HIGH_LOOKBACKS},
        **{f"low_{lookback}": lookback + 1 for lookback in HIGH_LOOKBACKS},
        **{f"ma_{lookback}": lookback + 1 for lookback in MA_LOOKBACKS},
        "atr": ATR_LOOKBACK + 1,
        "atr_slow": ATR_LOOKBACK * 4 + 1,
        "atr_pct": ATR_LOOKBACK + 1,
        "prev_range_atr": ATR_LOOKBACK + 1,
        "vol_20": VOL_LOOKBACK + 1,
        "volume_mean": VOLUME_LOOKBACK + 2,
        "volume_ratio": VOLUME_LOOKBACK + 2,
        "voladj_20": max(VOL_LOOKBACK, 20) + 1,
        "dist_ma_20": 21,
        "dist_ma_50": 51,
        "range_pct_20": 21,
        "atr_ratio": ATR_LOOKBACK * 4 + 1,
        "drawdown_from_high_20": 21,
        "prev_close": 1,
        "prev_high": 1,
        "prev_low": 1,
        "prev_open": 1,
        "prev2_high": 2,
        "prev2_low": 2,
        "prev2_close": 2,
    }
    for name, need in requirement.items():
        out[name] = np.where(bars_since >= need, out[name], np.nan)
    # A cell for a session the symbol did not trade carries no feature at all.
    for name in out:
        out[name] = np.where(present, out[name], np.nan)
    return out


def _eligibility(fields: dict[str, np.ndarray], present: np.ndarray) -> np.ndarray:
    """§12 liquidity screen, from strictly prior sessions, top-N by turnover."""
    turnover = fields["turnover"]
    close = fields["close"]
    median_turnover = _shift_down(_rolling(turnover, UNIVERSE_LOOKBACK_SESSIONS, _nanmedian))
    prior_close = _shift_down(close)
    prior_count = np.cumsum(present.astype(np.int32), axis=0) - present.astype(np.int32)

    passes = (
        present
        & (prior_count >= UNIVERSE_MIN_PRIOR_SESSIONS)
        & (prior_close >= UNIVERSE_MIN_PRICE_INR)
        & (median_turnover >= UNIVERSE_MIN_MEDIAN_TURNOVER_INR)
    )
    # Top N by the same prior median turnover, per session.
    scored = np.where(passes, median_turnover, -np.inf)
    ranked = np.argsort(-scored, axis=1, kind="stable")
    keep = np.zeros_like(passes)
    columns = ranked[:, :UNIVERSE_TOP_N_BY_TURNOVER]
    rows = np.arange(passes.shape[0])[:, None]
    keep[rows, columns] = True
    return passes & keep


def _index_series(root: Path, dates: list[str]) -> dict[str, np.ndarray]:
    """Benchmark closes aligned to the equity session axis, never forward-filled."""
    stored = IndexStore(root).read()
    out: dict[str, np.ndarray] = {}
    for name in (NIFTY_50, NIFTY_500):
        series = stored.get(name, {})
        out[name] = np.array(
            [series[day].close if day in series else np.nan for day in dates],
            dtype=np.float64,
        )
    return out


def _relative_strength(
    features: dict[str, np.ndarray],
    index_close: dict[str, np.ndarray],
    bars_since: np.ndarray,
) -> dict[str, np.ndarray]:
    """Stock return minus the broad index return over the same window (§5)."""
    broad = index_close.get(NIFTY_500)
    out: dict[str, np.ndarray] = {}
    if broad is None or not np.isfinite(broad).any():
        for lookback in RETURN_LOOKBACKS:
            out[f"rs_{lookback}"] = np.full_like(features["ret_20"], np.nan)
        return out
    column = broad.reshape(-1, 1)
    for lookback in RETURN_LOOKBACKS:
        prior = _shift_down(column)
        base = _shift_down(column, lookback + 1)
        index_return = np.where(base > 0, prior / base - 1.0, np.nan)
        out[f"rs_{lookback}"] = features[f"ret_{lookback}"] - index_return
    return out


def _to_cache(panel: Panel, path: Path) -> None:
    payload = {
        "dates": panel.dates,
        "symbols": panel.symbols,
        "open": panel.open,
        "high": panel.high,
        "low": panel.low,
        "close": panel.close,
        "volume": panel.volume,
        "turnover": panel.turnover,
        "bars_since_barrier": panel.bars_since_barrier,
        "eligible": panel.eligible,
    }
    for name, matrix in panel.features.items():
        payload[f"feat_{name}"] = matrix
    for name, series in panel.index_close.items():
        payload[f"index_{name.replace(' ', '_')}"] = series
    np.savez_compressed(path, **payload)


def _from_cache(path: Path, root: Path) -> Panel:
    with np.load(path, allow_pickle=False) as data:
        features = {
            key[len("feat_") :]: data[key] for key in data.files if key.startswith("feat_")
        }
        index_close = {
            key[len("index_") :].replace("_", " "): data[key]
            for key in data.files
            if key.startswith("index_")
        }
        return Panel(
            dates=data["dates"],
            symbols=data["symbols"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            volume=data["volume"],
            turnover=data["turnover"],
            bars_since_barrier=data["bars_since_barrier"],
            features=features,
            eligible=data["eligible"],
            index_close=index_close,
        )
