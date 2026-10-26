"""A stand-aside (AVOID/WAIT) call shows this tick's strike, even if it was
committed before the option chain loaded; a held BUY plan keeps its own leg."""
import time
from app.models import Signal, OptionType
from app.state import AppState

fails = []
def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name); ok or fails.append(name)

st = AppState("CRUDEOIL")
raw = st.tick().decision
now = time.time()
c = raw.model_copy(deep=True); c.signal = Signal.AVOID; c.strike = None; c.option_type = None
st._committed = c; st._committed_at = now
r = raw.model_copy(deep=True); r.signal = Signal.AVOID; r.strike = 9200.0; r.option_type = OptionType.PUT
out = st._stabilize(r, False, now)
check("AVOID committed before the chain loaded now shows the strike", out.strike == 9200.0 and out.option_type == OptionType.PUT)
r2 = r.model_copy(deep=True); r2.strike = 9300.0
check("AVOID follows the current pick", st._stabilize(r2, False, now).strike == 9300.0)
r3 = r.model_copy(deep=True); r3.strike = 9650.0; r3.option_type = OptionType.CALL
r3.recommended_option = "CRUDEOIL17NOV269650CE"; r3.current_premium = 532.1
st._committed.recommended_option = "CRUDEOIL17NOV269200CE"; st._committed.current_premium = 759.0
o3 = st._stabilize(r3, False, now)
check("AVOID label and price name the same contract",
      o3.strike == 9650.0 and o3.recommended_option == "CRUDEOIL17NOV269650CE" and o3.current_premium == 532.1)
b = raw.model_copy(deep=True); b.signal = Signal.BUY; b.strike = 9100.0; b.option_type = OptionType.CALL
st._committed = b; st._committed_at = now
rb = b.model_copy(deep=True); rb.strike = 9400.0
check("held BUY keeps its committed strike", st._stabilize(rb, False, now).strike == 9100.0)
print("ALL AVOID-STRIKE CHECKS PASSED" if not fails else f"FAILED: {fails}")
