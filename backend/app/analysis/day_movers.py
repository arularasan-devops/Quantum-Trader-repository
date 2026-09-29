"""Auto day-movers — pick the handful of instruments worth watching TODAY and
hold them for the session.

Two problems this solves, both from the journal:

* 66 instruments enabled means every one of them is scanned, so each name is
  visited less often and a BUY elsewhere is seen late.
* Choosing by hand each morning is guesswork, and the numbers that matter (how
  far the PREMIUM can travel net of costs) are not visible on a broker screen.

So the engine ranks the active universe on the same opportunity measure the
board and the bot already use, keeps the ones that clear the movement floor
(bounded to 5-10 — the size is decided from the data, not from a dial), and
then **holds that set for the rest of the IST day** rather than rotating: a set
that changes every few minutes is not concentration, and it means the bot is
always trading a name it has only just started watching.

Deliberate limits, so this cannot quietly do something surprising:

* It never writes the user's manual watchlist file. The saved list stays exactly
  as they left it, for reference and manual override; auto only changes which
  instruments are ACTIVE in memory (and its own state file).
* It refuses to pick until enough of the universe has actually been measured —
  right after a restart a cold instrument looks identical to a dead one.
* A held name is replaced only after it reads dead on several consecutive
  checks, and every change is logged with the numbers behind it.
* It is advisory about *what to watch*. It never places, sizes or exits a trade.
"""
from __future__ import annotations

import json
import os
import threading
import time as _time

from app.config import settings
from app.market.instruments import REGISTRY, set_universe

_IST_OFFSET = 19800  # +5:30

# A held name must read below the movement floor this many consecutive checks
# before it is swapped out. One quiet reading is a lull, not a dead instrument.
_DEAD_STRIKES = 3

# The count is clamped to this range: fewer than 5 is a single-instrument book
# (the journal's Crude concentration problem), more than 10 stops being
# concentration at all.
MIN_COUNT = 5
MAX_COUNT = 10


def clamp_count(count: int) -> int:
    return max(MIN_COUNT, min(MAX_COUNT, int(count)))


def ist_day(now: float) -> str:
    st = _time.gmtime(int(now) + _IST_OFFSET)
    return f"{st.tm_year:04d}-{st.tm_mon:02d}-{st.tm_mday:02d}"


def ist_minutes(now: float) -> int:
    st = _time.gmtime(int(now) + _IST_OFFSET)
    return st.tm_hour * 60 + st.tm_min


class DayMovers:
    """Today's held set, its provenance, and the change log."""

    def __init__(self) -> None:
        self.day: str = ""
        self.held: list[str] = []
        self.picked_at: float = 0.0
        self.pick_note: str = ""
        self.log: list[dict] = []
        self.strikes: dict[str, int] = {}
        self.last_check: float = 0.0
        self.last_skip: str = ""
        # The on/off switch is remembered too, not just the day's pick. Without
        # this it was in-memory only, so every restart silently reverted it to
        # OFF and it looked like a toggle that refuses to stay on.
        self.mode_enabled: bool | None = None
        self._loaded = False
        self._lock = threading.Lock()

    # --- persistence ------------------------------------------------------
    def _path(self) -> str:
        d = settings.data_dir
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "day_movers.json")

    def _save(self) -> None:
        payload = {
            "day": self.day,
            "held": self.held,
            "picked_at": self.picked_at,
            "pick_note": self.pick_note,
            "log": self.log[-60:],
            "strikes": self.strikes,
            "mode_enabled": self.mode_enabled,
        }
        try:
            tmp = self._path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self._path())
        except OSError:
            pass

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self._path(), encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(data, dict):
            return
        day = data.get("day")
        held = data.get("held")
        self.day = day if isinstance(day, str) else ""
        self.held = [s for s in held if isinstance(s, str) and s in REGISTRY] if isinstance(held, list) else []
        picked = data.get("picked_at")
        self.picked_at = float(picked) if isinstance(picked, (int, float)) else 0.0
        note = data.get("pick_note")
        self.pick_note = note if isinstance(note, str) else ""
        log = data.get("log")
        self.log = [r for r in log if isinstance(r, dict)] if isinstance(log, list) else []
        mode = data.get("mode_enabled")
        self.mode_enabled = mode if isinstance(mode, bool) else None
        strikes = data.get("strikes")
        self.strikes = (
            {k: int(v) for k, v in strikes.items() if isinstance(k, str) and isinstance(v, int)}
            if isinstance(strikes, dict)
            else {}
        )

    def _roll(self, now: float) -> None:
        """A held set belongs to one IST day; a new day starts unpicked."""
        self._load()
        today = ist_day(now)
        if self.day != today:
            self.day = today
            self.held = []
            self.picked_at = 0.0
            self.pick_note = ""
            self.strikes = {}
            self._save()

    def reset(self) -> None:
        """Drop today's pick (used by tests and by an explicit re-pick)."""
        with self._lock:
            self.day = ""
            self.held = []
            self.picked_at = 0.0
            self.pick_note = ""
            self.log = []
            self.strikes = {}
            self._loaded = True
            self._save()

    def remember_mode(self, enabled: bool) -> None:
        """Persist the on/off switch across restarts."""
        with self._lock:
            self._load()
            self.mode_enabled = bool(enabled)
            self._save()

    def restore_mode(self) -> bool | None:
        """The remembered switch state, or None if it was never set."""
        with self._lock:
            self._load()
            return self.mode_enabled

    # --- the decision -----------------------------------------------------
    def _note(self, now: float, action: str, detail: str, symbols: list[str]) -> None:
        self.log.append(
            {
                "ts": int(now),
                "day": self.day,
                "action": action,
                "detail": detail,
                "symbols": list(symbols),
            }
        )
        self.log = self.log[-60:]

    def apply_held(self) -> list[str]:
        """Re-apply today's held set to the active universe (used on restart)."""
        with self._lock:
            self._roll(_time.time())
            if not self.held:
                return []
            return set_universe(list(self.held))

    def evaluate(self, rows: list[dict], now: float | None = None) -> dict:
        """Pick or maintain today's set from ranked mover rows.

        ``rows`` are the measured opportunity rows (symbol / measured /
        net_move_pct / score), already sorted best-first. Returns a small dict
        describing what happened, so the caller can log or display it. Applies
        the universe change itself; never touches the manual watchlist file.
        """
        now = _time.time() if now is None else now
        with self._lock:
            self._roll(now)
            self.last_check = now
            # A closed exchange is not a quiet instrument. After 15:30 IST every
            # NSE/BSE premium is frozen, so counting those rows would drag coverage
            # down (blocking a pick) and read every held equity name as dead. Rows
            # without the flag are treated as open, so callers that do not supply
            # it behave exactly as before.
            rows = [r for r in rows if r.get("session_open", True)]
            measured = [r for r in rows if r.get("measured")]
            total = len(rows)
            coverage = len(measured) / total if total else 0.0
            min_cov = max(0.0, min(1.0, settings.day_movers_min_coverage / 100.0))

            # Not enough of the universe has reported yet: a cold instrument and
            # a dead one look the same, and picking now would lock in whichever
            # names happened to warm first.
            if coverage < min_cov or len(measured) < MIN_COUNT:
                self.last_skip = (
                    f"only {len(measured)} of {total} instruments measured "
                    f"({coverage * 100:.0f}%) — need {min_cov * 100:.0f}% and at least "
                    f"{MIN_COUNT} before locking the day's set"
                )
                self._save()
                return {"action": "waiting", "reason": self.last_skip, "held": list(self.held)}

            # The morning gap is the worst time to judge a day's character, and
            # it is where the journal's -Rs6,997 of opening trades came from.
            wait_min = max(0, settings.day_movers_pick_after_ist_min)
            if not self.held and ist_minutes(now) < wait_min:
                hh, mm = divmod(wait_min, 60)
                self.last_skip = (
                    f"holding off until {hh:02d}:{mm:02d} IST — the opening minutes "
                    "misprice which instruments are actually active today"
                )
                self._save()
                return {"action": "waiting", "reason": self.last_skip, "held": list(self.held)}

            self.last_skip = ""
            ranked = [r["symbol"] for r in measured]
            by_sym = {r["symbol"]: r for r in rows}
            # How many to hold is decided from the data, not from a dial: every
            # measured name clearing the movement floor is worth holding, within
            # the 5-10 bound (fewer is a one-instrument book — the journal's
            # Crude concentration problem; more stops being concentration and
            # costs warm-up requests and scan coverage).
            live = sum(
                1
                for r in measured
                if (r.get("net_move_pct") or 0.0) >= settings.opportunity_min_move_pct
            )
            count = clamp_count(live)

            # --- first pick of the day ---
            if not self.held:
                self.held = ranked[:count]
                self.picked_at = now
                best = ", ".join(
                    f"{s} {by_sym[s].get('net_move_pct'):.1f}%"
                    for s in self.held
                    if by_sym.get(s, {}).get("net_move_pct") is not None
                )
                self.pick_note = (
                    f"held {len(self.held)} of {live} names above the "
                    f"{settings.opportunity_min_move_pct:g}% net-move floor — {best}"
                )
                self._note(now, "picked", self.pick_note, self.held)
                self._save()
                applied = set_universe(list(self.held))
                return {
                    "action": "picked",
                    "reason": self.pick_note,
                    "held": list(self.held),
                    "applied": applied,
                }

            # --- maintain: only swap a name that reads DEAD repeatedly -------
            floor = settings.opportunity_min_move_pct
            dropped: list[str] = []
            for sym in list(self.held):
                row = by_sym.get(sym)
                if row is None or not row.get("measured"):
                    continue  # unmeasured is not evidence of being dead
                move = row.get("net_move_pct")
                if move is not None and move < floor:
                    self.strikes[sym] = self.strikes.get(sym, 0) + 1
                else:
                    self.strikes[sym] = 0
                if self.strikes.get(sym, 0) >= _DEAD_STRIKES:
                    dropped.append(sym)

            if not dropped or not settings.day_movers_replace_dead:
                self._save()
                return {"action": "held", "reason": "set unchanged", "held": list(self.held)}

            spare = [s for s in ranked if s not in self.held]
            swaps: list[str] = []
            for sym in dropped:
                if not spare:
                    break
                repl = spare.pop(0)
                self.held[self.held.index(sym)] = repl
                self.strikes.pop(sym, None)
                self.strikes[repl] = 0
                swaps.append(f"{sym}→{repl}")
            if not swaps:
                self._save()
                return {
                    "action": "held",
                    "reason": f"{', '.join(dropped)} read dead but nothing better is measured",
                    "held": list(self.held),
                }
            detail = (
                f"{', '.join(swaps)} — replaced after {_DEAD_STRIKES} consecutive readings "
                f"below the {floor:g}% net-move floor"
            )
            self._note(now, "swapped", detail, self.held)
            self._save()
            applied = set_universe(list(self.held))
            return {"action": "swapped", "reason": detail, "held": list(self.held), "applied": applied}

    def snapshot(self, now: float | None = None) -> dict:
        now = _time.time() if now is None else now
        self._roll(now)
        return {
            "enabled": settings.day_movers_auto_enabled,
            # The size auto arrived at today (0 before the first pick). Reported,
            # not configured: the user asked not to have to choose a number.
            "count": len(self.held),
            "min_count": MIN_COUNT,
            "max_count": MAX_COUNT,
            "day": self.day,
            "held": list(self.held),
            "held_names": [REGISTRY[s].display for s in self.held if s in REGISTRY],
            "picked_at": int(self.picked_at) if self.picked_at else None,
            "picked_minutes_ago": int((now - self.picked_at) / 60) if self.picked_at else None,
            "pick_note": self.pick_note,
            "waiting_reason": self.last_skip or None,
            "last_check": int(self.last_check) if self.last_check else None,
            "pick_after_ist_min": settings.day_movers_pick_after_ist_min,
            "min_coverage_pct": settings.day_movers_min_coverage,
            "replace_dead": settings.day_movers_replace_dead,
            "dead_strikes_needed": _DEAD_STRIKES,
            "strikes": dict(self.strikes),
            "log": list(reversed(self.log[-20:])),
        }


movers = DayMovers()
