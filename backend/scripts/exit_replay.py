"""Read-only exit-stage replay over the persisted paper journal (brief §8).

Run inside the backend container:
    docker compose exec -T backend python - < backend/scripts/exit_replay.py

The journal stores entry, stop, T1/T2/T3, exit price/reason and every stop
change, but NOT the intratrade option-price path. So this report classifies
where each closed trade exited relative to its stages and what the stop
ladder did; it does not invent an MFE/MAE path. Writes nothing.
"""
from app.journal.store import store

db = store().db


def stage(r):
    e, x = r["entry_price"], r["exit_price"]
    t1, t2, t3 = r["target1"], r["target2"], r["target3"]
    if x is None or e is None:
        return "NO_EXIT_PRICE"
    if t3 and x >= t3:
        return "E_BEYOND_T3"
    if t2 and x >= t2:
        return "D_T2_T3"
    if t1 and x >= t1:
        return "C_T1_T2"
    return "A_PRE_T1_WIN" if x > e else "A_PRE_T1_LOSS"


rows = db.query(
    "SELECT t.*, (SELECT COUNT(*) FROM jr_trade_events v WHERE v.trade_id = t.trade_id "
    "AND v.kind = 'STOP_CHANGE') AS stop_changes FROM jr_trades t WHERE status = 'CLOSED' "
    "ORDER BY entry_ts")
print(f"closed trades: {len(rows)}  (intratrade price path: NOT persisted -> no MFE/MAE replay)")
groups: dict[tuple, list] = {}
for r in rows:
    groups.setdefault((r["setup"], r["regime"], stage(r)), []).append(r)
print(f"\n{'setup':16} {'regime':14} {'exit stage':15} {'n':>4} {'net':>10} {'avg':>8} "
      f"{'PF':>5} {'stops chg':>9} {'avg min':>7}")
for (su, rg, st), g in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
    net = [float(r["net_pnl"] or 0) for r in g]
    gw, gl = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    pf = f"{gw / gl:.2f}" if gl > 0 else "—"
    mins = [float(r["holding_minutes"] or 0) for r in g]
    print(f"{su or '-':16} {rg or '-':14} {st:15} {len(g):>4} {sum(net):>10.0f} "
          f"{sum(net) / len(g):>8.0f} {pf:>5} {sum(r['stop_changes'] for r in g):>9} "
          f"{sum(mins) / len(mins):>7.1f}")
by_reason: dict[str, list] = {}
for r in rows:
    by_reason.setdefault(r["exit_reason"] or "-", []).append(float(r["net_pnl"] or 0))
print("\nby exit reason:")
for k, v in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
    print(f"  {k:28} n={len(v):>4} net={sum(v):>10.0f} avg={sum(v) / len(v):>8.0f}")
print("\nLimitation: comparing the old vs staged trail needs the option price path per"
      " trade (not stored). Sample size above is the full evidence; no holdout exists yet.")
