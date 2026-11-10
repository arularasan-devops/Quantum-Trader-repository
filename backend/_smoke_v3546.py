"""v3.5.46: futures history refreshes are round-robin per Angel key, and the
production engine reads REST history extended by live tick bars."""
import os
os.environ.setdefault("QT_TRADE_MODE", "paper")
from app.market import angelone as a

fails = 0
def ok(c, m):
    global fails
    print(("PASS " if c else "FAIL ") + m)
    fails += 0 if c else 1

L = a._HistLimiter()
t = 1000.0
for k in (1, 2, 3):
    L.fut_turn(k, t)
L.fut_called(1, t); L.fut_called(2, t + 1)
ok(L.fut_turn(3, t + 2), "never-refreshed instrument goes first")
ok(not L.fut_turn(1, t + 2) and not L.fut_turn(2, t + 2), "others wait their turn")
L.fut_called(3, t + 3)
ok(L.fut_turn(1, t + 4) and not L.fut_turn(2, t + 4), "then the oldest call goes next")
ok(L.fut_turn(2, t + 4 + a._HIST_QUEUE_ACTIVE_SEC + 1), "an instrument that stops asking drops out of the queue")
L2 = a._HistLimiter()
L2.fut_turn(1, t); L2.fut_called(1, t)
ok(L2.fut_backlog(t + 10, a._HIST_OPTION_YIELD_SEC) == 0, "fresh futures: option charts may refresh")
L2.fut_turn(1, t + a._HIST_OPTION_YIELD_SEC + 5)
ok(L2.fut_backlog(t + a._HIST_OPTION_YIELD_SEC + 5, a._HIST_OPTION_YIELD_SEC) == 1,
   "stale futures: option charts yield the budget")

src = open("app/state.py").read()
ok("candles = self.provider.futures_candles_live(300) or self.provider.futures_candles(300)" in src,
   "engine reads REST + live tick bars")
print("ALL PASS" if not fails else f"{fails} FAILED")
