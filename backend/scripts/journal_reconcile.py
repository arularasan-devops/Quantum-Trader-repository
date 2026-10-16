"""Read-only journal reconciliation (brief §8). Run inside the backend container:
    docker compose exec -T backend python - < backend/scripts/journal_reconcile.py
Prints counts only; writes nothing."""
from app.journal.store import store

db = store().db
q, q1 = db.query, db.query_one


def show(title, rows):
    print(f"\n== {title}")
    for r in rows:
        print("  " + ", ".join(f"{k}={v}" for k, v in r.items()))


show("jr_trades by mode/status", q(
    "SELECT mode, status, COUNT(*) AS n, ROUND(CAST(SUM(COALESCE(gross_pnl,0)) AS NUMERIC),2) AS gross, "
    "ROUND(CAST(SUM(COALESCE(costs,0)) AS NUMERIC),2) AS costs, "
    "ROUND(CAST(SUM(COALESCE(net_pnl,0)) AS NUMERIC),2) AS net "
    "FROM jr_trades GROUP BY mode, status ORDER BY mode, status"))
show("closed trades: wins / losses / gross-vs-net sign flips", [q1(
    "SELECT COUNT(*) AS closed, SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS net_wins, "
    "SUM(CASE WHEN gross_pnl > 0 AND net_pnl <= 0 THEN 1 ELSE 0 END) AS gross_win_net_loss, "
    "SUM(CASE WHEN exit_price IS NULL THEN 1 ELSE 0 END) AS closed_without_exit_price, "
    "SUM(CASE WHEN mfe_mae_status IS NULL OR mfe_mae_status = '' THEN 1 ELSE 0 END) AS no_mfe_mae "
    "FROM jr_trades WHERE status = 'CLOSED'")])
show("duplicate trades (same option + entry second)", q(
    "SELECT option_symbol, entry_ts, COUNT(*) AS n FROM jr_trades "
    "GROUP BY option_symbol, entry_ts HAVING COUNT(*) > 1 ORDER BY n DESC LIMIT 20"))
show("trades without an ENTRY event", [q1(
    "SELECT COUNT(*) AS n FROM jr_trades t WHERE NOT EXISTS (SELECT 1 FROM jr_trade_events e "
    "WHERE e.trade_id = t.trade_id AND e.kind = 'ENTRY')")])
show("closed trades without an EXIT event", [q1(
    "SELECT COUNT(*) AS n FROM jr_trades t WHERE t.status = 'CLOSED' AND NOT EXISTS ("
    "SELECT 1 FROM jr_trade_events e WHERE e.trade_id = t.trade_id AND SUBSTR(e.kind, 1, 4) = 'EXIT')")])
show("closed by setup / regime", q(
    "SELECT COALESCE(setup,'-') AS setup, COALESCE(regime,'-') AS regime, COUNT(*) AS n, "
    "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins, ROUND(CAST(SUM(net_pnl) AS NUMERIC), 2) AS net "
    "FROM jr_trades WHERE status = 'CLOSED' GROUP BY setup, regime ORDER BY n DESC"))
show("event kinds", q("SELECT kind, COUNT(*) AS n FROM jr_trade_events GROUP BY kind ORDER BY n DESC"))
try:
    show("legacy trade_journal (pre-v2) rows", [q1("SELECT COUNT(*) AS n FROM trade_journal")])
except Exception as exc:
    print(f"\n== legacy trade_journal: not in this database ({type(exc).__name__})")
show("BUY signals by outcome", q(
    "SELECT outcome, COUNT(*) AS n FROM jr_signals WHERE signal = 'BUY' GROUP BY outcome ORDER BY n DESC"))


def pf_rows(sql):
    out = []
    for r in q(sql):
        gw, gl = float(r.pop("gross_win") or 0), float(r.pop("gross_loss") or 0)
        n, w = int(r["n"] or 0), int(r["wins"] or 0)
        r["win_rate_pct"] = round(w / n * 100, 1) if n else None
        r["profit_factor"] = round(gw / abs(gl), 2) if gl else None
        out.append(r)
    return out


_AGG = ("COUNT(*) AS n, SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins, "
        "ROUND(CAST(SUM(net_pnl) AS NUMERIC), 2) AS net, "
        "SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END) AS gross_win, "
        "SUM(CASE WHEN net_pnl < 0 THEN net_pnl ELSE 0 END) AS gross_loss")
show("§8 recompute — jr_trades closed, by strategy/setup/regime (win rate, PF on net P&L)", pf_rows(
    f"SELECT COALESCE(strategy,'-') AS strategy, COALESCE(setup,'-') AS setup, "
    f"COALESCE(regime,'-') AS regime, {_AGG} FROM jr_trades WHERE status = 'CLOSED' "
    "GROUP BY strategy, setup, regime ORDER BY n DESC"))
try:
    show("§8 recompute — legacy trade_journal, by mode/auto (no setup/regime column exists here)",
         pf_rows(f"SELECT mode, auto, {_AGG} FROM trade_journal GROUP BY mode, auto ORDER BY n DESC"))
except Exception as exc:
    print(f"\n== legacy trade_journal recompute skipped ({type(exc).__name__})")
