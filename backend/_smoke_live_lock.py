"""Smoke: the build's real-money lock refuses a live order cleanly. It used to
raise out of the tick, leave a SUBMITTING record, and stop the price loop."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="qt-smoke-lock-"))
from app.state import AppState
from app.market.angelone import AngelOneProvider


class _Self:
    def __init__(self):
        self._pending_order, self.events, self.alerts = None, [], []
        self.provider = AngelOneProvider.__new__(AngelOneProvider)

    def _persist_state(self):
        return True

    def _event(self, kind, pend=None, st=None, **kw):
        self.events.append(kind)
        return True

    def _add_alert(self, kind, msg, level):
        self.alerts.append(kind)


me = _Self()
res = AppState._submit_live(me, "_pending_order", "ABB27OCT266300CE", "BUY", 3)
ok = (res.ok is False and me._pending_order is None and "SUBMIT_REFUSED_LOCKED" in me.events
      and "LIVE_ORDERS_LOCKED" in me.alerts and "disabled" in res.message)
print("PASS" if ok else "FAIL", " locked live order refused cleanly, nothing pending —",
      res.message, me.events, me.alerts)
sys.exit(0 if ok else 1)
