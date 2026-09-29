"""Re-verify the BUY path and the entry premium/stop after the engine fixes.

Checks the things the user asked for explicitly — "proper buy signal, proper entry
premium" — as invariants over live decisions on every registered instrument:

  * the recommended option is a real contract in the chain the engine priced
  * the quoted entry premium is EXACTLY that contract's premium (not the spot,
    not a stale value from a different strike)
  * the strike is the ATM/near-ATM one for the spot the decision actually used
  * stop < entry < T1 < T2 < T3, and the stop respects the premium-stop floor
  * an actionable BUY satisfies the reward/risk gate
  * a fresh Supertrend FLIP_UP selects a CALL and FLIP_DOWN selects a PUT

Read-only: it ticks the engine and inspects the result, it never trades.
"""
from app.config import settings
from app.market.instruments import REGISTRY, get_spec
from app.state import registry

failures: list[str] = []
checked = 0


def check(cond: bool, msg: str) -> None:
    global checked
    checked += 1
    if not cond:
        failures.append(msg)


for inst in REGISTRY:
    st = registry.get(inst)
    snap = st.tick()
    dec = snap.decision
    if dec is None:
        continue
    # Re-read the chain from the same provider the tick priced from.
    chain = st.provider.option_chain()
    sym = dec.recommended_option
    prem = dec.current_premium
    spot = snap.futures_price
    step = get_spec(inst).strike_step

    print(f"\n{inst}: {dec.signal} {sym} prem={prem} spot={spot} "
          f"stop={dec.stop_loss} t1={dec.target1} t2={dec.target2} t3={dec.target3}")

    if not sym or prem is None:
        continue

    # --- the premium must belong to the recommended contract -----------------
    if chain:
        q = next((x for x in chain if x.symbol == sym), None)
        check(q is not None, f"{inst}: {sym} is not in the chain the engine priced")
        if q is not None:
            check(abs(q.premium - prem) < 0.05,
                  f"{inst}: quoted premium {prem} != chain premium {q.premium} for {sym}")
            # Moneyness must be checked in DELTA, not in strike steps. The engine
            # deliberately targets a ~0.52-delta contract, and for a high-vol or
            # longer-dated instrument the 0.5-delta strike legitimately sits several
            # hundred points from spot (Black-Scholes forward + vol term). Counting
            # strike steps would flag correct GOLD/SILVER picks as wrong.
            if q.delta:
                check(0.35 <= abs(q.delta) <= 0.70,
                      f"{inst}: {sym} delta {q.delta} is outside the intraday "
                      f"0.35-0.70 band — too far OTM/ITM to be the ATM pick")
            # It must still be the best available delta fit, i.e. the liquidity term
            # must not drag the pick off the ATM target onto a high-OI far strike.
            same_side = [x for x in chain if x.option_type == q.option_type]
            if same_side and spot:
                best = min(same_side, key=lambda x: abs(abs(x.delta) - 0.52))
                check(abs(q.strike - best.strike) <= 2 * step,
                      f"{inst}: picked {q.strike} but the delta-target strike is "
                      f"{best.strike} — liquidity weighting distorted the selection")

    # --- the premium must be an OPTION premium, not the underlying -----------
    if spot:
        check(prem < spot,
              f"{inst}: entry premium {prem} >= spot {spot} — looks like the underlying")

    # --- level ordering ------------------------------------------------------
    if dec.stop_loss is not None:
        check(dec.stop_loss < prem,
              f"{inst}: stop {dec.stop_loss} is not below the entry premium {prem}")
        gap_pct = (prem - dec.stop_loss) / prem * 100.0
        check(gap_pct >= settings.min_stop_pct_of_premium - 0.51,
              f"{inst}: stop is only {gap_pct:.1f}% of premium, floor is "
              f"{settings.min_stop_pct_of_premium}%")
    tgts = [t for t in (dec.target1, dec.target2, dec.target3) if t is not None]
    check(all(t > prem for t in tgts), f"{inst}: a target is not above the entry premium")
    check(tgts == sorted(tgts), f"{inst}: targets are not in ascending order {tgts}")

    # --- reward/risk on an actionable BUY -----------------------------------
    if str(dec.signal).upper().endswith("BUY") and dec.stop_loss is not None and dec.target1:
        rr = (dec.target1 - prem) / max(1e-9, prem - dec.stop_loss)
        check(rr >= settings.min_reward_risk - 0.01,
              f"{inst}: actionable BUY with R:R {rr:.2f} below the "
              f"{settings.min_reward_risk} gate")
        print(f"   BUY R:R = {rr:.2f} (gate {settings.min_reward_risk})")

    # --- the Supertrend flip must pick the matching side --------------------
    flip = getattr(snap.indicators, "supertrend_flip", None)
    if flip in ("FLIP_UP", "FLIP_DOWN"):
        want = "CE" if flip == "FLIP_UP" else "PE"
        print(f"   fresh {flip} — engine option {sym}")
        if settings.supertrend_entry_enabled and dec.entry_trigger == "SUPERTREND":
            check(sym.endswith(want),
                  f"{inst}: {flip} should select a {want}, got {sym}")

print(f"\n{checked} invariants checked across {len(REGISTRY)} instruments")
if failures:
    print(f"\n{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL BUY-PATH / ENTRY-PREMIUM CHECKS PASSED")
