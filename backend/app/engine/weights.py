"""Self-learning vote weights.

The decision engine casts a weighted vote per factor (EMA Trend, VWAP, Chart
Pattern, News, …). This module keeps a *learned multiplier* per factor that is
nudged from real closed-trade outcomes: factors that were aligned with winning
trades gain weight, factors aligned with losing trades lose weight. Multipliers
are clamped to a sane band so one bad streak can't zero out a factor, and are
persisted to JSON so learning survives restarts.

This is deliberately simple and transparent — it re-tunes emphasis, it does not
invent new signals or place trades.
"""
from __future__ import annotations

import json
import os
import threading

from app.config import settings

_MIN_MULT = 0.5
_MAX_MULT = 1.6
_STEP = 0.04  # per-trade nudge


class WeightStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self.multipliers: dict[str, float] = {}
        self.stats: dict[str, dict[str, float]] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.multipliers = {k: float(v) for k, v in data.get("multipliers", {}).items()}
            self.stats = {
                k: {kk: float(vv) for kk, vv in v.items()}
                for k, v in data.get("stats", {}).items()
            }
        except (OSError, ValueError, KeyError):
            self.multipliers = {}
            self.stats = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp = f"{self._path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"multipliers": self.multipliers, "stats": self.stats}, fh, indent=2)
        os.replace(tmp, self._path)

    def multiplier(self, name: str) -> float:
        """The multiplier actually APPLIED to the live vote weight.

        Governance (per the "don't let the AI rewrite its own rules" principle):
          * advisory mode (default) -> always 1.0. Learning still records stats
            and computes recommendations, but the core rules stay fixed.
          * auto mode -> apply the learned multiplier, but ONLY once the factor
            has a statistically meaningful sample (>= learning_min_trades);
            otherwise 1.0. This prevents overfitting to a handful of trades.
        """
        if settings.learning_mode != "auto":
            return 1.0
        s = self.stats.get(name)
        trades = (s["wins"] + s["losses"]) if s else 0
        if trades < settings.learning_min_trades:
            return 1.0
        return self.multipliers.get(name, 1.0)

    def recommendations(self) -> list[dict]:
        """Suggested weight adjustments the user can review before enabling
        `auto`. Only surfaces factors with enough evidence to matter."""
        out = []
        for name, s in self.stats.items():
            trades = int(s["wins"] + s["losses"])
            mult = self.multipliers.get(name, 1.0)
            if trades < settings.learning_min_trades or abs(mult - 1.0) < 0.05:
                continue
            out.append({
                "name": name,
                "suggested_multiplier": round(mult, 3),
                "trades": trades,
                "win_rate": round(100.0 * s["wins"] / trades, 1) if trades else 0.0,
                "direction": "increase" if mult > 1.0 else "decrease",
                "applied": settings.learning_mode == "auto",
            })
        out.sort(key=lambda d: abs(d["suggested_multiplier"] - 1.0), reverse=True)
        return out

    def record_trade(self, factors: list[str], net_pnl: float) -> None:
        """Attribute a closed trade's outcome to the factors that drove entry."""
        if not settings.learning_enabled or not factors:
            return
        win = net_pnl >= 0
        with self._lock:
            for name in factors:
                s = self.stats.setdefault(name, {"wins": 0.0, "losses": 0.0, "pnl": 0.0})
                s["pnl"] += net_pnl
                if win:
                    s["wins"] += 1
                else:
                    s["losses"] += 1
                cur = self.multipliers.get(name, 1.0)
                cur += _STEP if win else -_STEP
                self.multipliers[name] = max(_MIN_MULT, min(_MAX_MULT, cur))
            self._save()

    def summary(self) -> list[dict]:
        out = []
        for name, s in self.stats.items():
            total = s["wins"] + s["losses"]
            out.append(
                {
                    "name": name,
                    "multiplier": round(self.multipliers.get(name, 1.0), 3),
                    "trades": int(total),
                    "win_rate": round(100.0 * s["wins"] / total, 1) if total else 0.0,
                    "pnl": round(s["pnl"], 1),
                }
            )
        out.sort(key=lambda d: d["multiplier"], reverse=True)
        return out


store = WeightStore(os.path.join(settings.data_dir, "weights.json"))
