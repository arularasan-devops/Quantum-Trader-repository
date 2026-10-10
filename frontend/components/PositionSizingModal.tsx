"use client";
import { useState } from "react";
import { Snapshot } from "@/lib/types";

/**
 * Display-only position-sizing calculator. It reads the FROZEN engine's
 * recommended option (premium / stop / targets) and the risk config (capital,
 * risk %, lot size, brokerage) and shows how many lots to buy, the money
 * committed, the worst-case loss, R:R, break-even and estimated charges.
 * It changes nothing in the engine and places no orders.
 */
export default function PositionSizingModal({ snap, onClose }: { snap: Snapshot; onClose: () => void }) {
  const d = snap.decision;
  const risk = snap.risk;

  const premium = d.current_premium ?? snap.position.entry_premium ?? 0;
  const lotSize = risk?.lot_size ?? 1;
  const capital = risk?.capital ?? 0;
  const riskPct = risk?.risk_per_trade_pct ?? 0;
  const brokeragePerLot = risk?.brokerage_per_lot ?? 0;
  const suggested = Math.max(1, risk?.suggested_lots ?? 1);
  const stopPrem = d.stop_loss ?? premium * 0.9;

  const costPerLot = premium * lotSize;
  const maxLotsByCapital = costPerLot > 0 ? Math.floor(capital / costPerLot) : 0;

  const [lots, setLots] = useState(suggested);
  const n = Math.max(1, lots || 1);

  const capitalUsed = costPerLot * n;
  const riskPerUnit = Math.max(0, premium - stopPrem);
  const maxLossTrade = riskPerUnit * lotSize * n;

  // Estimated statutory charges for Indian index/commodity options (round-trip).
  // Standard rates — clearly an ESTIMATE; actual varies by broker/exchange.
  const buyTurn = premium * lotSize * n;
  const sellTurn = premium * lotSize * n; // assume exit near entry for the estimate
  const brokerage = brokeragePerLot * n * 2;
  const stt = 0.000625 * sellTurn;                 // STT 0.0625% on sell premium
  const exch = 0.00035 * (buyTurn + sellTurn);     // ~0.035% exchange txn
  const sebi = 0.000001 * (buyTurn + sellTurn);    // SEBI turnover fee
  const stamp = 0.00003 * buyTurn;                 // 0.003% stamp on buy
  const gst = 0.18 * (brokerage + exch + sebi);    // 18% GST
  const estCharges = brokerage + stt + exch + sebi + stamp + gst;

  const maxLossWithCharges = maxLossTrade + estCharges;
  const riskOfCapitalPct = capital > 0 ? (maxLossWithCharges / capital) * 100 : 0;

  const reward1 = (d.target1 ?? premium) - premium;
  const rr = riskPerUnit > 0 ? reward1 / riskPerUnit : 0;

  const beOptionPrem = premium + (n > 0 && lotSize > 0 ? estCharges / (lotSize * n) : 0);
  const strike = d.strike;
  const isCall = d.option_type === "CE";
  const underlyingBE =
    strike != null ? (isCall ? strike + premium : strike - premium) : null;

  const inr = (v: number) => "₹" + Math.round(v).toLocaleString("en-IN");
  const inr2 = (v: number) => "₹" + v.toLocaleString("en-IN", { maximumFractionDigits: 2 });

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-[#0d1117] border border-white/15 rounded-2xl w-full max-w-4xl max-h-[92vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10 sticky top-0 bg-[#0d1117]">
          <div>
            <div className="text-lg font-bold text-white">Position Size Calculator</div>
            <div className="text-xs text-white/45">
              {d.recommended_option || "No option recommended yet"}
              {d.option_type ? ` · ${d.option_type}` : ""}
              {strike != null ? ` · Strike ${strike}` : ""}
            </div>
          </div>
          <button onClick={onClose} className="text-white/50 hover:text-white text-2xl leading-none px-2">×</button>
        </div>

        <div className="p-6 space-y-5">
          {/* lots selector */}
          <div className="flex items-center gap-4 flex-wrap">
            <div className="text-sm text-white/70">Lots to buy</div>
            <div className="flex items-center gap-2">
              <button onClick={() => setLots((l) => Math.max(1, l - 1))} className="w-9 h-9 rounded-lg border border-white/15 text-white text-xl hover:bg-white/5">−</button>
              <input
                type="number"
                min={1}
                value={n}
                onChange={(e) => setLots(Math.max(1, parseInt(e.target.value || "1", 10)))}
                className="w-20 text-center bg-white/5 border border-white/15 rounded-lg py-2 text-white text-lg font-bold"
              />
              <button onClick={() => setLots((l) => l + 1)} className="w-9 h-9 rounded-lg border border-white/15 text-white text-xl hover:bg-white/5">+</button>
            </div>
            <button
              onClick={() => setLots(suggested)}
              className="text-xs px-3 py-1.5 rounded-lg border border-[#19e57f]/40 text-[#19e57f] hover:bg-[#19e57f]/10"
            >
              Use recommended ({suggested})
            </button>
            <div className="text-xs text-white/45">
              Max by capital: <span className="text-white/80 font-semibold">{maxLotsByCapital}</span> lots · {n * lotSize} qty
            </div>
          </div>

          {/* headline numbers */}
          <div className="grid grid-cols-3 gap-3">
            <Big label="Capital used" value={inr(capitalUsed)} sub={`${capital > 0 ? Math.round((capitalUsed / capital) * 100) : 0}% of ${inr(capital)}`} color="#3ea6ff" />
            <Big label="Max loss (at SL)" value={inr(maxLossWithCharges)} sub={`incl. est. charges`} color="#ff4d4d" />
            <Big label="Risk of capital" value={riskOfCapitalPct.toFixed(2) + "%"} sub={`limit ${riskPct}%`} color={riskOfCapitalPct > riskPct ? "#ff4d4d" : "#19e57f"} />
          </div>

          {/* detail grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Capital available" value={inr(capital)} />
            <Stat label="Option premium" value={inr2(premium)} />
            <Stat label="Lot size" value={String(lotSize)} />
            <Stat label="Cost per lot" value={inr(costPerLot)} />
            <Stat label="Recommended lots" value={String(suggested)} />
            <Stat label="Total quantity" value={String(n * lotSize)} />
            <Stat label="Stop-loss (premium)" value={inr2(stopPrem)} />
            <Stat label="Risk / unit" value={inr2(riskPerUnit)} />
            <Stat label="Target 1" value={d.target1 != null ? inr2(d.target1) : "—"} />
            <Stat label="Target 2" value={d.target2 != null ? inr2(d.target2) : "—"} />
            <Stat label="Target 3" value={d.target3 != null ? inr2(d.target3) : "—"} />
            <Stat label="Risk : Reward (T1)" value={rr > 0 ? `1 : ${rr.toFixed(2)}` : "—"} />
            <Stat label="Break-even premium" value={inr2(beOptionPrem)} />
            <Stat label="Underlying break-even" value={underlyingBE != null ? inr2(underlyingBE) : "—"} />
            <Stat label="Brokerage (round-trip)" value={inr2(brokerage)} />
            <Stat label="Est. charges (incl. taxes)" value={inr2(estCharges)} />
          </div>

          {!risk && (
            <div className="text-xs text-amber-400/90">
              Risk config not available in this snapshot — showing option levels only.
            </div>
          )}
          <div className="text-[11px] text-white/40 leading-relaxed">
            Charges are an <span className="text-white/60">estimate</span> using standard Indian F&O rates
            (STT 0.0625% on sell, exchange txn ~0.035%, SEBI, stamp 0.003% on buy, 18% GST) and assume exit
            near entry — actual figures depend on your broker and fill prices. This calculator is display-only:
            it changes nothing in the decision engine and places no orders.
          </div>
        </div>
      </div>
    </div>
  );
}

function Big({ label, value, sub, color }: { label: string; value: string; sub?: string; color: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="text-xs text-white/50">{label}</div>
      <div className="text-2xl font-extrabold mt-1" style={{ color }}>{value}</div>
      {sub && <div className="text-[11px] text-white/40 mt-0.5">{sub}</div>}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/8 bg-white/[0.02] px-3 py-2">
      <div className="text-[11px] text-white/45">{label}</div>
      <div className="text-sm font-semibold text-white mt-0.5">{value}</div>
    </div>
  );
}
